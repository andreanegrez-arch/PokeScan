"""Make the fingerprints of editions' illustrations (see app/recognition/artwork.py).

For each card of an edition in the catalog (Pokémon, Trainers, Energies):
download its English picture from pokemontcg.io into a temporary folder,
fingerprint the artwork box, and delete the picture. Only the fingerprints
are kept, in backend/fingerprints/<set_id>.sqlite, with the card's printings
in every language the catalog has a name for it (TCGdex lists: names as
printed in that language, never translated here).

A card whose illustration another card already has (same English name, same
layout, the two pictures in clear agreement) is a reprint: its printing links
to that illustration instead of keeping one more. Editions are built in
release order, so the first printing keeps the illustration; build an edition
after those it reprints from.

--check N: once built, N cards per edition are turned into phone-like photos
(perspective, light, blur, noise, glare, JPEG) and recognized against every
edition's fingerprints, with the set size known and without: a test on
pictures, not on real photos.

Run from the backend folder, after the catalog import:
    python -m jobs.build_fingerprints base1 base2 --check 12
"""

import argparse
import datetime as dt
import logging
import re
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import httpx
import numpy as np
import psycopg

from app.db import CONNECTION_HINT, connect
from app.recognition import artwork, detect
from jobs.evaluate_scan import make_photo

log = logging.getLogger("build_fingerprints")

SOURCE = "pokemontcg.io"
PICTURE_URL = "https://images.pokemontcg.io/{set_id}/{number}_hires.png"
# TCGdex set ids that pokemontcg.io names otherwise.
SOURCE_SET_IDS = {"lc": "base6"}
LANGS = ("en", "it")
WORKERS = 4
RETRIES = 4
RETRY_WAIT = 10  # seconds, times the attempt

# A reprint links to the illustration only when the two pictures are nearly
# alike. Two illustrations of the same Pokémon never agree on more than 4
# keypoints (343 pairs, Wizards era); one illustration printed twice agrees on
# 7 to 358, low between a holo and its non-holo. Measured on simulated photos
# of such pairs: up to 80, separate fingerprints recognize the very printing
# more often than one shared fingerprint recognizes the illustration (and
# never the other one); above, the shared fingerprint does better.
REPRINT_INLIERS = 80

# Plain tables (BGR) under the --check photos: the check is about the
# fingerprints, not about finding the card on a busy background.
TABLES = [(60, 90, 60), (40, 60, 90), (90, 90, 90), (35, 35, 45), (150, 160, 170)]

CARDS = """
SELECT c.card_id, c.set_id, c.local_number, c.category, c.rarity, s.series_id, s.official_count,
       s.release_date, t.lang, t.name
FROM cards c
JOIN sets s ON s.set_id = c.set_id
JOIN card_translations t ON t.card_id = c.card_id AND t.lang = ANY(%(langs)s)
WHERE c.set_id = ANY(%(sets)s)
ORDER BY s.release_date, c.set_id, c.card_id, t.lang
"""


@dataclass
class Card:
    card_id: str
    set_id: str
    number: str
    total: str | None  # as printed after the slash
    layout: str | None
    names: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        number = re.sub(r"^([A-Z]+)0+(\d)", r"\1\2", self.number)  # 'H01' is 'H1' there
        return PICTURE_URL.format(set_id=SOURCE_SET_IDS.get(self.set_id, self.set_id),
                                  number=number)


@dataclass
class Report:
    set_id: str
    cards: int = 0
    kept: int = 0  # illustrations kept in the file
    linked: dict[str, list[str]] = field(default_factory=dict)  # edition -> card ids
    missing: list[str] = field(default_factory=list)  # no picture
    no_layout: list[str] = field(default_factory=list)
    names: dict[str, int] = field(default_factory=dict)  # lang -> printings
    downloaded: int = 0  # bytes
    size: int = 0  # bytes of the file
    checks: dict[str, list[int]] = field(default_factory=dict)  # mode -> right, wrong, none


def printed_total(number: str, official_count: int | None, numbers: list[str]) -> str | None:
    """What is printed after the slash: the set size; for a lettered subset
    ('H01' in Aquapolis), the letter and the subset's size ('H32'); nothing
    for a number without digits (Triumphant's 'FOUR')."""
    lettered = re.fullmatch(r"([A-Z]+)\d+", number)
    if lettered:
        prefix = lettered.group(1)
        return f"{prefix}{sum(bool(re.fullmatch(prefix + r'\d+', n)) for n in numbers)}"
    if not re.search(r"\d", number) or official_count is None:
        return None
    return str(official_count)


def _order(card: Card) -> tuple:
    """Numbers in the set first (1, 2, ... 150), then lettered ones (H1 ...)."""
    digits = re.search(r"\d+", card.number)
    return (not card.number[:1].isdigit(), int(digits.group()) if digits else 0, card.number)


