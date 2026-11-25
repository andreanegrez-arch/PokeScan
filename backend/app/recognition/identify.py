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
5. Where the edition the code points to has fingerprints (artwork.py), the
   card's illustration is compared with its illustrations: a clear match
   confirms the number read, or corrects it (Raichu's 14/102 read as 4/102),
   unless the code was already confirmed by the set code or the name. Nothing
   read at all: the illustration is looked for in every edition, last.
6. Still more than one: 'ambiguous', and the user picks.
7. A card was found, or a code was read that no card in the catalog has (a
   Japanese or Chinese card, say): estimate its condition from the same photo
   and, if given, a photo of the back (see condition.py); for a card found,
   with its catalog picture at hand where the photo doesn't show the
   border's end all around (see reference.py). Nothing read: the photo is
   too poor to measure anything, and no estimate.
"""

from collections.abc import Callable
from functools import cache, partial

import numpy as np
import psycopg

from app import catalog
from app.recognition import artwork, condition, detect, reference, text
from app.schemas import CardSummary, CollectorCode, ConditionEstimate, Lang, ScanResult

MAX_CANDIDATES = 20


class _Illustration:
    """The photo's illustration, compared with the editions' only when asked, once each."""

    def __init__(self, image: np.ndarray, editions: list[artwork.Pool], chosen: bool):
        self.image, self.editions, self.chosen = image, editions, chosen
        self.seen: dict[tuple, tuple[np.ndarray | None, artwork.Match | None]] = {}

    def _card(self, turned: bool) -> np.ndarray | None:
        """The card straightened from the photo, for views that aren't (the whole photo)."""
        corners = detect.find_outline(self.image)
        card = (detect.straighten(self.image, corners) if corners is not None
                else detect.as_card(self.image))
        return None if card is None else detect.upside_down(card) if turned else card

    def match(self, view: np.ndarray | None, turned: bool,
              total: str | None) -> artwork.Match | None:
        """The illustration of the edition(s) of this size the card shows, if clearly one.

        `view`: the card as straightened to read its code; None to straighten it
        here. `total` None: search every edition (stricter), the last resort.
        An edition the user chose is searched whatever the size read.
        """
        if self.chosen:
            editions, size = self.editions, None
        elif total is None:
            editions, size = self.editions, None
        elif total.isdigit():
            size = int(total)
            editions = [pool for pool in self.editions if size in pool.totals]
        else:
            return None
        if not editions:
            return None
        key = (id(view), turned, size)
        if key not in self.seen:
            card = view if view is not None else self._card(turned)
            found = None
            if card is not None:
                strict = total is None and not self.chosen
                found = artwork.best_match(
                    card, editions, size,
                    artwork.MIN_INLIERS_ANYWHERE if strict else artwork.MIN_INLIERS)
            self.seen[key] = (view, found)  # the view is kept: its id stays its own
        return self.seen[key][1]


def _illustrated(
    conn: psycopg.Connection, seen: artwork.Match, read: CollectorCode | None, lang: Lang
) -> tuple[CollectorCode | None, list[CardSummary]]:
    """The cards the illustration matched (one, unless reprinted alike), and the
    code read, corrected to theirs."""
    ids = {p.card_id for p in seen.printings}
    printed = seen.printings[0]
    lookup = CollectorCode(raw=printed.number, set_code=None, number=printed.number,
                           total=str(printed.set_total) if printed.set_total else None)
    cards = [c for c in catalog.search_by_code(conn, lookup, lang, MAX_CANDIDATES)
             if c.card_id in ids]
    if read is None or not cards:
        return None, cards
    return read.model_copy(update={"number": cards[0].local_number,
                                   "set_code": cards[0].set_code,
                                   "total": lookup.total}), cards


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
    conn: psycopg.Connection, image: np.ndarray, lang: Lang, back: np.ndarray | None = None,
    pictures: bool = True, illustrations: bool = True, slash_lost: bool = True,
    set_id: str | None = None,
) -> ScanResult:
    """Identify the card in a photo (see detect.decode) and estimate its condition.

    `pictures`: the catalog's picture of a card found may be used for its
    centering (see reference.py). `illustrations`: compare the card's
    illustration with the editions' fingerprints (artwork.py). `slash_lost`:
    guess codes from digits read without their slash (text.slashless_codes).
    `set_id`: the edition, as the user chose it: its illustrations are
    searched whatever the set size read.
    """
    editions = artwork.pools() if illustrations else []
    if set_id is not None:
        editions = [pool for pool in editions if pool.set_id == set_id]
    illustration = _Illustration(image, editions, chosen=set_id is not None)

    def found(cards, code, turned) -> ScanResult:
        card = cards[0]
        picture = (partial(reference.picture, card.image_url, card.image_fallback_url)
                   if pictures and len(cards) == 1 else None)
        estimate = condition.assess(image, turned, back, lang, picture)
        if len(cards) == 1:
            return _result("matched", code, match=card, condition=estimate)
        return _result("ambiguous", code, candidates=cards, condition=estimate)

    # A code read, but no card has it, and which way up the card was then.
    first: tuple[CollectorCode, bool] | None = None
    for view, code_region, name_region, turned in _views(image):
        lines = text.read_lines(code_region(view))
        reading = text.read_code(lines, slash_lost=slash_lost)
        name_lines = _name_reader(view, name_region, lines)
        card_view = view if name_region else None  # else the whole photo was read
        for read in reading.codes:
            candidates = catalog.search_by_code(conn, read, lang, MAX_CANDIDATES)
            if not candidates:
                continue
            code, candidates, confirmed = _narrow(conn, read, candidates, reading, name_lines)
            if read.total is not None and not (confirmed and len(candidates) == 1):
                seen = illustration.match(card_view, turned, read.total)
                if seen is not None:
                    corrected, cards = _illustrated(conn, seen, read, lang)
                    if cards:
                        return found(cards, corrected, turned)
            if read in reading.guesses and not confirmed:
                continue
            return found(candidates, code, turned)
        if reading.codes and first is None:
            first = reading.codes[0], turned  # try the other views
    if first is None:
        # No code at all: the illustration alone, in every edition (or the one chosen).
        for turned in (False, True):
            seen = illustration.match(None, turned, None)
            if seen is not None:
                _, cards = _illustrated(conn, seen, None, lang)
                if cards:
                    return found(cards, None, turned)
        return _result("unreadable")
    code, turned = first
    # A number no card has: misread, if the illustration is one of that edition's.
    seen = illustration.match(None, turned, code.total) if code.total is not None else None
    if seen is not None:
        corrected, cards = _illustrated(conn, seen, code, lang)
        if cards:
            return found(cards, corrected, turned)
    # The card isn't in the catalog (a Chinese one, say), but the photo is
    # still good enough to read its code: measure its condition all the same.
    return _result("not_found", code, condition=condition.assess(image, turned, back, lang))
