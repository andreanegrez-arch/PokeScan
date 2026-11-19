"""Daily job: save Cardmarket's Pokémon price guide into cardmarket_prices.

Source: Cardmarket's public price guide for Pokémon (game id 6), a JSON file
with the prices of every product, regenerated once a night around 02:50
Italian time. The file stays the same until the next night, so a run at any
time of the day saves that day's prices.

This table is the price history: a day that is not saved is lost for good.
The job is safe to run more than once: a day already saved is skipped.

Run from the backend folder:
    python -m jobs.snapshot_prices            # download and save
    python -m jobs.snapshot_prices --dry-run  # download and check, no database
    pythonw -m jobs.snapshot_prices --log-file snapshot_prices.log  # scheduled, no window
"""

import argparse
import datetime as dt
import logging
import sys
from typing import Any

import httpx
import psycopg
from psycopg import sql

from app.db import CONNECTION_HINT, connect

log = logging.getLogger("snapshot_prices")

PRICE_GUIDE_URL = (
    "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json"
)

# The file lists about 80,000 products (November 2026): far fewer means a
# broken file, and saving it would block the real data for that day.
MIN_EXPECTED_PRODUCTS = 40_000

# Price columns of cardmarket_prices -> field names in the price guide.
PRICE_FIELDS = {
    "avg": "avg",
    "low": "low",
    "trend": "trend",
    "avg1": "avg1",
    "avg7": "avg7",
    "avg30": "avg30",
    "avg_holo": "avg-holo",
    "low_holo": "low-holo",
    "trend_holo": "trend-holo",
    "avg1_holo": "avg1-holo",
    "avg7_holo": "avg7-holo",
    "avg30_holo": "avg30-holo",
}
COLUMNS = ["id_product", "id_category", "snapshot_date", *PRICE_FIELDS]

Row = tuple[Any, ...]


def download_price_guide(url: str = PRICE_GUIDE_URL) -> dict[str, Any]:
    """Download the price guide (about 16 MB of JSON)."""
    response = httpx.get(url, timeout=120, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def snapshot_date(guide: dict[str, Any]) -> dt.date:
    """Day the file was generated, in its own time zone (Italian time).

    Converting to UTC first would move files created just after midnight
    to the previous day.
    """
    created_at = dt.datetime.strptime(guide["createdAt"], "%Y-%m-%dT%H:%M:%S%z")
    return created_at.date()


def to_rows(guide: dict[str, Any]) -> list[Row]:
    """Turn the price guide into rows for cardmarket_prices, in COLUMNS order."""
    day = snapshot_date(guide)
    return [
        (
            item["idProduct"],
            item.get("idCategory"),
            day,
            *(item.get(field) for field in PRICE_FIELDS.values()),
        )
        for item in guide["priceGuides"]
    ]


def check_plausible(rows: list[Row]) -> None:
    """Refuse files that look broken, before anything is saved."""
    if len(rows) < MIN_EXPECTED_PRODUCTS:
        raise ValueError(
            f"Only {len(rows)} products in the price guide "
            f"(expected at least {MIN_EXPECTED_PRODUCTS}): not saving it"
        )
    ids = [row[0] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("The price guide lists the same product more than once")


def is_saved(conn: psycopg.Connection, day: dt.date) -> bool:
    query = "SELECT 1 FROM cardmarket_prices WHERE snapshot_date = %s LIMIT 1"
    return conn.execute(query, (day,)).fetchone() is not None


def save_rows(conn: psycopg.Connection, rows: list[Row]) -> None:
    """Bulk insert with COPY: much faster than one INSERT per row."""
    copy_sql = sql.SQL("COPY cardmarket_prices ({}) FROM STDIN").format(
        sql.SQL(", ").join(map(sql.Identifier, COLUMNS))
    )
    with conn.cursor() as cur, cur.copy(copy_sql) as copy:
        for row in rows:
            copy.write_row(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="download and check the file without touching the database",
    )
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

    try:
        return run(dry_run=args.dry_run)
    except Exception:
        # Scheduled runs have no console: make sure every failure lands in the log.
        log.exception("Price snapshot failed")
        return 1


def run(dry_run: bool) -> int:
    guide = download_price_guide()
    day = snapshot_date(guide)
    rows = to_rows(guide)
    log.info("Price guide created at %s: %d products", guide["createdAt"], len(rows))
    check_plausible(rows)

    if dry_run:
        log.info("Dry run: nothing saved")
        return 0

    try:
        conn = connect()
    except psycopg.OperationalError as error:
        log.error("%s\n%s", CONNECTION_HINT, error)
        return 1

    # One transaction: the day is saved completely or not at all.
    with conn:
        if is_saved(conn, day):
            log.info("Prices of %s already saved, nothing to do", day)
            return 0
        save_rows(conn, rows)

    log.info("Saved %d prices for %s", len(rows), day)
    return 0


if __name__ == "__main__":
    sys.exit(main())
