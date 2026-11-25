"""Make the fingerprints of an edition's illustrations (see app/recognition/artwork.py).

For each card of the edition in the catalog (Pokémon, Trainers, Energies):
download its English picture from pokemontcg.io into a temporary folder,
fingerprint the artwork box, and delete the picture. Only the fingerprints
are kept, in backend/fingerprints/<set_id>.sqlite, with the card's printings
in every language the catalog has it in (TCGdex lists: names as printed in
that language).

Run from the backend folder, after the catalog import:
    python -m jobs.build_fingerprints base1
"""

import argparse
import datetime as dt
import logging
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psycopg

from app.db import CONNECTION_HINT, connect
from app.recognition import artwork, detect

log = logging.getLogger("build_fingerprints")

SOURCE = "pokemontcg.io"
PICTURE_URL = "https://images.pokemontcg.io/{set_id}/{number}_hires.png"
# TCGdex set ids that pokemontcg.io names otherwise.
SOURCE_SET_IDS = {"lc": "base6"}
LANGS = ("en", "it")
WORKERS = 4

CARDS = """
SELECT c.card_id, c.local_number, c.category, s.series_id, s.official_count,
       t.lang, t.name
FROM cards c
JOIN sets s ON s.set_id = c.set_id
JOIN card_translations t ON t.card_id = c.card_id AND t.lang = ANY(%(langs)s)
WHERE c.set_id = %(set_id)s
ORDER BY c.card_id, t.lang
"""


def _download(client: httpx.Client, url: str, folder: Path) -> Path | None:
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as error:
        log.warning("No picture at %s: %s", url, error)
        return None
    file = folder / url.rsplit("/", 1)[1]
    file.write_bytes(response.content)
    return file


def build(set_id: str, out: Path) -> int:
    """Fingerprint one edition. Returns how many cards have no fingerprint."""
    with connect() as conn:
        rows = conn.execute(CARDS, {"set_id": set_id, "langs": list(LANGS)}).fetchall()
    if not rows:
        log.error("%s: no cards in the catalog (run jobs.import_catalog first)", set_id)
        return 1

    cards: dict[str, dict] = {}
    for card_id, number, category, series_id, official_count, lang, name in rows:
        card = cards.setdefault(card_id, {
            "number": number, "layout": artwork.layout_for(series_id, category),
            "total": official_count, "names": {},
        })
        card["names"][lang] = name
    unknown = sorted({c for c in cards if cards[c]["layout"] is None})
    if unknown:
        log.error("%s: the artwork box of this era isn't measured yet (artwork.LAYOUTS)",
                  set_id)
        return len(unknown)

    source_set = SOURCE_SET_IDS.get(set_id, set_id)
    urls = {card_id: PICTURE_URL.format(set_id=source_set, number=card["number"])
            for card_id, card in cards.items()}
    artworks, missing, downloaded = [], 0, 0
    # The pictures live only in this folder, deleted with it at the end.
    with tempfile.TemporaryDirectory(prefix="pokescan-pictures-") as folder, \
            httpx.Client(timeout=30, follow_redirects=True,
                         headers={"User-Agent": "PokeScan fingerprints"}) as client:
        with ThreadPoolExecutor(WORKERS) as pool:
            files = dict(zip(urls, pool.map(lambda u: _download(client, u, Path(folder)),
                                            urls.values())))
        for artwork_id, (card_id, file) in enumerate(sorted(files.items()), start=1):
            card = cards[card_id]
            picture = None
            if file is not None:
                downloaded += file.stat().st_size
                picture = detect.as_card(detect.decode(file.read_bytes()))
            if picture is None:
                log.warning("%s: no usable picture", card_id)
                missing += 1
                continue
            printings = [
                artwork.Printing(lang, set_id, card["total"], card["number"], card_id, name)
                for lang, name in sorted(card["names"].items())
            ]
            artworks.append(artwork.Artwork(artwork_id, card["layout"], urls[card_id],
                                            artwork.fingerprint(picture, card["layout"]),
                                            printings))

    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{set_id}.sqlite"
    meta = {"set_id": set_id, "official_count": str(next(iter(cards.values()))["total"]),
            "source": SOURCE, "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    artwork.write_pool(path, meta, artworks)
    langs = {lang: sum(lang in c["names"] for c in cards.values()) for lang in LANGS}
    log.info("%s: %d illustrations, printings %s; %d pictures downloaded (%.1f MB, deleted);"
             " %s: %.0f kB", set_id, len(artworks),
             ", ".join(f"{lang} {n}" for lang, n in langs.items()),
             sum(f is not None for f in files.values()), downloaded / 1e6, path.name,
             path.stat().st_size / 1e3)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sets", nargs="+", help="TCGdex set ids, e.g. base1")
    parser.add_argument("--out", type=Path, default=artwork.POOLS,
                        help=f"folder for the files (default {artwork.POOLS})")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        missing = sum(build(set_id, args.out) for set_id in args.sets)
    except psycopg.OperationalError as error:
        log.error("%s\n%s", CONNECTION_HINT, error)
        return 1
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
