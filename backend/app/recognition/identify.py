"""From a photo to a card: the scan behind POST /scan.

1. Find the card in the photo and straighten it.
2. Read the collector code in the bottom strip (if nothing is found, the card
   may be upside down: read it again turned around). No code in the card's
   outline: the photo may be cropped to the card, or the outline wrong; try
   those views too (see _views).
3. Look the code up in the catalog.
4. Several cards with that code (sets of the same size): keep those whose set
   code was printed next to the number, then those whose name is printed at
   the top of the card.
5. Still more than one: 'ambiguous', and the user picks.
6. A card was found: estimate its condition from the same photo and, if
   given, a photo of the back (see condition.py).
"""

import numpy as np
import psycopg

from app import catalog
from app.recognition import condition, detect, text
from app.schemas import CardSummary, CollectorCode, ConditionEstimate, Lang, ScanResult

MAX_CANDIDATES = 20


def by_set_code(candidates: list[CardSummary], hints: list[str]) -> list[CardSummary]:
    """Cards whose set code begins a word printed near the number ('SVIEN' -> SVI).

    The longest matching code wins. No match at all: the candidates are kept.
    """
    def matched_length(card: CardSummary) -> int:
        code = (card.set_code or "").upper()
        return len(code) if len(code) >= 2 and any(h.startswith(code) for h in hints) else 0

    best = max((matched_length(card) for card in candidates), default=0)
    if not best:
        return candidates
    return [card for card in candidates if matched_length(card) == best]


def by_name(
    candidates: list[CardSummary], names: dict[str, list[str]], lines: list[str]
) -> list[CardSummary]:
    """Cards whose name (in any language) appears in the text read on the card.

    Names read exactly beat names read with a few mistakes; then, when names
    contain each other ('Gardevoir', 'Gardevoir ex'), the longest wins.
    No name found: the candidates are kept.
    """
    found_text = " ".join(lines)

    def score(card: CardSummary) -> tuple[int, int]:
        found = [(text.find_name(n, found_text), len(text.normalize(n)))
                 for n in names.get(card.card_id, [])]
        return max((s for s in found if s[0] != text.ABSENT), default=(text.ABSENT, 0))

    best = max((score(card) for card in candidates), default=(text.ABSENT, 0))
    if best[0] == text.ABSENT:
        return candidates
    return [card for card in candidates if score(card) == best]


def _whole(view):
    return view


def _views(image):
    """Ways to look at the photo, most likely first:
    (image, code region, name region, turned upside down).

    Each is tried both ways up, until a collector code is read.
    """
    corners = detect.find_outline(image)
    cards = []
    if corners is not None:
        cards.append(detect.straighten(image, corners))
        # The outline may be a frame inside the card, leaving the code out.
        cards.append(detect.straighten(image, detect.enlarge(corners)))
    cropped = detect.as_card(image)  # a photo cropped to the card
    if cropped is not None:
        cards.append(cropped)
    for card in cards:
        yield card, detect.bottom_strip, detect.top_strip, False
        yield detect.upside_down(card), detect.bottom_strip, detect.top_strip, True
    # Last resort: read the whole photo.
    yield image, _whole, None, False
    yield detect.upside_down(image), _whole, None, True


def _look_up(
    conn: psycopg.Connection, codes: list[CollectorCode], lang: Lang
) -> tuple[CollectorCode | None, list[CardSummary]]:
    """The first of the possible codes that some card has, and those cards."""
    for code in codes:
        if candidates := catalog.search_by_code(conn, code, lang, MAX_CANDIDATES):
            return code, candidates
    return (codes[0] if codes else None), []


def _result(
    status, code=None, match=None, candidates=(), condition: ConditionEstimate | None = None
) -> ScanResult:
    return ScanResult(
        status=status, code=code, match=match, candidates=list(candidates), condition=condition
    )


def identify(
    conn: psycopg.Connection, image: np.ndarray, lang: Lang, back: np.ndarray | None = None
) -> ScanResult:
    """Identify the card in a photo (see detect.decode) and estimate its condition."""
    first_code: CollectorCode | None = None
    for view, code_region, name_region, turned in _views(image):
        lines = text.read_lines(code_region(view))
        reading = text.read_code(lines)
        code, candidates = _look_up(conn, reading.codes, lang)
        if candidates:
            break
        first_code = first_code or code  # read, but no card has it: try other views
    else:
        return _result("not_found", first_code) if first_code else _result("unreadable")

    with_set_code = by_set_code(candidates, reading.set_hints)
    if with_set_code is not candidates:  # the set code was read too
        code = code.model_copy(update={"set_code": with_set_code[0].set_code})
        candidates = with_set_code
    if len(candidates) > 1:
        name_lines = text.read_lines(name_region(view)) if name_region else lines
        names = catalog.names_of(conn, [c.card_id for c in candidates])
        candidates = by_name(candidates, names, name_lines)

    estimate = condition.assess(image, turned, back, lang)
    if len(candidates) == 1:
        return _result("matched", code, match=candidates[0], condition=estimate)
    return _result("ambiguous", code, candidates=candidates, condition=estimate)
