from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app import catalog
from app.db import get_conn
from app.schemas import CardDetail, Lang, PriceHistory, SearchResult, Variant

router = APIRouter(prefix="/cards", tags=["cards"])

Connection = Annotated[psycopg.Connection, Depends(get_conn)]

NOT_FOUND = {404: {"description": "Card not found"}}


# Declared before /{card_id}, otherwise "search" would be read as a card id.
@router.get("/search", response_model=SearchResult)
def search_cards(
    conn: Connection,
    q: str = Query(
        min_length=2,
        max_length=60,
        description="Card code ('SVI 025', '136/189', 'SWSH020') or name ('Pikachu')",
    ),
    lang: Lang = "it",
    limit: int = Query(default=20, ge=1, le=100),
) -> SearchResult:
    """Manual search, the fallback when a scan can't read the code.

    A code matches exactly: number plus set code and/or official set size
    (leading zeros don't matter). Several cards may share a number and set
    size: all of them are returned, newest set first. When the query is not
    a code, or no card has that code, it is searched as a name, in Italian
    and English.
    """
    return catalog.search(conn, q, lang, limit)


@router.get("/{card_id}", response_model=CardDetail, responses=NOT_FOUND)
def get_card(card_id: str, conn: Connection, lang: Lang = "it") -> CardDetail:
    """Card details with the latest Cardmarket prices."""
    card = catalog.card_detail(conn, card_id, lang)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.get("/{card_id}/prices", response_model=PriceHistory, responses=NOT_FOUND)
def get_price_history(
    card_id: str,
    conn: Connection,
    variant: Variant = "normal",
    days: int = Query(default=90, ge=1, le=365),
) -> PriceHistory:
    """Daily price history (trend price) for one variant.

    One point per day saved by the daily price job, over the last `days`
    days. Days when the card had no price are missing.
    """
    points = catalog.price_history(conn, card_id, variant, days)
    if points is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return PriceHistory(card_id=card_id, variant=variant, points=points)
