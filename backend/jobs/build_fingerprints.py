"""Make the fingerprints of editions' illustrations (see app/recognition/artwork.py).

For each card of an edition in the catalog (Pokémon, Trainers, Energies):
download its English picture from pokemontcg.io (the picture its card list
gives, github.com/PokemonTCG/pokemon-tcg-data) into a temporary folder,
fingerprint the artwork box, and delete the picture. Only the fingerprints
are kept, in backend/fingerprints/<set_id>.sqlite, with the card's printings
in every language the catalog has a name for it (TCGdex lists: names as
printed in that language, never translated here). Cards pokemontcg.io
doesn't list are reported, not looked for elsewhere.

A card whose illustration another card already has (same English name, same
layout, the two pictures in clear agreement) is a reprint: its printing links
to that illustration instead of keeping one more. Editions are built in
release order, so the first printing keeps the illustration; build an edition
after those it reprints from.

--skip-built: editions whose file is already there are left as they are, so
an interrupted run starts again where it stopped. The build stops when the
disk of the temporary folder has less than MIN_FREE left.

--check N: N cards per edition are turned into phone-like photos
(perspective, light, blur, noise, glare, JPEG) and recognized against every
edition's fingerprints, with the set size known and without: a test on
pictures, not on real photos. --photos DIR keeps them (for --check-photos DIR
later, against more editions).

--vocabulary makes the visual words' vocabulary (artwork.VOCABULARY) from the
fingerprints already made; --reindex gives every file the words of the
vocabulary. Neither downloads anything.

Run from the backend folder, after the catalog import:
    python -m jobs.build_fingerprints base1 base2 --check 12
"""

import argparse
import datetime as dt
import json
import logging
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote

import httpx
import numpy as np
import psycopg

from app.db import CONNECTION_HINT, connect
from app.recognition import artwork, detect
from jobs.evaluate_scan import make_photo

log = logging.getLogger("build_fingerprints")

SOURCE = "pokemontcg.io"
SOURCE_DATA = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master/"
# TCGdex set ids that pokemontcg.io names otherwise ('sv03.5' is 'sv3pt5' there
# anyway, see source_set).
SOURCE_SET_IDS = {
    "lc": "base6", "bog": "bp", "exu": "ex10", "hgssp": "hsp",
    "tk-ex-latia": "tk1a", "tk-ex-latio": "tk1b", "tk-ex-p": "tk2a", "tk-ex-m": "tk2b",
    "2011bw": "mcd11", "2012bw": "mcd12", "2014xy": "mcd14", "2015xy": "mcd15",
    "2016xy": "mcd16", "2017sm": "mcd17", "2018sm": "mcd18", "2019sm": "mcd19",
    "2021swsh": "mcd21", "2022swsh": "mcd22", "sm3.5": "sm35", "sm7.5": "sm75",
    "swsh3.5": "swsh35", "swsh4.5": "swsh45", "swsh4.5sv": "swsh45sv", "cel25cc": "cel25c",
    "swsh10.5": "pgo", "fut2020": "fut20", "sv10.5b": "zsv10pt5", "sv10.5w": "rsv10pt5",
    "30th": "me55", "30th-c": "me55c",
}
LANGS = ("en", "it")
WORKERS = 4
RETRIES = 4
RETRY_WAIT = 10  # seconds, times the attempt
MAX_WAIT = 300  # seconds: the longest pause a "too many requests" may ask for
MIN_FREE = 5e9  # bytes left on the pictures' disk, below which the build stops

# A reprint links to the illustration only when the two pictures are nearly
# alike. Two illustrations of the same Pokémon never agree on more than 4
# keypoints (343 pairs, Wizards era); one illustration printed twice agrees on
# 7 to 358, low between a holo and its non-holo. Measured on simulated photos
# of such pairs: up to 80, separate fingerprints recognize the very printing
# more often than one shared fingerprint recognizes the illustration (and
# never the other one); above, the shared fingerprint does better.
REPRINT_INLIERS = 80

