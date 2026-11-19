from fastapi import APIRouter, HTTPException, Query

from app.schemas import CardDetail, Lang, PriceHistory, SearchResult, Variant

router = APIRouter(prefix="/cards", tags=["cards"])

NOT_FOUND = {404: {"description": "Card not found"}}


# Declared before /{card_id}, otherwise "search" would be read as a card id.
@router.get("/search", response_model=SearchResult)
def search_cards(
    q: str = Query(
        min_length=2,
        description="Card code ('SVI 025', '136/189') or name ('Pikachu')",
    ),
    lang: Lang = "it",
) -> SearchResult:
    """Manual search, the fallback when a scan can't read the code.

    A code is matched exactly (same rules as /scan); a name may match
    the same card in several sets, so all of them are returned.
    """
    # TODO: query sets + cards + card_translations
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.get("/{card_id}", response_model=CardDetail, responses=NOT_FOUND)
def get_card(card_id: str, lang: Lang = "it") -> CardDetail:
    """Card details with the latest Cardmarket prices."""
    # TODO: query cards + latest row of cardmarket_prices
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.get("/{card_id}/prices", response_model=PriceHistory, responses=NOT_FOUND)
def get_price_history(
    card_id: str,
    variant: Variant = "normal",
    days: int = Query(default=90, ge=1, le=365),
) -> PriceHistory:
    """Daily price history (trend price) for one variant."""
    # TODO: query cardmarket_prices
    raise HTTPException(status_code=501, detail="Not implemented yet")
