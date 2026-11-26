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
   read at all: the illustration is looked for in every edition, last. So it
   is too when the code was another number on the card read as one: no card
   has it, or the cards that have it have illustrations of their own, and
   none is the photo's. Found nowhere: as before, the code read stands.
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

    def __init__(self, image: np.ndarray, artworks: list[artwork.Artwork], set_id: str | None):
        self.image, self.artworks, self.set_id = image, artworks, set_id
        self.seen: dict[tuple, tuple[np.ndarray | None, artwork.Match | None]] = {}

    def _card(self, turned: bool) -> np.ndarray | None:
        """The card straightened from the photo, for views that aren't (the whole photo)."""
        corners = detect.find_outline(self.image)
        card = (detect.straighten(self.image, corners) if corners is not None
                else detect.as_card(self.image))
        return None if card is None else detect.upside_down(card) if turned else card

    def match(self, view: np.ndarray | None, turned: bool,
              total: str | None) -> artwork.Match | None:
        """The illustration printed with this set size the card shows, if clearly one.

        Several editions may share the size (/102: Base Set and Triumphant):
        all their illustrations are compared. `view`: the card as straightened
        to read its code; None to straighten it here. `total` None: compare
        every edition's (stricter), the last resort. In an edition the user
        chose, its illustrations are compared whatever the size read.
        """
        if self.set_id is not None:
            chosen = self.set_id
            keep, total, strict = (lambda p: p.set_id == chosen), None, False
        elif total is None:
            keep, strict = (lambda p: True), True
        else:
            size = total
            keep, strict = (lambda p: artwork.same_number(p.set_total, size)), False
        key = (id(view), turned, total)
        if key not in self.seen:
            found = None
            if any(keep(p) for art in self.artworks for p in art.printings):
                card = view if view is not None else self._card(turned)
                if card is not None:
                    found = artwork.best_match(
                        card, self.artworks, keep,
                        artwork.MIN_INLIERS_ANYWHERE if strict else artwork.MIN_INLIERS)
            self.seen[key] = (view, found)  # the view is kept: its id stays its own
        return self.seen[key][1]

    def fingerprinted(self, cards: list[CardSummary]) -> bool:
        """Whether any of these cards' illustrations has a fingerprint to compare."""
        ids = {card.card_id for card in cards}
        return any(p.card_id in ids for art in self.artworks for p in art.printings)


def _illustrated(
    conn: psycopg.Connection, seen: artwork.Match, read: CollectorCode | None, lang: Lang
) -> tuple[CollectorCode | None, list[CardSummary]]:
    """The cards the illustration matched, and the code read, corrected to theirs.

    An illustration printed more than once with the size read (in two editions
    of that size, say) is the printing whose number was read; if none was,
    the user picks among them.
    """
    printings = seen.printings
    if read is not None:
        printings = [p for p in printings if artwork.same_number(p.number, read.number)]             or printings
    totals = {p.card_id: p.set_total for p in printings}
    cards = catalog.summaries_of(conn, list(totals), lang)
    if read is None or not cards:
        return None, cards
    return read.model_copy(update={"number": cards[0].local_number,
                                   "set_code": cards[0].set_code,
                                   "total": totals[cards[0].card_id]}), cards


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
    illustration = _Illustration(image, artwork.library() if illustrations else [], set_id)

    def found(cards, code, turned) -> ScanResult:
        card = cards[0]
        picture = (partial(reference.picture, card.image_url, card.image_fallback_url)
                   if pictures and len(cards) == 1 else None)
        estimate = condition.assess(image, turned, back, lang, picture)
        if len(cards) == 1:
            return _result("matched", code, match=card, condition=estimate)
        return _result("ambiguous", code, candidates=cards, condition=estimate)

    def anywhere(view, turned) -> ScanResult | None:
        """The card found by its illustration alone, in every edition (or the
        one chosen): the way up given first, then the other."""
        for way_up, card_view in ((turned, view), (not turned, None)):
            seen = illustration.match(card_view, way_up, None)
            if seen is not None:
                _, cards = _illustrated(conn, seen, None, lang)
                if cards:
                    return found(cards, None, way_up)
        return None

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
            doubted = False  # their illustrations compared, and none is the photo's
            if read.total is not None and not (confirmed and len(candidates) == 1):
                seen = illustration.match(card_view, turned, read.total)
                if seen is not None:
                    corrected, cards = _illustrated(conn, seen, read, lang)
                    if cards:
                        return found(cards, corrected, turned)
                doubted = not confirmed and illustration.fingerprinted(candidates)
            if read in reading.guesses and not confirmed:
                continue
            if doubted:
                # Another number on the card, read as its code? (An attack's
                # damage, say.) A card the illustration alone finds wins.
                result = anywhere(card_view, turned)
                if result is not None:
                    return result
            return found(candidates, code, turned)
        if reading.codes and first is None:
            first = reading.codes[0], turned  # try the other views
    if first is None:
        # No code at all: the illustration alone.
        return anywhere(None, False) or _result("unreadable")
    code, turned = first
    # A number no card has: misread, if the illustration is one of that edition's.
    seen = illustration.match(None, turned, code.total) if code.total is not None else None
    if seen is not None:
        corrected, cards = _illustrated(conn, seen, code, lang)
        if cards:
            return found(cards, corrected, turned)
    # Or another number on the card, read as its code: the illustration alone.
    result = anywhere(None, turned)
    if result is not None:
        return result
    # The card isn't in the catalog (a Chinese one, say), but the photo is
    # still good enough to read its code: measure its condition all the same.
    return _result("not_found", code, condition=condition.assess(image, turned, back, lang))