# Which layout (artwork.LAYOUTS) a card has: its era's, for its kind.
# TCGdex series of a single era:
SERIES_ERAS = {"base": "wizards", "gym": "wizards", "neo": "wizards", "lc": "wizards",
               "ecard": "ecard", "ex": "ex", "dp": "dp", "pl": "dp", "hgss": "hgss",
               "col": "hgss", "bw": "bw", "xy": "bw", "sm": "modern", "swsh": "modern",
               "sv": "modern", "me": "modern"}
# Series over several eras (promos, trainer kits, McDonald's): by release date.
DATED_ERAS = [(dt.date(2002, 8, 1), "ecard"), (dt.date(2003, 7, 1), "ex"),
              (dt.date(2007, 5, 1), "dp"), (dt.date(2010, 2, 10), "hgss"),
              (dt.date(2011, 4, 25), "bw"), (dt.date(2017, 2, 3), "modern")]
SET_ERAS = {"xy12": "evolutions"}  # Evolutions: Base Set's frame, but for its EX and BREAK
CATEGORY_LAYOUTS = {"Pokemon": "pokemon", "Trainer": "trainer", "Energy": "energy"}
RARITY_LAYOUTS = {"LEGEND": "legend"}  # cards whose box isn't their category's
# Reprints of old cards in their old frames: the layout is that of the
# illustration of the same name the picture agrees with (see _classic); they
# are listed by name at pokemontcg.io, under the old cards' numbers.
CLASSIC_SETS = {"cel25cc", "30th-c"}

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


class Stop(Exception):
    """The build can't go on (disk full, the source refusing): tell the user."""


@dataclass
class Card:
    card_id: str
    set_id: str
    number: str
    total: str | None  # as printed after the slash
    layout: str | None
    names: dict[str, str] = field(default_factory=dict)
    url: str | None = None  # the picture, from the source's list

    @property
    def classic(self) -> bool:
        return self.set_id in CLASSIC_SETS


@dataclass
class Report:
    set_id: str
    cards: int = 0
    kept: int = 0  # illustrations kept in the file
    linked: dict[str, list[str]] = field(default_factory=dict)  # edition -> card ids
    absent: list[str] = field(default_factory=list)  # not in the source's list
    missing: list[str] = field(default_factory=list)  # listed, but no picture came
    no_layout: list[str] = field(default_factory=list)
    classic: dict[str, str] = field(default_factory=dict)  # card id -> what it reprints
    names: dict[str, int] = field(default_factory=dict)  # lang -> printings
    downloaded: int = 0  # bytes
    hosts: dict[str, int] = field(default_factory=dict)  # pictures per host
    size: int = 0  # bytes of the file
    seconds: float = 0
    checks: dict[str, list[int]] = field(default_factory=dict)  # mode -> right, wrong, none


def era_of(set_id: str, series_id: str | None, released: dt.date | None) -> str | None:
    if set_id in SET_ERAS:
        return SET_ERAS[set_id]
    if series_id in SERIES_ERAS:
        return SERIES_ERAS[series_id]
    if released is None:
        return None
    era = "wizards"
    for start, name in DATED_ERAS:
        if released >= start:
            era = name
    return era


def layout_for(era: str | None, category: str | None, rarity: str | None = None,
               name: str = "") -> str | None:
    """The layout of a card's illustration; None if its era has none."""
    if era == "evolutions" and re.search(r"\b(EX|BREAK)$|^M ", name):
        era = "bw"  # Evolutions' own cards: XY's frame
    for kind in (RARITY_LAYOUTS.get(rarity or ""), CATEGORY_LAYOUTS.get(category or "")):
        if era and kind and f"{era}-{kind}" in artwork.LAYOUTS:
            return f"{era}-{kind}"
    return None


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
    details: dict[str, tuple] = {}
    for card_id, set_id, number, category, rarity, series_id, official, released, lang, name \
            in rows:
        cards = found.setdefault(set_id, {})
        if card_id not in cards:
            cards[card_id] = Card(card_id, set_id, number, None, None)
            details[card_id] = (official, era_of(set_id, series_id, released), category, rarity)
        cards[card_id].names[lang] = name
    for cards in found.values():
        numbers = [c.number for c in cards.values()]
        for card in cards.values():
            official, era, category, rarity = details[card.card_id]
            card.total = printed_total(card.number, official, numbers)
            card.layout = layout_for(era, category, rarity, card.names.get("en", ""))
    return {set_id: sorted(cards.values(), key=_order) for set_id, cards in found.items()}


