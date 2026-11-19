import datetime as dt

import pytest

from jobs import snapshot_prices
from jobs.snapshot_prices import COLUMNS, check_plausible, snapshot_date, to_rows


def make_guide(created_at="2026-11-19T02:48:38+0100", items=None):
    if items is None:
        items = [
            {
                "idProduct": 483559,
                "idCategory": 51,
                "avg": 0.06,
                "low": 0.02,
                "trend": 0.09,
                "avg1": 0.05,
                "avg7": 0.09,
                "avg30": 0.06,
                "avg-holo": 0.22,
                "low-holo": 0.08,
                "trend-holo": 0.28,
                "avg1-holo": 0.23,
                "avg7-holo": 0.41,
                "avg30-holo": 0.29,
            }
        ]
    return {"version": 1, "createdAt": created_at, "priceGuides": items}


def test_snapshot_date_uses_the_file_time_zone():
    # 00:30 in Italy is still 23:30 of the previous day in UTC.
    guide = make_guide(created_at="2026-11-19T00:30:00+0100")

    assert snapshot_date(guide) == dt.date(2026, 11, 19)


def test_to_rows_maps_every_column():
    (row,) = to_rows(make_guide())
    values = dict(zip(COLUMNS, row))

    assert values["id_product"] == 483559
    assert values["id_category"] == 51
    assert values["snapshot_date"] == dt.date(2026, 11, 19)
    assert values["trend"] == 0.09
    assert values["trend_holo"] == 0.28
    assert values["avg30_holo"] == 0.29


def test_to_rows_keeps_missing_prices_as_null():
    (row,) = to_rows(make_guide(items=[{"idProduct": 1, "trend": 2.5}]))
    values = dict(zip(COLUMNS, row))

    assert values["trend"] == 2.5
    assert values["avg"] is None
    assert values["trend_holo"] is None


def test_check_plausible_refuses_a_file_with_too_few_products(monkeypatch):
    monkeypatch.setattr(snapshot_prices, "MIN_EXPECTED_PRODUCTS", 3)
    rows = to_rows(make_guide(items=[{"idProduct": 1}, {"idProduct": 2}]))

    with pytest.raises(ValueError, match="Only 2 products"):
        check_plausible(rows)


def test_check_plausible_refuses_duplicate_products(monkeypatch):
    monkeypatch.setattr(snapshot_prices, "MIN_EXPECTED_PRODUCTS", 1)
    rows = to_rows(make_guide(items=[{"idProduct": 1}, {"idProduct": 1}]))

    with pytest.raises(ValueError, match="more than once"):
        check_plausible(rows)
