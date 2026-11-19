"""Daily job: save what changed in Cardmarket's Pokémon price guide.

Source: Cardmarket's public price guide for Pokémon (game id 6), a JSON file
with the prices of every product, regenerated once a night around 02:50
Italian time. The file stays the same until the next night, so a run at any
time of the day saves that day's prices.

Only changes are stored. A product gets a new row in cardmarket_prices when
it first appears, when any of its prices changes, or when it is no longer
listed. Every saved day is recorded in price_guide_runs, so a day without
changes is still known to be saved. The view cardmarket_prices_daily
rebuilds the full price list of any saved day.

A day that is not saved is lost for good. The job is safe to run more than
once: a day already saved (or older than the latest saved) is skipped.

Run from the backend folder:
    python -m jobs.snapshot_prices            # download and save
    python -m jobs.snapshot_prices --dry-run  # download and check, no database
    pythonw -m jobs.snapshot_prices --log-file snapshot_prices.log  # scheduled, no window
"""

import argparse
import datetime as dt
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass
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
PRICE_COLUMNS = list(PRICE_FIELDS)

# Columns filled from the file, in the order of to_rows().
COLUMNS = ["id_product", "id_category", "snapshot_date", *PRICE_COLUMNS]
STORED_COLUMNS = [*COLUMNS, "listed"]
# A product gets a new row when any of these differs from its latest row.
COMPARED_COLUMNS = ["listed", "id_category", *PRICE_COLUMNS]

Row = tuple[Any, ...]


@dataclass
class SnapshotResult:
    day: dt.date
    products: int
    changed: int  # rows added for new products or changed prices
    removed: int  # rows added for products no longer listed


def download_price_guide(url: str = PRICE_GUIDE_URL) -> dict[str, Any]:
    """Download the price guide (about 16 MB of JSON)."""
    response = httpx.get(url, timeout=120, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def created_at(guide: dict[str, Any]) -> dt.datetime:
    return dt.datetime.strptime(guide["createdAt"], "%Y-%m-%dT%H:%M:%S%z")


def snapshot_date(guide: dict[str, Any]) -> dt.date:
    """Day the file was generated, in its own time zone (Italian time).

    Converting to UTC first would move files created just after midnight
    to the previous day.
    """
    return created_at(guide).date()


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


def latest_saved_day(conn: psycopg.Connection) -> dt.date | None:
    return conn.execute("SELECT max(snapshot_date) FROM price_guide_runs").fetchone()[0]


def _columns(names: Iterable[str], table: str | None = None) -> sql.Composable:
    """Comma-separated column list, optionally qualified: "s"."avg", ..."""
    ids = (sql.Identifier(table, n) if table else sql.Identifier(n) for n in names)
    return sql.SQL(", ").join(ids)


def save_snapshot(
    conn: psycopg.Connection, created: dt.datetime, rows: list[Row]
) -> SnapshotResult:
    """Store what changed since the latest saved day.

    Runs in the caller's transaction, which must be committed afterwards.
    """
    day = created.date()

    # Today's file, in a temporary table.
    conn.execute(
        "CREATE TEMP TABLE staging (LIKE cardmarket_prices INCLUDING DEFAULTS)"
        " ON COMMIT DROP"
    )
    copy_sql = sql.SQL("COPY staging ({}) FROM STDIN").format(_columns(COLUMNS))
    with conn.cursor() as cur, cur.copy(copy_sql) as copy:
        for row in rows:
            copy.write_row(row)

    # Latest known state of every product, before today's changes.
    conn.execute(
        "CREATE TEMP TABLE latest ON COMMIT DROP AS"
        " SELECT DISTINCT ON (id_product) * FROM cardmarket_prices"
        " ORDER BY id_product, snapshot_date DESC"
    )

    # New products, and products whose values differ from their latest row.
    # IS DISTINCT FROM treats two NULLs as equal, unlike <>.
    changed = conn.execute(
        sql.SQL(
            "INSERT INTO cardmarket_prices ({stored})"
            " SELECT {staged} FROM staging s"
            " LEFT JOIN latest l ON l.id_product = s.id_product"
            " WHERE l.id_product IS NULL"
            " OR ROW({latest_values}) IS DISTINCT FROM ROW({staged_values})"
        ).format(
            stored=_columns(STORED_COLUMNS),
            staged=_columns(STORED_COLUMNS, "s"),
            latest_values=_columns(COMPARED_COLUMNS, "l"),
            staged_values=_columns(COMPARED_COLUMNS, "s"),
        )
    ).rowcount

    # Products listed until now but missing from today's file.
    removed = conn.execute(
        "INSERT INTO cardmarket_prices (id_product, id_category, snapshot_date, listed)"
        " SELECT l.id_product, l.id_category, %s, false FROM latest l"
        " WHERE l.listed"
        " AND NOT EXISTS (SELECT 1 FROM staging s WHERE s.id_product = l.id_product)",
        (day,),
    ).rowcount

    conn.execute(
        "INSERT INTO price_guide_runs"
        " (snapshot_date, created_at, products, changed, removed)"
        " VALUES (%s, %s, %s, %s, %s)",
        (day, created, len(rows), changed, removed),
    )
    return SnapshotResult(day, len(rows), changed, removed)


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
    created = created_at(guide)
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
        last = latest_saved_day(conn)
        if last is not None and created.date() <= last:
            log.info(
                "Prices of %s already saved (latest saved day: %s), nothing to do",
                created.date(),
                last,
            )
            return 0
        result = save_snapshot(conn, created, rows)

    log.info(
        "Saved %s: %d products, %d new or changed, %d no longer listed",
        result.day,
        result.products,
        result.changed,
        result.removed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