# ---------------------------------------------------------------------------
# The source: pokemontcg.io's card lists, and their pictures
# ---------------------------------------------------------------------------


def source_set(set_id: str) -> str:
    """pokemontcg.io's id of a TCGdex set: 'sv03.5' is 'sv3pt5', 'me01' 'me1'."""
    return SOURCE_SET_IDS.get(set_id) or re.sub(r"^(sv|me)0(\d)", r"\1\2",
                                                 set_id.replace(".", "pt"))


def _plain_number(number: str) -> str:
    """'025' is 25, 'H01' is H1, '%3F' is '?': numbers as compared."""
    return re.sub(r"(^|[A-Z])0+(\d)", r"\1\2", unquote(number).strip().upper())


def _plain_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def attach_pictures(cards: list[Card], listed: list[dict]) -> None:
    """Each card's picture URL, from the source's list of the edition: by
    number; by name for reprints in old frames (listed under the old numbers)."""
    if cards and cards[0].classic:
        unused: dict[str, list[dict]] = {}
        for entry in sorted(listed, key=lambda e: (_order_key(e["number"]), e["images"]["large"])):
            unused.setdefault(_plain_name(entry["name"]), []).append(entry)
        for card in cards:
            name = _plain_name(card.names.get("en", ""))
            keys = [name] if unused.get(name) else [
                k for k, entries in unused.items() if k.startswith(name) and entries]
            if len(keys) == 1:
                card.url = unused[keys[0]].pop(0)["images"]["large"]
        return
    by_number = {_plain_number(e["number"]): e["images"]["large"] for e in listed}
    for card in cards:
        card.url = by_number.get(_plain_number(card.number))


def _order_key(number: str) -> tuple:
    digits = re.search(r"\d+", number)
    return (int(digits.group()) if digits else 0, number)


def _listing(client: httpx.Client, path: str) -> list[dict] | None:
    for attempt in range(1, RETRIES + 1):
        try:
            response = client.get(SOURCE_DATA + path)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            if attempt == RETRIES:
                raise Stop(f"pokemontcg.io's list {path} unreadable: {error}") from error
            time.sleep(RETRY_WAIT * attempt)
    return None


def _download(client: httpx.Client, url: str, folder: Path) -> tuple[Path | None, bool]:
    """The picture saved in `folder` (None if the source has none), and whether
    the source refused or failed (rather than having no such picture). A dropped
    connection or a server error is tried again; "too many requests" waits as
    asked."""
    for attempt in range(1, RETRIES + 1):
        try:
            response = client.get(url)
            if response.status_code == 404:
                log.warning("No picture at %s", url)
                return None, False
            if response.status_code == 429:
                wait = min(float(response.headers.get("Retry-After", 0) or 0)
                           or RETRY_WAIT * attempt * 3, MAX_WAIT)
                log.warning("Too many requests: waiting %.0f s", wait)
                time.sleep(wait)
                continue
            response.raise_for_status()
            break
        except httpx.HTTPError as error:
            if attempt == RETRIES:
                log.warning("No picture from %s: %s", url, error)
                return None, True
            time.sleep(RETRY_WAIT * attempt)
    else:
        return None, True
    file = folder / re.sub(r"[^\w.-]", "_", url.split("//", 1)[1])
    file.write_bytes(response.content)
    return file, False


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


Known = dict[str, list[artwork.Artwork]]  # the illustrations so far, by English name


def _first_printing(card: Card, fingerprint: artwork.Fingerprint,
                    known: Known) -> tuple[artwork.Artwork | None, int]:
    """The illustration this card reprints, if one is clearly the same; and
    the best agreement found."""
    name = card.names.get("en", "").casefold()
    scored = sorted(((artwork.agreement(fingerprint, art.fingerprint), art)
                     for art in known.get(name, []) if art.layout == card.layout),
                    key=lambda s: s[0], reverse=True)
    if not scored:
        return None, 0
    best, art = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if best >= REPRINT_INLIERS and best >= artwork.MIN_MARGIN * runner_up:
        return art, best
    return None, best