def editions(conn: psycopg.Connection, set_ids: list[str]) -> dict[str, list[Card]]:
    """The cards of these editions, editions in release order."""
    rows = conn.execute(CARDS, {"sets": set_ids, "langs": list(LANGS)}).fetchall()
    found: dict[str, dict[str, Card]] = {}
    raw: dict[str, tuple] = {}
    for card_id, set_id, number, category, rarity, series_id, official, _, lang, name in rows:
        cards = found.setdefault(set_id, {})
        if card_id not in cards:
            cards[card_id] = Card(card_id, set_id, number, None,
                                  artwork.layout_for(series_id, category, rarity))
            raw[card_id] = official
        cards[card_id].names[lang] = name
    for cards in found.values():
        numbers = [c.number for c in cards.values()]
        for card in cards.values():
            card.total = printed_total(card.number, raw[card.card_id], numbers)
    return {set_id: sorted(cards.values(), key=_order) for set_id, cards in found.items()}


def _download(client: httpx.Client, url: str, folder: Path) -> Path | None:
    """The picture saved in `folder`; None if the source has none. A dropped
    connection or a server error is tried again, a few times."""
    for attempt in range(1, RETRIES + 1):
        try:
            response = client.get(url)
            if response.status_code == 404:
                log.warning("No picture at %s", url)
                return None
            response.raise_for_status()
            break
        except httpx.HTTPError as error:
            if attempt == RETRIES:
                log.warning("No picture from %s: %s", url, error)
                return None
            time.sleep(RETRY_WAIT * attempt)
    file = folder / re.sub(r"[^\w.-]", "_", url.split("//", 1)[1])
    file.write_bytes(response.content)
    return file


def _english(art: artwork.Artwork) -> set[str]:
    return {p.name.casefold() for p in art.printings if p.lang == "en"}


def _first_printing(card: Card, fingerprint: artwork.Fingerprint,
                    known: list[artwork.Artwork]) -> tuple[artwork.Artwork | None, int]:
    """The illustration this card reprints, if one is clearly the same; and
    the best agreement found."""
    name = card.names.get("en", "").casefold()
    scored = sorted(((artwork.agreement(fingerprint, art.fingerprint), art) for art in known
                     if art.layout == card.layout and name in _english(art)),
                    key=lambda s: s[0], reverse=True)
    if not scored:
        return None, 0
    best, art = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if best >= REPRINT_INLIERS and best >= artwork.MIN_MARGIN * runner_up:
        return art, best
    return None, best


