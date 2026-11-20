"""Database queries behind the /cards endpoints.

Names, set names and images are given in the requested language, falling back
to English (and to Italian for the few cards TCGdex has only in Italian).
"""

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.codes import code_variants, parse_code, total_count
from app.schemas import (
    CardDetail,
    CardPrices,
    CardSummary,
    CollectorCode,
    Lang,
    PricePoint,
    SearchResult,
    Variant,
    VariantPrices,
)

# TCGdex stores image base URLs; these suffixes pick the file.
SMALL_IMAGE = "/low.webp"
LARGE_IMAGE = "/high.webp"

# Printed numbers and TCGdex numbers differ in zero padding ('025' vs '25',
# 'TG05' vs 'TG5' in some sets): compare them without leading zeros.
NUMBER_WITHOUT_ZEROS = r"regexp_replace(upper({}), '(^|[A-Z])0+([0-9])', '\1\2')"

# Display data of a card, joined to `cards c` and `sets s`.
CARD_DISPLAY_JOINS = """
CROSS JOIN LATERAL (
    SELECT t.name FROM card_translations t WHERE t.card_id = c.card_id
    ORDER BY t.lang = %(lang)s DESC, t.lang = 'en' DESC LIMIT 1
) card_name
CROSS JOIN LATERAL (
    SELECT t.name FROM set_translations t WHERE t.set_id = c.set_id
    ORDER BY t.lang = %(lang)s DESC, t.lang = 'en' DESC LIMIT 1
) set_name
LEFT JOIN LATERAL (
    SELECT t.image_url FROM card_translations t
    WHERE t.card_id = c.card_id AND t.image_url IS NOT NULL
    ORDER BY t.lang = %(lang)s DESC, t.lang = 'en' DESC LIMIT 1
) image ON true
-- The English image when the one above is another language's (see
-- image_fallback_url): TCGdex lists a few images it doesn't have.
LEFT JOIN LATERAL (
    SELECT t.image_url FROM card_translations t
    WHERE t.card_id = c.card_id AND t.lang = 'en' AND t.image_url IS NOT NULL
      AND t.image_url IS DISTINCT FROM image.image_url
) fallback ON true
-- Latest stored prices; only the changes are stored, so they are still valid.
LEFT JOIN LATERAL (
    SELECT * FROM cardmarket_prices p WHERE p.id_product = c.cardmarket_id
    ORDER BY p.snapshot_date DESC LIMIT 1
) price ON true
"""

CARD_DISPLAY_COLUMNS = """
    c.card_id, card_name.name, set_name.name AS set_name, s.abbreviation AS set_code,
    c.local_number, s.official_count AS set_total, image.image_url,
    fallback.image_url AS image_fallback_url
"""

# `found` lists the matching cards (card_id, rank), already limited.
SUMMARIES = f"""
WITH found AS ({{found}})
SELECT {CARD_DISPLAY_COLUMNS},
       CASE WHEN price.listed THEN price.trend::float8 END AS price_eur
FROM found
JOIN cards c ON c.card_id = found.card_id
JOIN sets s ON s.set_id = c.set_id
{CARD_DISPLAY_JOINS}
ORDER BY found.rank
"""

FIND_BY_CODE = f"""
SELECT c.card_id,
       row_number() OVER (ORDER BY s.release_date DESC NULLS LAST, c.card_id) AS rank
FROM cards c
JOIN sets s ON s.set_id = c.set_id
WHERE {NUMBER_WITHOUT_ZEROS.format("c.local_number")}
      = {NUMBER_WITHOUT_ZEROS.format("%(number)s::text")}
  AND (%(set_code)s::text IS NULL OR upper(s.abbreviation) = %(set_code)s::text)
  AND (%(total)s::int IS NULL OR s.official_count = %(total)s::int)
ORDER BY rank
LIMIT %(limit)s
"""

# A name may match in any language. Exact names first, then names starting
# with the text, then the rest; newest sets first.
FIND_BY_NAME = """
SELECT m.card_id,
       row_number() OVER (
           ORDER BY m.exact DESC, m.starts DESC, s.release_date DESC NULLS LAST, m.card_id
       ) AS rank
FROM (
    SELECT t.card_id,
           bool_or(lower(t.name) = lower(%(text)s)) AS exact,
           bool_or(t.name ILIKE %(starts_with)s) AS starts
    FROM card_translations t
    WHERE t.name ILIKE %(contains)s
    GROUP BY t.card_id
) m
JOIN cards c ON c.card_id = m.card_id
JOIN sets s ON s.set_id = c.set_id
ORDER BY rank
LIMIT %(limit)s
"""