def _classic(card: Card, picture: np.ndarray, known: Known
             ) -> tuple[str | None, artwork.Fingerprint | None, artwork.Artwork | None, int]:
    """A reprint in an old frame: the layout of the illustration of the same
    name its picture agrees with most (if one agrees at all), the fingerprint
    in that layout, the illustration if clearly the same (as _first_printing),
    and the agreement."""
    taken: dict[str, artwork.Fingerprint] = {}
    scored = []
    for art in known.get(card.names.get("en", "").casefold(), []):
        if art.layout not in taken:
            taken[art.layout] = artwork.fingerprint(picture, art.layout)
        scored.append((artwork.agreement(taken[art.layout], art.fingerprint), art))
    scored.sort(key=lambda s: s[0], reverse=True)
    if not scored or scored[0][0] < artwork.MIN_INLIERS:
        return None, None, None, scored[0][0] if scored else 0
    best, art = scored[0]
    runner_up = next((s for s, other in scored[1:] if other.layout == art.layout), 0)
    same = best >= REPRINT_INLIERS and best >= artwork.MIN_MARGIN * runner_up
    return art.layout, taken[art.layout], art if same else None, best


def glare(card: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Light reflected off part of the card: a bright, soft patch."""
    height, width = card.shape[:2]
    x, y = rng.uniform(0.1, 0.9) * width, rng.uniform(0.1, 0.6) * height
    radius = rng.uniform(0.12, 0.3) * width
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    patch = np.exp(-((xs - x) ** 2 + ((ys - y) / 1.4) ** 2) / (2 * radius**2))
    lit = card.astype(np.float32) + patch[..., None] * rng.uniform(90, 170)
    return np.clip(lit, 0, 255).astype(np.uint8)


def _file_name(card_id: str) -> str:
    return re.sub(r"[^\w.-]", "_", card_id) + ".jpg"


def build(cards: list[Card], out: Path, client: httpx.Client, folder: Path, known: Known,
          samples: int, photos: Path, vocabulary: np.ndarray | None) -> Report:
    """Fingerprint one edition; reprints link to the illustrations in `known`
    (which this edition's own are added to). Photos for --check go in `photos`."""
    started = time.monotonic()
    set_id = cards[0].set_id
    free = shutil.disk_usage(folder).free
    if free < MIN_FREE:
        raise Stop(f"only {free / 1e9:.1f} GB left on the disk of {folder}")
    report = Report(set_id, cards=len(cards))
    report.no_layout = [c.card_id for c in cards if c.layout is None]
    report.absent = [c.card_id for c in cards if c.layout is not None and c.url is None]
    usable = [c for c in cards if c.layout is not None and c.url is not None]
    if not usable:
        log.info("%s: none of its %d cards at %s, no file", set_id, len(cards), SOURCE)
        return report
    with ThreadPoolExecutor(WORKERS) as pool:
        files = list(pool.map(lambda c: _download(client, c.url, folder), usable))
    failed = sum(refused for _, refused in files)
    if usable and failed > max(3, len(usable) // 5):
        for file, _ in files:
            if file is not None:
                file.unlink(missing_ok=True)
        raise Stop(f"{set_id}: {failed} of {len(usable)} pictures refused by the source")

    rng = np.random.default_rng(sum(map(ord, set_id)))
    tested = set(rng.choice(len(usable), min(samples, len(usable)), replace=False).tolist())
    artworks: list[artwork.Artwork] = []
    printings: list[tuple[str, artwork.Printing]] = []
    for index, (card, (file, _)) in enumerate(zip(usable, files)):
        picture = None
        if file is not None:
            report.downloaded += file.stat().st_size
            host = card.url.split("/")[2]
            report.hosts[host] = report.hosts.get(host, 0) + 1
            picture = detect.as_card(detect.decode(file.read_bytes()))
            file.unlink()
        if picture is None:
            report.missing.append(card.card_id)
            continue
        if card.classic:
            layout, fingerprint, first, inliers = _classic(card, picture, known)
            if layout is None:
                report.classic[card.card_id] = "-"
            else:
                card.layout = layout
                report.classic[card.card_id] = (first.key if first else
                                                f"own ({inliers} like {layout})")
        if not card.classic or layout is None:
            fingerprint = artwork.fingerprint(picture, card.layout)
            first, inliers = _first_printing(card, fingerprint, known)
        if first is not None:
            key = first.key
            origin = key.split("/")[0]
            report.linked.setdefault(origin, []).append(card.card_id)
            log.info("%s reprints %s (%d keypoints agree)", card.card_id, key, inliers)
        else:
            if inliers >= artwork.MIN_INLIERS:
                log.info("%s: like an illustration of the same name (%d keypoints agree),"
                         " kept as its own", card.card_id, inliers)
            key = artwork.artwork_key(set_id, card.number)
            art = artwork.Artwork(key, card.layout, card.url, fingerprint)
            artworks.append(art)
            known.setdefault(card.names.get("en", "").casefold(), []).append(art)
            first = art
        for lang, name in sorted(card.names.items()):
            printing = artwork.Printing(lang, set_id, card.total, card.number, card.card_id, name)
            printings.append((key, printing))
            first.printings.append(printing)
            report.names[lang] = report.names.get(lang, 0) + 1
        if index in tested:
            table = TABLES[int(rng.integers(len(TABLES)))]
            (photos / _file_name(card.card_id)).write_bytes(
                make_photo(glare(picture, rng), rng, turn=0, background=table))
            with open(photos / "photos.jsonl", "a", encoding="utf-8") as listed:
                listed.write(json.dumps({"card_id": card.card_id, "set_id": set_id,
                                         "total": card.total}) + "\n")

    path = out / f"{set_id}.sqlite"
    official = next((c.total for c in cards if c.total and c.total.isdigit()), "")
    meta = {"set_id": set_id, "official_count": official, "source": SOURCE,
            "features": str(artwork.FEATURES),
            "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    artwork.write_edition(path, meta, artworks, printings, vocabulary)
    for art in artworks:  # from now on read from the file when needed
        art.stored, art.file = None, path
    report.kept, report.size = len(artworks), path.stat().st_size
    report.seconds = time.monotonic() - started
    log.info("%s: %d cards, %d illustrations kept, %d reprints, %d not at the source,"
             " %d without picture; %.1f MB downloaded (deleted); %s %.0f kB", set_id,
             report.cards, report.kept, sum(map(len, report.linked.values())),
             len(report.absent), len(report.missing), report.downloaded / 1e6, path.name,
             report.size / 1e3)
    return report


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


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


def check(photos: Path, library: list[artwork.Artwork],
          reports: dict[str, Report]) -> dict[str, list[float]]:
    """Recognize the test photos saved in `photos`: with the set size known,
    then anywhere. Returns the seconds each search took, per mode."""
    def keep_size(total: str | None) -> Callable[[artwork.Printing], bool]:
        return lambda p: artwork.same_number(p.set_total, total)

    timings: dict[str, list[float]] = {"size known": [], "anywhere": []}
    listed = photos / "photos.jsonl"
    entries = ([json.loads(line) for line in listed.read_text(encoding="utf-8").splitlines()]
               if listed.exists() else [])
    for entry in entries:
        card_id, set_id, total = entry["card_id"], entry["set_id"], entry["total"]
        report = reports.setdefault(set_id, Report(set_id))
        views = list(_views(detect.decode((photos / _file_name(card_id)).read_bytes())))
        modes = {"size known": (keep_size(total), artwork.MIN_INLIERS),
                 "anywhere": (lambda p: True, artwork.MIN_INLIERS_ANYWHERE)}
        for mode, (keep, minimum) in modes.items():
            if mode == "size known" and total is None:
                continue
            started = time.perf_counter()
            match = next((m for view in views
                          if (m := artwork.best_match(view, library, keep, minimum))), None)
            timings[mode].append(time.perf_counter() - started)
            outcome = (2 if match is None
                       else 0 if card_id in {p.card_id for p in match.printings} else 1)
            counts = report.checks.setdefault(mode, [0, 0, 0])
            counts[outcome] += 1
            if outcome == 1:
                log.warning("check %s (%s): taken for %s", card_id, mode,
                            match.printings[0].card_id)
            elif outcome == 2:
                log.info("check %s (%s): not recognized%s", card_id, mode,
                         "" if views else " (card not found in the photo)")
    return timings


def summary(reports: list[Report]) -> str:
    lines = ["set      cards kept reprints absent missing  it    kB  size known (ok/wrong/none)"
             "  anywhere (ok/wrong/none)"]
    for r in reports:
        checks = ["/".join(map(str, r.checks.get(mode, []))) or "-"
                  for mode in ("size known", "anywhere")]
        lines.append(f"{r.set_id:8} {r.cards:5} {r.kept:4} {sum(map(len, r.linked.values())):8}"
                     f" {len(r.absent):6} {len(r.missing):7} {r.names.get('it', 0):3}"
                     f" {r.size / 1e3:5.0f}  {checks[0]:26}  {checks[1]}")
    for r in reports:
        if r.linked:
            lines.append(f"{r.set_id}: reprints from " + ", ".join(
                f"{source} ({len(ids)})" for source, ids in sorted(r.linked.items())))
        if r.absent:
            lines.append(f"{r.set_id}: not at {SOURCE}: " + ", ".join(r.absent))
        if r.missing:
            lines.append(f"{r.set_id}: no picture for " + ", ".join(r.missing))
        if r.no_layout:
            lines.append(f"{r.set_id}: no layout for " + ", ".join(r.no_layout))
        for card_id, what in r.classic.items():
            lines.append(f"{r.set_id}: {card_id} in an old frame: {what}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Visual words
# ---------------------------------------------------------------------------


def train_vocabulary(descriptors: np.ndarray, size: int, rng: np.random.Generator,
                     rounds: int = 4, sample: int = 150_000) -> np.ndarray:
    """Reference descriptors spread like these ones (k-majority: each is the
    bitwise majority of the descriptors nearest to it)."""
    data = descriptors[rng.choice(len(descriptors), min(sample, len(descriptors)),
                                  replace=False)]
    centers = data[rng.choice(len(data), size, replace=False)].copy()
    bits = np.unpackbits(data, axis=1)
    for _ in range(rounds):
        nearest = artwork.words(data, centers)
        order = np.argsort(nearest, kind="stable")
        starts = np.searchsorted(nearest[order], np.arange(size + 1))
        for center in range(size):
            members = order[starts[center]:starts[center + 1]]
            if len(members):
                centers[center] = np.packbits(bits[members].mean(axis=0) >= 0.5)
    return centers


def reindex(out: Path) -> int:
    """Give every file in `out` the visual words of its vocabulary."""
    vocabulary = artwork.read_vocabulary(out)
    if vocabulary is None:
        raise Stop(f"no {artwork.VOCABULARY} in {out}: make it with --vocabulary")
    paths = sorted(out.glob("*.sqlite"))
    for path in paths:
        meta, artworks, printings = artwork.read_edition(path, ("2", artwork.FORMAT))
        artwork.write_edition(path, meta, artworks, printings, vocabulary)
    log.info("%d files given the visual words of %s", len(paths), artwork.VOCABULARY)
    return len(paths)


def make_vocabulary(out: Path) -> None:
    descriptors = [art.fingerprint.descriptors for path in sorted(out.glob("*.sqlite"))
                   for art in artwork.read_edition(path, ("2", artwork.FORMAT))[1]]
    if not descriptors:
        raise Stop(f"no fingerprints in {out} to make the vocabulary from")
    every = np.vstack(descriptors)
    vocabulary = train_vocabulary(every, artwork.VOCABULARY_SIZE, np.random.default_rng(0))
    (out / artwork.VOCABULARY).write_bytes(vocabulary.tobytes())
    log.info("Vocabulary of %d words from %d descriptors", len(vocabulary), len(every))


# ---------------------------------------------------------------------------


def _built(path: Path, vocabulary: np.ndarray | None) -> bool:
    """Whether the edition's file is there, complete, of this format and vocabulary."""
    if not path.exists():
        return False
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            meta = dict(db.execute("SELECT key, value FROM meta"))
        finally:
            db.close()
    except sqlite3.Error:
        return False
    return (meta.get("format") == artwork.FORMAT
            and meta.get("vocabulary") == artwork.vocabulary_id(vocabulary))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sets", nargs="*", help="TCGdex set ids, e.g. base1")
    parser.add_argument("--out", type=Path, default=artwork.POOLS,
                        help=f"folder for the files (default {artwork.POOLS})")
    parser.add_argument("--check", type=int, default=0, metavar="N",
                        help="test N cards per edition on simulated photos")
    parser.add_argument("--photos", type=Path, help="keep the --check photos in this folder")
    parser.add_argument("--check-photos", type=Path, metavar="DIR",
                        help="recognize the photos kept in DIR, against every file")
    parser.add_argument("--skip-built", action="store_true",
                        help="leave the editions already built as they are")
    parser.add_argument("--report", type=Path, help="append each edition's report (JSON) here")
    parser.add_argument("--vocabulary", action="store_true",
                        help="make the visual words' vocabulary, then --reindex")
    parser.add_argument("--reindex", action="store_true",
                        help="give every file the visual words of the vocabulary")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        if args.vocabulary:
            make_vocabulary(args.out)
        if args.vocabulary or args.reindex:
            reindex(args.out)
            return 0
        if args.check_photos:
            reports: dict[str, Report] = {}
            timings = check(args.check_photos, artwork.library(args.out), reports)
            print(summary(list(reports.values())))
            for mode, seconds in timings.items():
                if seconds:
                    print(f"{mode}: {np.mean(seconds) * 1000:.0f} ms per photo on average,"
                          f" {np.percentile(seconds, 95) * 1000:.0f} ms at most for 95%")
            return 0
        return _build(args)
    except Stop as error:
        log.error("Stopped: %s", error)
        return 2


def _build(args: argparse.Namespace) -> int:
    if not args.sets:
        log.error("Which editions? e.g. base1 base2")
        return 1
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
    vocabulary = artwork.read_vocabulary(args.out)
    if vocabulary is None:
        log.warning("No %s in %s: files without visual words (see --vocabulary)",
                    artwork.VOCABULARY, args.out)
    if args.skip_built:
        done = [s for s in found if _built(args.out / f"{s}.sqlite", vocabulary)]
        if done:
            log.info("Already built, left as they are: %s", " ".join(done))
        found = {s: cards for s, cards in found.items() if s not in done}
    building = set(found)
    # Illustrations of the editions already built (not those being rebuilt),
    # read from their files only when compared.
    known: Known = {}
    for art in artwork.library(args.out):
        if art.key.split("/")[0] in building:
            continue
        copy = artwork.Artwork(art.key, art.layout, art.source_url, None, list(art.printings),
                               file=art.file)
        for name in {p.name.casefold() for p in art.printings if p.lang == "en"}:
            known.setdefault(name, []).append(copy)
    reports: dict[str, Report] = {}
    # The pictures live only in this folder, deleted with it at the end.
    with tempfile.TemporaryDirectory(prefix="pokescan-pictures-") as folder, \
            tempfile.TemporaryDirectory(prefix="pokescan-photos-") as scratch, \
            httpx.Client(timeout=60, follow_redirects=True,
                         headers={"User-Agent": "PokeScan fingerprints"}) as client:
        photos = args.photos or Path(scratch)
        photos.mkdir(parents=True, exist_ok=True)
        available = {s["id"] for s in _listing(client, "sets/en.json") or []}
        for set_id, cards in found.items():
            source = source_set(set_id)
            listed = _listing(client, f"cards/en/{source}.json") if source in available else None
            attach_pictures(cards, listed or [])
            reports[set_id] = build(cards, args.out, client, Path(folder), known,
                                    args.check, photos, vocabulary)
            if args.report:
                with open(args.report, "a", encoding="utf-8") as saved:
                    saved.write(json.dumps(asdict(reports[set_id]), ensure_ascii=False) + "\n")
        if args.check and not args.photos:
            check(photos, artwork.library(args.out), reports)
    print(summary(list(reports.values())))
    return 1 if any(r.missing or r.no_layout for r in reports.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
