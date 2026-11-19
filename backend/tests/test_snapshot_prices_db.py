"""Tests of the price history against a real PostgreSQL.

They run in a throwaway database (pokescan_test), created and dropped by the
fixture in conftest.py, and are skipped when PostgreSQL can't be reached.
"""

import datetime as dt

from app.db import connect
from jobs import snapshot_prices
from jobs.snapshot_prices import created_at, latest_saved_day, save_snapshot, to_rows


def guide(day: int, trends: dict[int, float]) -> dict:
    """A price guide created on November `day`, with a trend price per product."""
    return {
        "createdAt": f"2026-11-{day:02d}T02:48:00+0100",
        "priceGuides": [
            {"idProduct": product, "idCategory": 51, "trend": trend}
            for product, trend in trends.items()
        ],
    }


def save(conn, price_guide):
    result = save_snapshot(conn, created_at(price_guide), to_rows(price_guide))
    conn.commit()
    return result


def rows_added_on(conn, day):
    query = (
        "SELECT id_product, listed, trend FROM cardmarket_prices"
        " WHERE snapshot_date = %s ORDER BY id_product"
    )
    return [
        (product, listed, None if trend is None else float(trend))
        for product, listed, trend in conn.execute(query, (dt.date(2026, 11, day),))
    ]


def price_list_of(conn, day):
    query = "SELECT id_product, trend FROM cardmarket_prices_daily WHERE day = %s"
    rows = conn.execute(query, (dt.date(2026, 11, day),)).fetchall()
    return {product: float(trend) for product, trend in rows}


def test_first_day_stores_every_product(db):
    result = save(db, guide(19, {1: 1.00, 2: 2.00}))

    assert (result.changed, result.removed) == (2, 0)
    assert rows_added_on(db, 19) == [(1, True, 1.00), (2, True, 2.00)]


def test_next_day_stores_only_new_changed_and_removed_products(db):
    save(db, guide(19, {1: 1.00, 2: 2.00, 3: 3.00}))

    # 1 unchanged, 2 changed, 3 removed, 4 new.
    result = save(db, guide(20, {1: 1.00, 2: 2.50, 4: 4.00}))

    assert (result.changed, result.removed) == (2, 1)
    assert rows_added_on(db, 20) == [(2, True, 2.50), (3, False, None), (4, True, 4.00)]


def test_a_day_without_changes_adds_no_prices_but_is_recorded(db):
    save(db, guide(19, {1: 1.00}))

    result = save(db, guide(20, {1: 1.00}))

    assert (result.changed, result.removed) == (0, 0)
    assert rows_added_on(db, 20) == []
    assert latest_saved_day(db) == dt.date(2026, 11, 20)


def test_daily_view_rebuilds_the_full_price_list_of_each_day(db):
    day_19 = {1: 1.00, 2: 2.00, 3: 3.00}
    day_20 = {1: 1.00, 2: 2.50, 4: 4.00}
    day_21 = {1: 1.00, 2: 2.50, 3: 3.50, 4: 4.00}  # 3 is listed again
    for day, trends in ((19, day_19), (20, day_20), (21, day_21)):
        save(db, guide(day, trends))

    assert price_list_of(db, 19) == day_19
    assert price_list_of(db, 20) == day_20
    assert price_list_of(db, 21) == day_21


def test_job_skips_days_already_saved_and_older_files(db, monkeypatch):
    monkeypatch.setattr(snapshot_prices, "check_plausible", lambda rows: None)
    monkeypatch.setattr(snapshot_prices, "connect", lambda: connect(dbname=db.info.dbname))

    def run_with(price_guide):
        monkeypatch.setattr(snapshot_prices, "download_price_guide", lambda: price_guide)
        return snapshot_prices.run(dry_run=False)

    assert run_with(guide(20, {1: 1.00})) == 0
    assert run_with(guide(20, {1: 1.00})) == 0  # same day again
    assert run_with(guide(19, {1: 9.99})) == 0  # older file

    assert db.execute("SELECT count(*) FROM cardmarket_prices").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM price_guide_runs").fetchone()[0] == 1
