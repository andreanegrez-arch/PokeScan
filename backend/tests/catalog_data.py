"""A tiny catalog with prices, shaped like the real one, for database tests."""

import datetime as dt

IMAGES = "https://assets.tcgdex.net"

SETS = [
    # set_id, abbreviation, official_count, release_date, names (en, it)
    ("sv01", "SVI", 198, "2023-03-31", ("Scarlet & Violet", "Scarlatto e Violetto")),
    ("swsh3", "DAA", 189, "2020-08-14", ("Darkness Ablaze", "Oscurità Incandescente")),
    ("swsh6", "CRE", 198, "2021-06-18", ("Chilling Reign", "Regno Glaciale")),  # 198 as sv01
    ("swshp", None, None, "2019-11-15", ("SWSH Black Star Promos", None)),
    ("old2", None, 102, "2001-06-01", ("Old Set Two", None)),
    ("old1", None, 102, "2000-01-01", ("Old Set One", None)),
]
CARDS = [
    # card_id, local_number, cardmarket_id, names (en, it)
    ("swsh3-136", "136", 483559, ("Furret", "Furret")),
    ("sv01-025", "025", 700025, ("Pikachu", "Pikachu")),
    ("swsh6-025", "025", 488025, ("Snom", "Snom")),  # same code as sv01-025: 025/198
    ("sv01-200", "200", None, ("Professor's Research", "Ricerca Accademica")),
    ("sv01-201", "201", None, (None, "Carta Solo Italiana")),
    ("swshp-SWSH020", "SWSH020", 400020, ("Pikachu", None)),
    ("old1-58", "58", 600058, ("Pikachu", None)),
    ("old2-58", "58", 600058, ("Pikachu", None)),  # same product: a catalog mistake
]
RUN_DAYS = [17, 18, 19]  # November 2026
PRICES = [
    # id_product, day, listed, trend, trend_holo
    (483559, 17, True, 0.10, 0.25),
    (483559, 19, True, 0.12, 0.28),
    (700025, 17, True, 1.50, None),
    (700025, 19, False, None, None),  # no longer listed
    (600058, 17, True, 2.00, None),
    (400020, 18, True, 3.00, None),
]


def nov(day):
    return dt.date(2026, 11, day)


def seed(conn):
    for set_id, abbreviation, official, release, names in SETS:
        conn.execute(
            "INSERT INTO sets (set_id, abbreviation, official_count, release_date)"
            " VALUES (%s, %s, %s, %s)",
            (set_id, abbreviation, official, release),
        )
        for lang, name in zip(("en", "it"), names):
            if name:
                conn.execute(
                    "INSERT INTO set_translations (set_id, lang, name) VALUES (%s, %s, %s)",
                    (set_id, lang, name),
                )
    for card_id, number, cardmarket_id, names in CARDS:
        set_id = card_id.split("-")[0]
        conn.execute(
            "INSERT INTO cards (card_id, set_id, local_number, rarity, category, hp, types,"
            " variants, cardmarket_id) VALUES (%s, %s, %s, 'Common', 'Pokemon', 70,"
            " '{Colorless}', '{normal,reverse}', %s)",
            (card_id, set_id, number, cardmarket_id),
        )
        for lang, name in zip(("en", "it"), names):
            if name:
                conn.execute(
                    "INSERT INTO card_translations (card_id, lang, name, image_url)"
                    " VALUES (%s, %s, %s, %s)",
                    (card_id, lang, name, f"{IMAGES}/{lang}/{card_id}"),
                )
    for day in RUN_DAYS:
        conn.execute(
            "INSERT INTO price_guide_runs (snapshot_date, created_at, products, changed, removed)"
            " VALUES (%s, %s, 4, 0, 0)",
            (nov(day), dt.datetime(2026, 11, day, 2, 48, tzinfo=dt.UTC)),
        )
    for product, day, listed, trend, trend_holo in PRICES:
        conn.execute(
            "INSERT INTO cardmarket_prices (id_product, id_category, snapshot_date, listed,"
            " trend, low, avg7, avg30, trend_holo) VALUES (%s, 51, %s, %s, %s, %s, %s, %s, %s)",
            (product, nov(day), listed, trend, trend, trend, trend, trend_holo),
        )
    conn.commit()
