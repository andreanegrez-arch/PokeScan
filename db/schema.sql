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

-- One row per saved day, even when nothing changed: it tells
-- "prices unchanged that day" apart from "day not saved".
CREATE TABLE IF NOT EXISTS price_guide_runs (
    snapshot_date  DATE PRIMARY KEY,    -- day of the file's createdAt (Italian time)
    created_at     TIMESTAMPTZ NOT NULL,-- the file's createdAt
    products       INTEGER NOT NULL,    -- products listed in the file
    changed        INTEGER NOT NULL,    -- rows added for new products or changed prices
    removed        INTEGER NOT NULL,    -- rows added for products no longer listed
    saved_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cardmarket prices in EUR, stored only when something changes: a row holds
-- a product's prices from snapshot_date until the product's next row.
-- listed = false: the product disappeared from the price guide that day.
-- Keyed by Cardmarket's product id, so the history is saved even before the
-- catalog is imported. The *_holo columns are the reverse holo version.
CREATE TABLE IF NOT EXISTS cardmarket_prices (
    id_product     INTEGER NOT NULL,
    id_category    INTEGER,             -- Cardmarket category; 51 = single cards
    snapshot_date  DATE NOT NULL,       -- first day these values were seen
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
    listed         BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (id_product, snapshot_date)
);

-- Upgrade for databases created before the "listed" column existed.
ALTER TABLE cardmarket_prices ADD COLUMN IF NOT EXISTS listed BOOLEAN NOT NULL DEFAULT true;

-- The primary key already serves "history of one product";
-- this index serves "everything that changed on a given day".
CREATE INDEX IF NOT EXISTS cardmarket_prices_date_idx ON cardmarket_prices (snapshot_date);

-- Full price list of every saved day, rebuilt from the changes:
-- one row per listed product per day, as if every day had been stored in full.
CREATE OR REPLACE VIEW cardmarket_prices_daily AS
WITH versions AS (
    SELECT p.*,
           LEAD(p.snapshot_date) OVER (
               PARTITION BY p.id_product ORDER BY p.snapshot_date
           ) AS valid_until
    FROM cardmarket_prices p
)
SELECT r.snapshot_date AS day,
       v.id_product, v.id_category,
       v.avg, v.low, v.trend, v.avg1, v.avg7, v.avg30,
       v.avg_holo, v.low_holo, v.trend_holo, v.avg1_holo, v.avg7_holo, v.avg30_holo
FROM versions v
JOIN price_guide_runs r
  ON r.snapshot_date >= v.snapshot_date
 AND (v.valid_until IS NULL OR r.snapshot_date < v.valid_until)
WHERE v.listed;
