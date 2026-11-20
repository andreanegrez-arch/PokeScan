"""From a photo to a card: the scan behind POST /scan.

1. Find the card in the photo and straighten it.
2. Read the collector code in the bottom strip (if nothing is found, the card
   may be upside down: read it again turned around). No code in the card's
   outline: the photo may be cropped to the card, or the outline wrong; try
   those views too (see _views).
3. Look the code up in the catalog.
4. Several cards with that code (sets of the same size): keep those whose set
   code was printed next to the number, then those whose name is printed at
   the top of the card. A code corrected from what was read ('161/5' tried as
   '1/5') is kept only if one of the two agrees: else it's just a misreading
   that happens to be some card's code.
5. Still more than one: 'ambiguous', and the user picks.
6. A card was found: estimate its condition from the same photo and, if
   given, a photo of the back (see condition.py).
"""

from collections.abc import Callable
from functools import cache

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


def _name_reader(view, name_region, code_lines: list[str]) -> Callable[[], list[str]]:
    """The text where the name is printed, read once and only if asked for.

    Without a name region (the whole photo was read), the lines read already.
    """
    @cache
    def read() -> list[str]:
        return text.read_lines(name_region(view)) if name_region else code_lines

    return read


def _narrow(
    conn: psycopg.Connection,
    code: CollectorCode,
    candidates: list[CardSummary],
    reading: text.CodeReading,
    name_lines: Callable[[], list[str]],
) -> tuple[CollectorCode, list[CardSummary], bool]:
    """The candidates the set code and the name leave, and whether either matched."""
    confirmed = False
    with_set_code = by_set_code(candidates, reading.set_hints)
    if with_set_code is not candidates:  # the set code was read too
        code = code.model_copy(update={"set_code": with_set_code[0].set_code})
        candidates, confirmed = with_set_code, True
    if len(candidates) > 1 or (code in reading.guesses and not confirmed):
        names = catalog.names_of(conn, [c.card_id for c in candidates])
        with_name = by_name(candidates, names, name_lines())
        if with_name is not candidates:  # a name was read
            candidates, confirmed = with_name, True
    return code, candidates, confirmed


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
    first_code: CollectorCode | None = None  # read, but no card has it
    for view, code_region, name_region, turned in _views(image):
        lines = text.read_lines(code_region(view))
        reading = text.read_code(lines)
        name_lines = _name_reader(view, name_region, lines)
        for read in reading.codes:
            candidates = catalog.search_by_code(conn, read, lang, MAX_CANDIDATES)
            if not candidates:
                continue
            code, candidates, confirmed = _narrow(conn, read, candidates, reading, name_lines)
            if read in reading.guesses and not confirmed:
                continue
            estimate = condition.assess(image, turned, back, lang)
            if len(candidates) == 1:
                return _result("matched", code, match=candidates[0], condition=estimate)
            return _result("ambiguous", code, candidates=candidates, condition=estimate)
        if reading.codes:
            first_code = first_code or reading.codes[0]  # try the other views
    return _result("not_found", first_code) if first_code else _result("unreadable")
