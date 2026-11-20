"""Tests of the /cards endpoints against the throwaway database of conftest.py,
filled with the tiny catalog of catalog_data.py.
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.db import get_conn
from app.main import app
from tests.catalog_data import IMAGES, seed


@pytest.fixture
def client(db):
    seed(db)
    app.dependency_overrides[get_conn] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def search(client, q, **params):
    response = client.get("/cards/search", params={"q": q, **params})
    assert response.status_code == 200
    return response.json()


def ids(result):
    return [card["card_id"] for card in result["results"]]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_by_set_code_and_number_returns_the_card_in_italian(client):
    result = search(client, "DAA 136")

    assert result["code"]["set_code"] == "DAA"
    assert result["results"] == [
        {
            "card_id": "swsh3-136",
            "name": "Furret",
            "set_name": "Oscurità Incandescente",
            "set_code": "DAA",
            "local_number": "136",
            "set_total": 189,
            "image_url": f"{IMAGES}/it/swsh3-136/low.webp",
            "image_fallback_url": f"{IMAGES}/en/swsh3-136/low.webp",
            "price_eur": 0.12,
        }
    ]


@pytest.mark.parametrize("q", ["136/189", "SVI 25", "svi 025/198", "SVI025"])
def test_search_by_code_ignores_zero_padding_and_spacing(client, q):
    expected = "swsh3-136" if q == "136/189" else "sv01-025"

    assert ids(search(client, q)) == [expected]


def test_a_code_shared_by_sets_of_the_same_size_returns_all_of_them_newest_first(client):
    assert ids(search(client, "58/102")) == ["old2-58", "old1-58"]


def test_promo_codes_are_found(client):
    assert ids(search(client, "SWSH020")) == ["swshp-SWSH020"]


def test_name_search_puts_exact_names_first_then_newest_sets(client):
    result = search(client, "pika")

    assert result["code"] is None
    assert ids(result) == ["sv01-025", "swshp-SWSH020", "old2-58", "old1-58"]


def test_name_search_matches_any_language_and_answers_in_the_requested_one(client):
    italian = search(client, "ricerca", lang="it")["results"]
    english = search(client, "ricerca", lang="en")["results"]

    assert [card["name"] for card in italian] == ["Ricerca Accademica"]
    assert [card["name"] for card in english] == ["Professor's Research"]
    assert english[0]["set_name"] == "Scarlet & Violet"


def test_cards_missing_in_the_requested_language_fall_back_to_another(client):
    result = search(client, "solo italiana", lang="en")["results"]

    assert [(card["name"], card["image_url"]) for card in result] == [
        ("Carta Solo Italiana", f"{IMAGES}/it/sv01-201/low.webp")
    ]


def test_search_treats_like_wildcards_as_plain_text(client):
    assert ids(search(client, "%%")) == []
    assert ids(search(client, "__")) == []


def test_unknown_code_or_name_returns_no_results(client):
    assert ids(search(client, "XYZ 999/999")) == []
    assert ids(search(client, "Mewtwo")) == []


def test_search_needs_at_least_two_characters(client):
    assert client.get("/cards/search", params={"q": "a"}).status_code == 422


# ---------------------------------------------------------------------------
# Card details
# ---------------------------------------------------------------------------


def test_card_details_have_the_latest_prices_of_both_variants(client):
    card = client.get("/cards/swsh3-136").json()

    assert card["image_url"] == f"{IMAGES}/it/swsh3-136/high.webp"
    # TCGdex lists a few images it doesn't have: the English one, if that fails.
    assert card["image_fallback_url"] == f"{IMAGES}/en/swsh3-136/high.webp"
    assert (card["set_code"], card["set_total"], card["release_date"]) == (
        "DAA", 189, "2020-08-14"
    )
    assert (card["rarity"], card["hp"], card["types"], card["variants"]) == (
        "Common", 70, ["Colorless"], ["normal", "reverse"]
    )
    assert card["prices"] == {
        "updated_at": "2026-11-19",
        "normal": {"trend": 0.12, "low": 0.12, "avg7": 0.12, "avg30": 0.12},
        "reverse": {"trend": 0.28, "low": None, "avg7": None, "avg30": None},
        "shared_with": [],
    }


def test_prices_say_when_another_card_has_the_same_cardmarket_product(client):
    card = client.get("/cards/old1-58").json()

    assert card["prices"]["shared_with"] == ["old2-58"]
    assert card["prices"]["reverse"] is None


def test_cards_no_longer_listed_or_without_product_have_no_prices(client):
    assert client.get("/cards/sv01-025").json()["prices"] is None
    assert client.get("/cards/sv01-200").json()["prices"] is None


def test_unknown_card_is_404(client):
    assert client.get("/cards/nope-1").status_code == 404
    assert client.get("/cards/nope-1/prices").status_code == 404


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------


def history(client, card_id, **params):
    response = client.get(f"/cards/{card_id}/prices", params=params)
    assert response.status_code == 200
    return [(point["date"], point["price"]) for point in response.json()["points"]]


def test_history_has_one_point_per_saved_day(client):
    assert history(client, "swsh3-136") == [
        ("2026-11-17", 0.10),
        ("2026-11-18", 0.10),  # unchanged day, rebuilt from the 17th
        ("2026-11-19", 0.12),
    ]
    assert history(client, "swsh3-136", variant="reverse") == [
        ("2026-11-17", 0.25),
        ("2026-11-18", 0.25),
        ("2026-11-19", 0.28),
    ]


def test_history_covers_the_requested_days_only(client):
    assert history(client, "swsh3-136", days=1) == [("2026-11-19", 0.12)]


def test_history_skips_days_without_price(client):
    assert history(client, "sv01-025") == [("2026-11-17", 1.50), ("2026-11-18", 1.50)]
    assert history(client, "sv01-200") == []


# ---------------------------------------------------------------------------
# Database down
# ---------------------------------------------------------------------------


def test_database_down_is_503():
    def unreachable():
        raise psycopg.OperationalError("connection refused")

    app.dependency_overrides[get_conn] = unreachable
    try:
        response = TestClient(app).get("/cards/swsh3-136")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Database not reachable"}