CARD_DETAIL = f"""
SELECT {CARD_DISPLAY_COLUMNS},
       c.rarity, c.category, c.hp, c.types, c.illustrator, c.variants,
       s.release_date, c.cardmarket_id,
       price.listed,
       price.trend::float8, price.low::float8, price.avg7::float8, price.avg30::float8,
       price.trend_holo::float8, price.low_holo::float8,
       price.avg7_holo::float8, price.avg30_holo::float8,
       (SELECT max(snapshot_date) FROM price_guide_runs) AS updated_at,
       ARRAY(
           SELECT other.card_id FROM cards other
           WHERE other.cardmarket_id = c.cardmarket_id AND other.card_id <> c.card_id
           ORDER BY other.card_id
       ) AS shared_with
FROM cards c
JOIN sets s ON s.set_id = c.set_id
{CARD_DISPLAY_JOINS}
WHERE c.card_id = %(card_id)s
"""

# Filtering the daily view on a fixed product id lets PostgreSQL rebuild the
# history of that product only (a join on cards would rebuild every product).
PRICE_HISTORY = """
SELECT day AS date, {column}::float8 AS price
FROM cardmarket_prices_daily
WHERE id_product = %(id_product)s
  AND {column} IS NOT NULL
  AND day > (SELECT max(snapshot_date) FROM price_guide_runs) - %(days)s::int
ORDER BY day
"""
PRICE_COLUMNS: dict[Variant, str] = {"normal": "trend", "reverse": "trend_holo"}


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _image(base_url: str | None, size: str) -> str | None:
    return base_url + size if base_url else None


def _images(row: dict[str, Any], size: str) -> dict[str, str | None]:
    """The row's image and fallback image base URLs, as URLs of this size."""
    return {key: _image(row[key], size) for key in ("image_url", "image_fallback_url")}


def _summaries(
    conn: psycopg.Connection, found: str, params: dict[str, Any]
) -> list[CardSummary]:
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(SUMMARIES.format(found=found), params).fetchall()
    return [CardSummary(**{**row, **_images(row, SMALL_IMAGE)}) for row in rows]


def search_by_code(
    conn: psycopg.Connection, code: CollectorCode, lang: Lang, limit: int
) -> list[CardSummary]:
    """Cards with this code. 'SVI025' is read as typed first (a promo number),
    and only if no card has it, as set SVI + number 025."""
    for variant in code_variants(code):
        params = {
            "number": variant.number,
            "set_code": variant.set_code,
            "total": total_count(variant),
            "lang": lang,
            "limit": limit,
        }
        if results := _summaries(conn, FIND_BY_CODE, params):
            return results
    return []


def search_by_name(
    conn: psycopg.Connection, text: str, lang: Lang, limit: int
) -> list[CardSummary]:
    escaped = _escape_like(text.strip())
    params = {
        "text": text.strip(),
        "starts_with": f"{escaped}%",
        "contains": f"%{escaped}%",
        "lang": lang,
        "limit": limit,
    }
    return _summaries(conn, FIND_BY_NAME, params)


def search(conn: psycopg.Connection, query: str, lang: Lang, limit: int) -> SearchResult:
    """Search by card code when the query is one, else (or if nothing matches) by name."""
    code = parse_code(query)
    if code is not None:
        results = search_by_code(conn, code, lang, limit)
        if results:
            return SearchResult(code=code, results=results)
    return SearchResult(code=None, results=search_by_name(conn, query, lang, limit))


def names_of(conn: psycopg.Connection, card_ids: list[str]) -> dict[str, list[str]]:
    """Every name of each card (one per language)."""
    rows = conn.execute(
        "SELECT card_id, name FROM card_translations WHERE card_id = ANY(%s)", (card_ids,)
    ).fetchall()
    names: dict[str, list[str]] = {card_id: [] for card_id in card_ids}
    for card_id, name in rows:
        names[card_id].append(name)
    return names


def _variant_prices(row: dict[str, Any], suffix: str) -> VariantPrices | None:
    prices = {name: row[name + suffix] for name in ("trend", "low", "avg7", "avg30")}
    if all(value is None for value in prices.values()):
        return None
    return VariantPrices(**prices)


def card_detail(conn: psycopg.Connection, card_id: str, lang: Lang) -> CardDetail | None:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(CARD_DETAIL, {"card_id": card_id, "lang": lang}).fetchone()
    if row is None:
        return None

    prices = None
    if row["listed"] and row["updated_at"] is not None:
        prices = CardPrices(
            updated_at=row["updated_at"],
            normal=_variant_prices(row, ""),
            reverse=_variant_prices(row, "_holo"),
            shared_with=row["shared_with"],
        )
    return CardDetail(
        **{
            **row,
            **_images(row, LARGE_IMAGE),
            "types": row["types"] or [],
            "variants": row["variants"] or [],
            "prices": prices,
        }
    )


def price_history(
    conn: psycopg.Connection, card_id: str, variant: Variant, days: int
) -> list[PricePoint] | None:
    """Daily trend prices of the last `days` saved days; None if the card doesn't exist."""
    card = conn.execute(
        "SELECT cardmarket_id FROM cards WHERE card_id = %s", (card_id,)
    ).fetchone()
    if card is None:
        return None
    if card[0] is None:
        return []
    query = PRICE_HISTORY.format(column=PRICE_COLUMNS[variant])
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(query, {"id_product": card[0], "days": days}).fetchall()
    return [PricePoint(**row) for row in rows]
