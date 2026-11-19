"""Tests of the TCGdex catalog import, without network.

TCGdex pages are replaced by small dicts shaped like the real ones. The
database tests use the throwaway database of conftest.py.
"""

from jobs.import_catalog import (
    build_catalog,
    card_details,
    cardmarket_id,
    import_card_details,
    save_catalog,
    variant_names,
)


def set_page(set_id, name, cards, serie="sv", official=198, abbreviation="SVI"):
    return {
        "id": set_id,
        "name": name,
        "logo": f"https://assets.tcgdex.net/{set_id}/logo",
        "symbol": f"https://assets.tcgdex.net/{set_id}/symbol",
        "releaseDate": "2023-03-31",
        "serie": {"id": serie, "name": serie.upper()},
        "abbreviation": {"official": abbreviation},
        "cardCount": {"official": official, "total": official + 60},
        "cards": [
            {"id": f"{set_id}-{number}", "localId": number, "name": card_name}
            for number, card_name in cards
        ],
    }


def card_page(card_id, id_product=None, from_variants=None, hp=60):
    return {
        "id": card_id,
        "category": "Pokemon",
        "rarity": "Common",
        "illustrator": "Somebody",
        "regulationMark": "G",
        "hp": hp,
        "stage": "Basic",
        "types": ["Grass"],
        "dexId": [1],
        "variants": {"normal": True, "reverse": True, "holo": False},
        "variants_detailed": [{"type": "normal", "thirdParty": {"cardmarket": from_variants}}],
        "pricing": {"cardmarket": {"idProduct": id_product}} if id_product else None,
    }


# ---------------------------------------------------------------------------
# Pages -> rows
# ---------------------------------------------------------------------------


def test_catalog_merges_languages_with_english_set_data_first():
    catalog = build_catalog(
        {
            ("sv01", "it"): set_page("sv01", "Scarlatto e Violetto", [("001", "Sprigatito")],
                                     official=999),
            ("sv01", "en"): set_page("sv01", "Scarlet & Violet", [("001", "Sprigatito")]),
        }
    )

    assert catalog.sets["sv01"][4] == 198  # official count from the English page
    assert sorted(catalog.set_translations) == [
        ("sv01", "en", "Scarlet & Violet", "https://assets.tcgdex.net/sv01/logo"),
        ("sv01", "it", "Scarlatto e Violetto", "https://assets.tcgdex.net/sv01/logo"),
    ]
    assert catalog.cards == {"sv01-001": ("sv01-001", "sv01", "001")}
    assert [(card, lang) for card, lang, *_ in catalog.card_translations] == [
        ("sv01-001", "en"),
        ("sv01-001", "it"),
    ]


def test_catalog_keeps_cards_found_only_in_italian():
    catalog = build_catalog(
        {
            ("sv01", "en"): set_page("sv01", "Scarlet & Violet", [("001", "Sprigatito")]),
            ("sv01", "it"): set_page("sv01", "Scarlatto e Violetto",
                                     [("001", "Sprigatito"), ("002", "Floragato")]),
        }
    )

    assert set(catalog.cards) == {"sv01-001", "sv01-002"}


def test_catalog_skips_pocket_sets_missing_pages_and_duplicate_numbers():
    pocket = set_page("A1", "Genetic Apex", [("001", "Bulbasaur")], serie="tcgp")
    same_number = set_page("sv01", "Scarlet & Violet", [("001", "Sprigatito")])
    same_number["cards"].append({"id": "sv01-001b", "localId": "001", "name": "Other"})

    catalog = build_catalog(
        {("A1", "en"): pocket, ("sv02", "en"): None, ("sv01", "en"): same_number}
    )

    assert set(catalog.sets) == {"sv01"}
    assert set(catalog.cards) == {"sv01-001"}
    assert catalog.duplicates == {"sv01-001b"}


def test_cardmarket_id_comes_from_prices_else_from_variants():
    assert cardmarket_id(card_page("sv01-001", id_product=702298)) == 702298
    assert cardmarket_id(card_page("swsh3-136", from_variants=483559)) == 483559
    assert cardmarket_id(card_page("base1-1")) is None


def test_card_details_keep_only_printed_variants():
    assert variant_names({"normal": True, "reverse": False, "holo": True}) == ["normal", "holo"]
    assert variant_names(None) == []

    details = card_details(card_page("sv01-001", id_product=702298, hp="60"))
    assert details[4] == 60  # hp as a number
    assert details[8:] == (["normal", "reverse"], 702298, "sv01-001")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class FakeTCGdex:
    """Serves card pages from a dict; records which pages were asked for."""

    def __init__(self, cards):
        self.cards = cards
        self.asked = []

    def get_many(self, paths):
        paths = list(paths)
        self.asked += paths
        return [self.cards.get(path.rsplit("/", 1)[1]) for path in paths]


def two_language_catalog():
    return build_catalog(
        {
            ("sv01", "en"): set_page("sv01", "Scarlet & Violet",
                                     [("001", "Sprigatito"), ("002", "Floragato")]),
            ("sv01", "it"): set_page("sv01", "Scarlatto e Violetto",
                                     [("001", "Sprigatito"), ("003", "Meowscarada")]),
        }
    )


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_saving_the_catalog_twice_updates_without_duplicating(db):
    save_catalog(db, two_language_catalog())
    catalog = two_language_catalog()
    catalog.card_translations[0] = ("sv01-001", "en", "Sprigatito (renamed)", None)
    save_catalog(db, catalog)
    db.commit()

    assert [count(db, t) for t in ("sets", "set_translations", "cards", "card_translations")] == [
        1, 2, 3, 4
    ]
    name = db.execute(
        "SELECT name FROM card_translations WHERE card_id = 'sv01-001' AND lang = 'en'"
    ).fetchone()[0]
    assert name == "Sprigatito (renamed)"


def test_card_pages_are_read_once_and_link_cards_to_cardmarket(db):
    save_catalog(db, two_language_catalog())
    db.commit()
    db.autocommit = True
    api = FakeTCGdex(
        {
            "sv01-001": card_page("sv01-001", id_product=702298),
            "sv01-002": card_page("sv01-002"),  # no Cardmarket id yet
            # sv01-003: no page (404)
        }
    )

    assert import_card_details(api, db, lambda done, total: None) == 3
    # The Italian-only card is read in Italian, the others in English.
    assert sorted(api.asked) == ["/en/cards/sv01-001", "/en/cards/sv01-002", "/it/cards/sv01-003"]
    assert db.execute(
        "SELECT card_id, cardmarket_id, variants FROM cards ORDER BY card_id"
    ).fetchall() == [
        ("sv01-001", 702298, ["normal", "reverse"]),
        ("sv01-002", None, ["normal", "reverse"]),
        ("sv01-003", None, None),
    ]

    # Next run: nothing new to read.
    assert import_card_details(api, db, lambda done, total: None) == 0

    # A week later, cards still without a Cardmarket id are read again.
    db.execute("UPDATE cards SET details_fetched_at = now() - interval '8 days'")
    api.cards["sv01-002"] = card_page("sv01-002", id_product=702299)
    assert import_card_details(api, db, lambda done, total: None) == 2
    assert db.execute(
        "SELECT cardmarket_id FROM cards WHERE card_id = 'sv01-002'"
    ).fetchone()[0] == 702299
