-- PostgreSQL schema for PokeScan.
-- Safe to run more than once: existing tables and indexes are left as they are.
-- Catalog ids come from TCGdex (e.g. set 'swsh3', card 'swsh3-136');
-- prices come from Cardmarket's daily price guide.

-- ---------------------------------------------------------------------------
-- Catalog (imported from TCGdex)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sets (
    set_id          TEXT PRIMARY KEY,
    abbreviation    TEXT,               -- e.g. 'SVI'; printed on recent cards only
    official_count  INTEGER,            -- printed after the slash: 198 in '025/198'
    total_count     INTEGER,            -- including secret rares numbered above it
    symbol_url      TEXT,               -- set symbol, to tell apart sets of equal size
    release_date    DATE
);

-- Set names differ by language.
CREATE TABLE IF NOT EXISTS set_translations (
    set_id  TEXT NOT NULL REFERENCES sets (set_id),
    lang    TEXT NOT NULL,              -- 'it', 'en'
    name    TEXT NOT NULL,
    PRIMARY KEY (set_id, lang)
);

CREATE TABLE IF NOT EXISTS cards (
    card_id        TEXT PRIMARY KEY,
    set_id         TEXT NOT NULL REFERENCES sets (set_id),
    local_number   TEXT NOT NULL,       -- number within the set; may contain letters
    rarity         TEXT,                -- stored in English
    cardmarket_id  INTEGER,             -- Cardmarket idProduct: joins to cardmarket_prices
    UNIQUE (set_id, local_number)
);

CREATE INDEX IF NOT EXISTS cards_cardmarket_id_idx ON cards (cardmarket_id);

-- Card names and images differ by language (an Italian card is printed in Italian).
CREATE TABLE IF NOT EXISTS card_translations (
    card_id    TEXT NOT NULL REFERENCES cards (card_id),
    lang       TEXT NOT NULL,           -- 'it', 'en'
    name       TEXT NOT NULL,
    image_url  TEXT,
    PRIMARY KEY (card_id, lang)
);

-- ---------------------------------------------------------------------------
-- Price history (filled daily by backend/jobs/snapshot_prices.py)
-- ---------------------------------------------------------------------------

-- Copy of Cardmarket's daily price guide: one row per product and day, in EUR.
-- Keyed by Cardmarket's product id, so the history is saved even before the
-- catalog is imported. The *_holo columns are the reverse holo version.
CREATE TABLE IF NOT EXISTS cardmarket_prices (
    id_product     INTEGER NOT NULL,
    id_category    INTEGER,             -- Cardmarket category; 51 = single cards
    snapshot_date  DATE NOT NULL,       -- date of the file's createdAt, not of the job run
    avg            NUMERIC(10, 2),
    low            NUMERIC(10, 2),
    trend          NUMERIC(10, 2),
    avg1           NUMERIC(10, 2),
    avg7           NUMERIC(10, 2),
    avg30          NUMERIC(10, 2),
    avg_holo       NUMERIC(10, 2),
    low_holo       NUMERIC(10, 2),
    trend_holo     NUMERIC(10, 2),
    avg1_holo      NUMERIC(10, 2),
    avg7_holo      NUMERIC(10, 2),
    avg30_holo     NUMERIC(10, 2),
    PRIMARY KEY (id_product, snapshot_date)
);

-- The primary key already serves "history of one product";
-- this index serves "all prices on a given day" (analysis queries).
CREATE INDEX IF NOT EXISTS cardmarket_prices_date_idx ON cardmarket_prices (snapshot_date);
