"""Import the card catalog from TCGdex into sets, cards and their translations.

What it saves, in Italian and English:
- every set: series, printed code, official size, symbol, release date,
  name and logo;
- every card: number, name, image, rarity, category, variants, ... and its
  Cardmarket product id, which links it to the price history.

Pokémon TCG Pocket (a digital-only game) is skipped.

TCGdex gives the Cardmarket id only on each card's own page, so the first run
reads about 21,000 pages (roughly 10-15 minutes). Progress is saved in batches:
an interrupted run resumes where it stopped. Later runs read only new cards,
plus, once a week, the cards still without a Cardmarket id.

Run from the backend folder:
    python -m jobs.import_catalog
"""

import argparse
import datetime as dt
import logging
import sys
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx
import psycopg

from app.db import CONNECTION_HINT, connect

log = logging.getLogger("import_catalog")

API_URL = "https://api.tcgdex.net/v2"
LANGS = ("en", "it")  # English first: it is the most complete
EXCLUDED_SERIES = {"tcgp"}  # Pokémon TCG Pocket, digital only

# Few parallel requests: TCGdex is a free, community-run service.
WORKERS = 6
BATCH_SIZE = 500
MAX_ATTEMPTS = 4

SetKey = tuple[str, str]  # (set_id, lang)


# ---------------------------------------------------------------------------
# TCGdex client
# ---------------------------------------------------------------------------


class TCGdex:
    def __init__(self) -> None:
        self.client = httpx.Client(
            base_url=API_URL,
            timeout=30,
            headers={"User-Agent": "PokeScan catalog import"},
            limits=httpx.Limits(max_connections=WORKERS),
        )

    def get(self, path: str) -> Any | None:
        """GET a JSON page; None if it doesn't exist. Retries temporary errors."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.client.get(path)
                if response.status_code == 404:
                    return None
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return response.json()
                if attempt == MAX_ATTEMPTS:
                    response.raise_for_status()
            except httpx.TransportError:
                if attempt == MAX_ATTEMPTS:
                    raise
            time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def get_many(self, paths: Iterable[str]) -> list[Any | None]:
        with ThreadPoolExecutor(WORKERS) as pool:
            return list(pool.map(self.get, paths))


# ---------------------------------------------------------------------------
# Turning TCGdex pages into rows (no network, no database: easy to test)
# ---------------------------------------------------------------------------


@dataclass
class Catalog:
    sets: dict[str, tuple] = field(default_factory=dict)
    set_translations: list[tuple] = field(default_factory=list)
    cards: dict[str, tuple] = field(default_factory=dict)
    card_translations: list[tuple] = field(default_factory=list)
    # Ids of cards skipped because another card has the same set and number.
    duplicates: set[str] = field(default_factory=set)


def as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def set_row(page: dict[str, Any]) -> tuple:
    serie = page.get("serie") or {}
    count = page.get("cardCount") or {}
    release = page.get("releaseDate")
    return (
        page["id"],
        serie.get("id"),
        serie.get("name"),
        (page.get("abbreviation") or {}).get("official"),
        as_int(count.get("official")),
        as_int(count.get("total")),
        page.get("symbol"),
        dt.date.fromisoformat(release) if release else None,
    )


def build_catalog(set_pages: dict[SetKey, dict[str, Any] | None]) -> Catalog:
    """Merge the set pages of every language into catalog rows."""
    catalog = Catalog()
    numbers_seen: set[tuple[str, str]] = set()

    for lang in LANGS:  # English first, so its set data wins
        for (set_id, page_lang), page in sorted(set_pages.items()):
            if page_lang != lang or page is None:
                continue
            if (page.get("serie") or {}).get("id") in EXCLUDED_SERIES:
                continue

            catalog.sets.setdefault(set_id, set_row(page))
            catalog.set_translations.append((set_id, lang, page["name"], page.get("logo")))

            for card in page.get("cards", []):
                card_id, number = card["id"], card["localId"]
                if card_id not in catalog.cards:
                    if (set_id, number) in numbers_seen:
                        catalog.duplicates.add(card_id)
                        continue
                    numbers_seen.add((set_id, number))
                    catalog.cards[card_id] = (card_id, set_id, number)
                catalog.card_translations.append(
                    (card_id, lang, card["name"], card.get("image"))
                )
    return catalog


def cardmarket_id(card: dict[str, Any]) -> int | None:
    """Cardmarket product id: from the prices block, else from the variants."""
    from_pricing = ((card.get("pricing") or {}).get("cardmarket") or {}).get("idProduct")
    if found := as_int(from_pricing):
        return found
    for variant in card.get("variants_detailed") or []:
        if found := as_int((variant.get("thirdParty") or {}).get("cardmarket")):
            return found
    return None


def variant_names(variants: dict[str, bool] | None) -> list[str]:
    """{'normal': True, 'reverse': True, 'holo': False} -> ['normal', 'reverse']"""
    return [name for name, printed in (variants or {}).items() if printed]


def card_details(card: dict[str, Any]) -> tuple:
    """Values for UPDATE_DETAILS, in order (card_id last)."""
    return (
        card.get("category"),
        card.get("rarity"),
        card.get("illustrator"),
        card.get("regulationMark"),
        as_int(card.get("hp")),
        card.get("stage"),
        card.get("types"),
        card.get("dexId"),
        variant_names(card.get("variants")),
        cardmarket_id(card),
        card["id"],
    )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

UPSERT_SET = """
    INSERT INTO sets (set_id, series_id, series_name, abbreviation,
                      official_count, total_count, symbol_url, release_date)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (set_id) DO UPDATE SET
        series_id = EXCLUDED.series_id, series_name = EXCLUDED.series_name,
        abbreviation = EXCLUDED.abbreviation, official_count = EXCLUDED.official_count,
        total_count = EXCLUDED.total_count, symbol_url = EXCLUDED.symbol_url,
        release_date = EXCLUDED.release_date
