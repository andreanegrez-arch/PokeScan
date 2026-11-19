"""API contract: the shapes of every request parameter and response.

The frontend's TypeScript types are generated from these models
(via the OpenAPI schema), so changing a field here changes it there too.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

Lang = Literal["it", "en"]

# Cardmarket prices every card in two versions: the regular printing
# (which for holo rares is itself holo) and the reverse holo.
Variant = Literal["normal", "reverse"]

ScanStatus = Literal["matched", "ambiguous", "unreadable", "not_found"]

# Cardmarket's condition scale.
Condition = Literal[
    "mint", "near_mint", "excellent", "good", "light_played", "played", "poor"
]


class CardBase(BaseModel):
    card_id: str = Field(description="TCGdex card id", examples=["swsh3-136"])
    name: str
    set_name: str
    local_number: str = Field(description="Number within the set", examples=["136"])
    image_url: str | None


class CardSummary(CardBase):
    price_eur: float | None = Field(
        description="Cardmarket trend price of the normal variant, in EUR"
    )


class SearchResult(BaseModel):
    results: list[CardSummary]


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


class CollectorCode(BaseModel):
    """The code printed at the bottom of the card, read with OCR."""

    raw: str = Field(description="Text as read", examples=["SVI 025/198"])
    set_code: str | None = Field(
        description="Set abbreviation; printed only on recent cards", examples=["SVI"]
    )
    number: str = Field(examples=["025"])
    total: str | None = Field(
        description="Official set size, printed after the slash", examples=["198"]
    )


class Centering(BaseModel):
    left_pct: float = Field(
        description="Left border as % of left + right borders (50 = perfect)",
        examples=[55.0],
    )
    top_pct: float = Field(
        description="Top border as % of top + bottom borders (50 = perfect)",
        examples=[48.0],
    )


class ConditionEstimate(BaseModel):
    """Indicative estimate from photos; not a professional grade."""

    estimate: Condition | None = Field(
        description="Indicative condition on Cardmarket's scale"
    )
    front_centering: Centering | None
    back_centering: Centering | None = Field(
        description="Null when no back photo was sent"
    )
    edge_whitening_pct: float | None = Field(
        description="% of the back border showing whitening; null without a back photo"
    )
    notes: list[str] = Field(
        description="Findings in plain words, e.g. 'whitening on the top-left corner'"
    )


class ScanResult(BaseModel):
    status: ScanStatus = Field(
        description=(
            "matched: one card identified from its code. "
            "ambiguous: the code fits several sets; the user picks from candidates. "
            "unreadable: no code could be read; ask for a sharper photo. "
            "not_found: the code was read but matches no known card."
        )
    )
    code: CollectorCode | None = Field(description="Null when status is unreadable")
    match: CardSummary | None = Field(description="Set only when status is matched")
    candidates: list[CardSummary] = Field(
        description="Cards sharing the code, when status is ambiguous"
    )
    condition: ConditionEstimate | None


# ---------------------------------------------------------------------------
# Card details and prices
# ---------------------------------------------------------------------------


class VariantPrices(BaseModel):
    """Cardmarket prices for one variant, in EUR."""

    trend: float | None
    low: float | None
    avg7: float | None
    avg30: float | None


class CardPrices(BaseModel):
    updated_at: dt.date
    normal: VariantPrices | None
    reverse: VariantPrices | None


class CardDetail(CardBase):
    rarity: str | None
    prices: CardPrices | None


class PricePoint(BaseModel):
    date: dt.date
    price: float


class PriceHistory(BaseModel):
    card_id: str
    variant: Variant
    points: list[PricePoint]