def glare(card: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Light reflected off part of the card: a bright, soft patch."""
    height, width = card.shape[:2]
    x, y = rng.uniform(0.1, 0.9) * width, rng.uniform(0.1, 0.6) * height
    radius = rng.uniform(0.12, 0.3) * width
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    patch = np.exp(-((xs - x) ** 2 + ((ys - y) / 1.4) ** 2) / (2 * radius**2))
    lit = card.astype(np.float32) + patch[..., None] * rng.uniform(90, 170)
    return np.clip(lit, 0, 255).astype(np.uint8)


def build(cards: list[Card], out: Path, client: httpx.Client, folder: Path,
          known: list[artwork.Artwork], samples: int,
          tests: list[tuple[Card, bytes]]) -> Report:
    """Fingerprint one edition; reprints link to the illustrations in `known`
    (which this edition's own are added to). Photos for --check go in `tests`."""
    set_id = cards[0].set_id
    report = Report(set_id, cards=len(cards))
    report.no_layout = [c.card_id for c in cards if c.layout is None]
    usable = [c for c in cards if c.layout is not None]
    with ThreadPoolExecutor(WORKERS) as pool:
        files = list(pool.map(lambda c: _download(client, c.url, folder), usable))

    rng = np.random.default_rng(sum(map(ord, set_id)))
    tested = set(rng.choice(len(usable), min(samples, len(usable)), replace=False).tolist())
    artworks: list[artwork.Artwork] = []
    printings: list[tuple[str, artwork.Printing]] = []
    for index, (card, file) in enumerate(zip(usable, files)):
        picture = None
        if file is not None:
            report.downloaded += file.stat().st_size
            picture = detect.as_card(detect.decode(file.read_bytes()))
            file.unlink()
        if picture is None:
            report.missing.append(card.card_id)
            continue
        fingerprint = artwork.fingerprint(picture, card.layout)
        first, inliers = _first_printing(card, fingerprint, known)
        if first is not None:
            key = first.key
            source_set = key.split("/")[0]
            report.linked.setdefault(source_set, []).append(card.card_id)
            log.info("%s reprints %s (%d keypoints agree)", card.card_id, key, inliers)
        else:
            if inliers >= artwork.MIN_INLIERS:
                log.info("%s: like an illustration of the same name (%d keypoints agree),"
                            " kept as its own", card.card_id, inliers)
            key = artwork.artwork_key(set_id, card.number)
            art = artwork.Artwork(key, card.layout, card.url, fingerprint)
            artworks.append(art)
            known.append(art)
            first = art
        for lang, name in sorted(card.names.items()):
            printing = artwork.Printing(lang, set_id, card.total, card.number, card.card_id, name)
            printings.append((key, printing))
            first.printings.append(printing)
            report.names[lang] = report.names.get(lang, 0) + 1
        if index in tested:
            table = TABLES[int(rng.integers(len(TABLES)))]
            tests.append((card, make_photo(glare(picture, rng), rng, turn=0,
                                           background=table)))

    path = out / f"{set_id}.sqlite"
    official = next((c.total for c in cards if c.total and c.total.isdigit()), "")
    meta = {"set_id": set_id, "official_count": official, "source": SOURCE,
            "features": str(artwork.FEATURES),
            "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    artwork.write_edition(path, meta, artworks, printings)
    report.kept, report.size = len(artworks), path.stat().st_size
    log.info("%s: %d cards, %d illustrations kept, %d reprints, %d without picture;"
             " %.1f MB downloaded (deleted); %s %.0f kB", set_id, report.cards, report.kept,
             sum(map(len, report.linked.values())), len(report.missing),
             report.downloaded / 1e6, path.name, report.size / 1e3)
    return report


def _views(photo: np.ndarray):
    """The card straightened as a scan does: from its outline, from the outline
    enlarged (an inner frame taken for it), then the photo taken as the card."""
    corners = detect.find_outline(photo)
    if corners is not None:
        yield detect.straighten(photo, corners)
        yield detect.straighten(photo, detect.enlarge(corners))
    card = detect.as_card(photo)
    if card is not None:
        yield card


def check(tests: list[tuple[Card, bytes]], library: list[artwork.Artwork],
          reports: dict[str, Report]) -> None:
    """Recognize the test photos: with the set size known, then anywhere."""
    def keep_size(total: str | None) -> Callable[[artwork.Printing], bool]:
        return lambda p: artwork.same_number(p.set_total, total)

    for card, jpeg in tests:
        views = list(_views(detect.decode(jpeg)))
        modes = {"size known": (keep_size(card.total), artwork.MIN_INLIERS),
                 "anywhere": (lambda p: True, artwork.MIN_INLIERS_ANYWHERE)}
        for mode, (keep, minimum) in modes.items():
            if mode == "size known" and card.total is None:
                continue
            match = next((m for view in views
                          if (m := artwork.best_match(view, library, keep, minimum))), None)
            outcome = (2 if match is None
                       else 0 if card.card_id in {p.card_id for p in match.printings} else 1)
            counts = reports[card.set_id].checks.setdefault(mode, [0, 0, 0])
            counts[outcome] += 1
            if outcome == 1:
                log.warning("check %s (%s): taken for %s", card.card_id, mode,
                            match.printings[0].card_id)
            elif outcome == 2:
                log.info("check %s (%s): not recognized%s", card.card_id, mode,
                         "" if views else " (card not found in the photo)")


def summary(reports: list[Report]) -> str:
    lines = ["set      cards kept reprints missing  it  kB    size known (ok/wrong/none)"
             "  anywhere (ok/wrong/none)"]
    for r in reports:
        checks = ["/".join(map(str, r.checks.get(mode, []))) or "-"
                  for mode in ("size known", "anywhere")]
        lines.append(f"{r.set_id:8} {r.cards:5} {r.kept:4} {sum(map(len, r.linked.values())):8}"
                     f" {len(r.missing):7} {r.names.get('it', 0):3} {r.size / 1e3:5.0f}"
                     f"    {checks[0]:24}  {checks[1]}")
    for r in reports:
        if r.linked:
            lines.append(f"{r.set_id}: reprints from " + ", ".join(
                f"{source} ({len(ids)})" for source, ids in sorted(r.linked.items())))
        if r.missing:
            lines.append(f"{r.set_id}: no picture for " + ", ".join(r.missing))
        if r.no_layout:
            lines.append(f"{r.set_id}: no layout for " + ", ".join(r.no_layout))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sets", nargs="+", help="TCGdex set ids, e.g. base1")
    parser.add_argument("--out", type=Path, default=artwork.POOLS,
                        help=f"folder for the files (default {artwork.POOLS})")
    parser.add_argument("--check", type=int, default=0, metavar="N",
                        help="test N cards per edition on simulated photos")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        with connect() as conn:
            found = editions(conn, args.sets)
    except psycopg.OperationalError as error:
        log.error("%s\n%s", CONNECTION_HINT, error)
        return 1
    absent = [s for s in args.sets if s not in found]
    if absent:
        log.error("Not in the catalog: %s (run jobs.import_catalog first)", ", ".join(absent))
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    building = set(found)
    # Illustrations of the editions already built (not those being rebuilt).
    known = [artwork.Artwork(art.key, art.layout, art.source_url, art.fingerprint,
                             list(art.printings))
             for art in artwork.library(args.out) if art.key.split("/")[0] not in building]
    tests: list[tuple[Card, bytes]] = []
    reports: dict[str, Report] = {}
    # The pictures live only in this folder, deleted with it at the end.
    with tempfile.TemporaryDirectory(prefix="pokescan-pictures-") as folder, \
            httpx.Client(timeout=60, follow_redirects=True,
                         headers={"User-Agent": "PokeScan fingerprints"}) as client:
        for set_id, cards in found.items():
            reports[set_id] = build(cards, args.out, client, Path(folder), known,
                                    args.check, tests)
    if tests:
        check(tests, artwork.library(args.out), reports)
    print(summary(list(reports.values())))
    return 1 if any(r.missing or r.no_layout for r in reports.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