"""
UPSERT_SET_TRANSLATION = """
    INSERT INTO set_translations (set_id, lang, name, logo_url) VALUES (%s, %s, %s, %s)
    ON CONFLICT (set_id, lang) DO UPDATE SET name = EXCLUDED.name, logo_url = EXCLUDED.logo_url
"""
UPSERT_CARD = """
    INSERT INTO cards (card_id, set_id, local_number) VALUES (%s, %s, %s)
    ON CONFLICT (card_id) DO UPDATE SET
        set_id = EXCLUDED.set_id, local_number = EXCLUDED.local_number
"""
UPSERT_CARD_TRANSLATION = """
    INSERT INTO card_translations (card_id, lang, name, image_url) VALUES (%s, %s, %s, %s)
    ON CONFLICT (card_id, lang) DO UPDATE SET name = EXCLUDED.name, image_url = EXCLUDED.image_url
"""
UPDATE_DETAILS = """
    UPDATE cards SET category = %s, rarity = %s, illustrator = %s, regulation_mark = %s,
        hp = %s, stage = %s, types = %s, dex_ids = %s, variants = %s,
        cardmarket_id = %s, details_fetched_at = now()
    WHERE card_id = %s
"""
MARK_MISSING = "UPDATE cards SET details_fetched_at = now() WHERE card_id = %s"

# Cards never read, or still without a Cardmarket id after a week (TCGdex may
# have added it since). The page is read in English when available.
CARDS_TO_READ = """
    SELECT c.card_id, CASE WHEN bool_or(t.lang = 'en') THEN 'en' ELSE min(t.lang) END
    FROM cards c JOIN card_translations t USING (card_id)
    WHERE c.details_fetched_at IS NULL
       OR (c.cardmarket_id IS NULL AND c.details_fetched_at < now() - interval '7 days')
    GROUP BY c.card_id
    ORDER BY c.card_id
"""


def save_catalog(conn: psycopg.Connection, catalog: Catalog) -> None:
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SET, list(catalog.sets.values()))
        cur.executemany(UPSERT_SET_TRANSLATION, catalog.set_translations)
        cur.executemany(UPSERT_CARD, list(catalog.cards.values()))
        cur.executemany(UPSERT_CARD_TRANSLATION, catalog.card_translations)


def save_details(conn: psycopg.Connection, pages: list[tuple[str, dict | None]]) -> None:
    found = [card_details(page) for _, page in pages if page is not None]
    missing = [(card_id,) for card_id, page in pages if page is None]
    with conn.cursor() as cur:
        if found:
            cur.executemany(UPDATE_DETAILS, found)
        if missing:
            cur.executemany(MARK_MISSING, missing)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


def import_sets(api: TCGdex, conn: psycopg.Connection) -> Catalog:
    set_ids = {lang: [s["id"] for s in api.get(f"/{lang}/sets") or []] for lang in LANGS}
    keys: list[SetKey] = [(set_id, lang) for lang in LANGS for set_id in set_ids[lang]]
    pages = api.get_many(f"/{lang}/sets/{set_id}" for set_id, lang in keys)
    catalog = build_catalog(dict(zip(keys, pages)))
    with conn.transaction():
        save_catalog(conn, catalog)
    return catalog


def import_card_details(
    api: TCGdex, conn: psycopg.Connection, progress: Callable[[int, int], None]
) -> int:
    to_read = conn.execute(CARDS_TO_READ).fetchall()
    for start in range(0, len(to_read), BATCH_SIZE):
        batch = to_read[start : start + BATCH_SIZE]
        pages = api.get_many(f"/{lang}/cards/{card_id}" for card_id, lang in batch)
        with conn.transaction():  # saved batch by batch: an interrupted run resumes
            save_details(conn, [(card_id, page) for (card_id, _), page in zip(batch, pages)])
        progress(start + len(batch), len(to_read))
    return len(to_read)


def run() -> int:
    api = TCGdex()
    try:
        conn = connect(autocommit=True)
    except psycopg.OperationalError as error:
        log.error("%s\n%s", CONNECTION_HINT, error)
        return 1

    with conn:
        catalog = import_sets(api, conn)
        log.info(
            "Sets: %d, cards: %d, names and images: %d (skipped %d duplicate numbers)",
            len(catalog.sets),
            len(catalog.cards),
            len(catalog.card_translations),
            len(catalog.duplicates),
        )

        read = import_card_details(
            api, conn, lambda done, total: log.info("Card pages read: %d/%d", done, total)
        )
        linked, total = conn.execute(
            "SELECT count(cardmarket_id), count(*) FROM cards"
        ).fetchone()
        log.info(
            "Read %d card pages. Cards linked to Cardmarket: %d of %d", read, linked, total
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--log-file",
        help="append the log to this file instead of the console (scheduled runs)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        filename=args.log_file,
        encoding="utf-8",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too much

    try:
        return run()
    except Exception:
        log.exception("Catalog import failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
