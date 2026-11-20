"""Indicative condition from photos: centering and whitening on the edges.

Centering: the width of the printed border (yellow, silver or, on the back,
dark blue) on each side. find_outline's corners are only roughly right, so
first the card's four edges are found in a wide view around it and the
corners are put where they meet. Of the edges found on a side, the card's is
the one alike to those on the other sides (see _card_edges): the background
can have edges of its own near the card, like a keyboard. The card is then
straightened from those corners with some background around it (added in
the card's plane, so that a photo taken at an angle keeps it centered), and
on each side the border is measured from the card's edge (where the color
changes along most of the side) to where the border's color stops for good
(text printed on the border, like the copyright line, is thinner); where a
holo border's color drifts on one side, to the next edge there instead (see
_border_ends). Borders that don't look like one printed border are
discarded: see _looks_like_one_border.

Where the border's inner edge doesn't show on every side (a silver border
around a silver frame, lit so that the two look alike), it can be carried
over from the catalog's picture of the card: see borders_by_reference. A
dark blue back on a black table can have a card edge too faint for STEP:
it is looked for again with the BACK search.

Whitening: the back's dark blue border turns white where the edges and
corners are worn. Measured on a thin band just inside the card's edge,
following its rounded corners, for each corner and side.

The grade combines the two with PokeScan's own thresholds (GRADES): an
indicative estimate, not a professional grade. Glare, or a white table
showing through, can look like whitening.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np

from app.recognition import detect
from app.schemas import Centering, Condition, ConditionEstimate, Lang

SIDES = ("left", "right", "top", "bottom")

# Views of the card used to find its edges, each straightened from the corners
# found in the one before: background around the card (see _grown),
# how far from where it is expected an edge may be (share of the width), and
# the slopes it may have.
REFINING = [
    (1.25, 0.08, np.linspace(-0.15, 0.15, 16)),  # find_outline may be a few degrees off
    (1.12, 0.03, np.linspace(-0.03, 0.03, 13)),
]
MARGIN = 1.08  # final view, where the borders are measured
FINE_TILTS = np.linspace(-0.01, 0.01, 5)

# A color change across STEP px of at least EDGE_CHANGE (Lab units) is an
# edge where it covers EDGE_COVERAGE of the side's length.
STEP = 3
EDGE_CHANGE = 12.0
EDGE_COVERAGE = 0.6
LINES = 100  # lines across each side, between 15% and 85% of its length
RUN = 20  # pixels an edge's change may extend past where it is looked for

# Printed borders are about 3 mm of 63: their inner edge is looked for between
# these shares of the card's width.
BORDER_RANGE = (0.012, 0.1)
# The border ends where the color stops being the border's for at least this
# share of the width: text printed on the border (the copyright line) is
# thinner, and the border's color comes back after it.
PERSIST = 0.018
# The border's end found by color and as an edge are the same within this
# share of the width on a plain border (see _border_ends).
AGREE = 0.006
# The border is as wide on every side: off-center printing moves it, so
# left + right and top + bottom stay the same. Further apart than this, an
# inner edge was missed (e.g. a strip of text along the bottom).
SAME_TOTAL = 0.2
# The border is printed in one color: its four sides may differ in
# brightness (uneven light), much less in hue (a and b in Lab).
SAME_COLOR = 40.0

CARD_MM = (63.0, 88.0)  # width, height of a Pokémon card
CORNER_RADIUS = 3 / 63  # Pokémon cards have 3 mm corners, 63 mm wide

# A back's dark blue border on a black table, in dim light: its edge can be
# a change too faint and blurred to show across STEP pixels. It is looked for
# across SOFT_STEP pixels then, keeping only edges with the back's blue
# inside: b of at most BLUE_B in OpenCV's Lab (128 is neutral, lower is bluer).
SOFT_STEP = 6
BLUE_B = 120

# The catalog's picture of the card (see borders_by_reference). It is clean:
# fainter changes are still lines (REFERENCE_CHANGE). The photo is aligned
# to it on points found in both (SIFT, kept by Lowe's ALIGN_RATIO): at least
# MIN_ALIGNED of them must agree on one perspective, within MAX_ALIGN_ERROR
# px (median). The line carried over must lie where the photo shows it, on
# the sides where it shows, within CARRIED_AGREE_MM.
REFERENCE_CHANGE = 6.0
ALIGN_RATIO = 0.75
MIN_ALIGNED = 150
MAX_ALIGN_ERROR = 0.8
CARRIED_AGREE_MM = 0.15
BAND = (2, 12)  # whitening band: pixels this far inside the card's edge (about 1 mm)
BACKGROUND = (4, 10)  # background strip: pixels this far outside the card's edge


@dataclass
class Grade:
    """The worst measures a grade allows: PokeScan's own thresholds."""

    name: Condition
    front: float  # worse side's centering on the front: 60 means 60/40
    back: float  # same, on the back
    worst_region: float  # whitening, % of the band of the most worn corner or edge
    whitening: float  # whitening, % of the whole band


GRADES = [
    Grade("mint", 55, 60, 1, 0.5),
    Grade("near_mint", 60, 70, 6, 1.5),
    Grade("excellent", 70, 80, 20, 4),
    Grade("good", 100, 100, 100, 10),
    Grade("light_played", 100, 100, 100, 20),
    Grade("played", 100, 100, 100, 35),
]


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


@dataclass
class Edge:
    depth: float  # pixels from the image's side, at the middle of the card's side
    tilt: float  # change of depth per pixel along the side
    end: int  # last depth where the change still covers the side
    outside: np.ndarray  # Lab color just before the change
    inside: np.ndarray  # Lab color just past the change

    @property
    def lighter(self) -> int:
        """1 if it's lighter inside the edge, -1 if darker; 0 if about as
        light, or if the edge is mostly a change of hue: then lightness
        depends on the light (a dark blue border on a black table)."""
        change = float(self.inside[0] - self.outside[0])
        hue_change = float(np.linalg.norm(self.inside[1:] - self.outside[1:]))
        if abs(change) < max(EDGE_CHANGE, hue_change):
            return 0
        return 1 if change > 0 else -1


@dataclass(frozen=True)
class Search:
    """How edges are looked for: a change of color across `step` pixels of
    at least `change` (Lab). `card_inside`, if given: whether a color (Lab)
    can be the card's just inside its edge."""

    step: int = STEP
    change: float = EDGE_CHANGE
    card_inside: Callable[[np.ndarray], bool] | None = None

    def is_card_edge(self, edge: Edge) -> bool:
        return self.card_inside is None or self.card_inside(edge.inside)


def _back_blue(color: np.ndarray) -> bool:
    return bool(color[2] <= BLUE_B)


PLAIN = Search()
BACK = Search(step=SOFT_STEP, card_inside=_back_blue)  # see SOFT_STEP
REFERENCE = Search(change=REFERENCE_CHANGE)  # the catalog's clean picture


def _lab(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(cv2.GaussianBlur(image, (5, 5), 0), cv2.COLOR_BGR2LAB)


@dataclass
class Lines:
    """Lines across one side of the image, outer end first, avoiding the corners."""

    colors: np.ndarray  # (lines, depth, 3), Lab
    changed: np.ndarray  # (lines, depth - step): the color changes there
    along: np.ndarray  # each line's position along the side
    step: int  # see Search
    change: float

    def color_at(self, depth: float, tilt: float) -> np.ndarray:
        """Median color of the lines at this depth (at the middle of the side)."""
        offset = self.along - self.along.mean()
        positions = np.rint(depth + tilt * offset).astype(np.intp)
        positions = np.clip(positions, 0, self.colors.shape[1] - 1)
        return np.median(self.colors[np.arange(len(positions)), positions], axis=0)


def _lines(lab: np.ndarray, side: str, search: Search = PLAIN) -> Lines:
    height, width = lab.shape[:2]
    depth = int(0.25 * width)
    length = height if side in ("left", "right") else width
    along = np.linspace(0.15 * length, 0.85 * length, LINES).astype(int)
    if side == "left":
        lines = lab[along, :depth]
    elif side == "right":
        lines = lab[along, width - depth :][:, ::-1]
    elif side == "top":
        lines = lab[:depth, along].transpose(1, 0, 2)
    else:
        lines = lab[height - depth :, along][::-1].transpose(1, 0, 2)
    lines = lines.astype(np.float32)
    step = search.step
    change = np.linalg.norm(lines[:, step:] - lines[:, :-step], axis=2)
    return Lines(lines, change > search.change, along, step, search.change)


def _coverage(
    changed: np.ndarray, along: np.ndarray, tilts: np.ndarray, depths: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """For each depth (at the middle of the side), the share of lines that
    change there along the best-fitting slope, and that slope."""
    count, depth = changed.shape
    offset = along - along.mean()
    positions = np.rint(
        depths[None, None, :] + tilts[:, None, None] * offset[None, :, None]
    ).astype(np.intp)
    inside = (positions >= 0) & (positions < depth)
    hits = changed[np.arange(count)[None, :, None], np.clip(positions, 0, depth - 1)] & inside
    coverage = hits.mean(axis=1)  # (tilts, depths)
    return coverage.max(axis=0), tilts[coverage.argmax(axis=0)]


def _edges(lines: Lines, tilts: np.ndarray, start: float, stop: float) -> Iterator[Edge]:
    """The edges whose change starts between start and stop, from outside in.

    A change with the same color on both sides is a line printed on the
    border (text), not where it ends: it is skipped.
    """
    start, stop = max(0, int(start)), min(lines.changed.shape[1], int(stop))
    if start >= stop:
        return
    depths = np.arange(start, min(lines.changed.shape[1], stop + RUN))
    coverage, slopes = _coverage(lines.changed, lines.along, tilts, depths)
    first = 0
    while True:
        above = np.flatnonzero(coverage[first : stop - start] >= EDGE_COVERAGE)
        if not len(above):
            return
        first = end = first + int(above[0])
        while end + 1 < len(coverage) and coverage[end + 1] >= EDGE_COVERAGE:
            end += 1
        run = coverage[first : end + 1]
        peak = first + int(run.argmax())
        tilt = float(slopes[peak])
        before = lines.color_at(start + first - 1, tilt)
        after = lines.color_at(start + end + lines.step + 1, tilt)
        if np.linalg.norm(after - before) >= lines.change:
            strongest = first + np.flatnonzero(run >= run.max() - 0.05)
            depth = start + float(strongest.mean()) + lines.step / 2
            yield Edge(depth, tilt, start + end, before, after)
        first = end + 1


def _first_edge(lines: Lines, tilts: np.ndarray, start: float, stop: float) -> Edge | None:
    """The first edge from outside whose change starts between start and stop."""
    return next(_edges(lines, tilts, start, stop), None)


def _first_card_edge(
    lines: Lines, search: Search, start: float, stop: float
) -> Edge | None:
    """The first edge between start and stop that can be the card's (see Search)."""
    return next((e for e in _edges(lines, FINE_TILTS, start, stop) if search.is_card_edge(e)),
                None)


def _alike(edge: Edge, other: Edge) -> bool:
    """Edges between the same two things: the same hue inside (within
    SAME_COLOR), and not lighter inside one and darker inside the other.

    Lightness itself isn't compared: one side of the card may be in shadow,
    and a silver border looks lighter or darker with the angle of the light.
    """
    same_hue = np.linalg.norm(edge.inside[1:] - other.inside[1:]) <= SAME_COLOR
    return bool(same_hue and edge.lighter * other.lighter >= 0)


def _card_edges(candidates: dict[str, list[Edge]]) -> dict[str, Edge]:
    """On each side, the card's edge among the edges found, from outside in.

    Things in the background can have edges outside the card's, like a
    keyboard above it or a pattern below it. The card's edge, though, is
    alike on all four sides (see _alike): from the table to the border. So
    the edge alike to edges on the most sides is taken as a model (on ties,
    the one whose alike edges are the furthest out), and on each side the
    first edge alike to it is the card's. None alike on a side: the first
    one from outside.
    """
    def first_alike(edges: list[Edge], model: Edge) -> int | None:
        return next((i for i, e in enumerate(edges) if _alike(e, model)), None)

    def support(model: Edge) -> tuple[int, int]:
        found = [i for edges in candidates.values()
                 if (i := first_alike(edges, model)) is not None]
        return len(found), -sum(found)

    model = max((edge for edges in candidates.values() for edge in edges), key=support)
    return {side: edges[first_alike(edges, model) or 0] for side, edges in candidates.items()}


def _side_size(side: str, shape: tuple[int, ...]) -> int:
    return shape[1] if side in ("left", "right") else shape[0]


def _point(side: str, depth: float, along: float, shape: tuple[int, ...]) -> tuple[float, float]:
    height, width = shape[:2]
    if side == "left":
        return depth, along
    if side == "right":
        return width - 1 - depth, along
    if side == "top":
        return along, depth
    return along, height - 1 - depth


def _line(side: str, edge: Edge, middle: float, shape: tuple[int, ...]) -> np.ndarray:
    """The edge as a line in homogeneous coordinates."""
    a = _point(side, edge.depth - 100 * edge.tilt, middle - 100, shape)
    b = _point(side, edge.depth + 100 * edge.tilt, middle + 100, shape)
    return np.cross([*a, 1.0], [*b, 1.0])


def _grown(corners: np.ndarray, margin: float) -> np.ndarray:
    """The outline grown `margin` times in the card's own plane.

    Straightened, the card is then in the middle of the view with as much
    background on every side. Grown in the photo instead (detect.enlarge),
    a card photographed at an angle ends up off center: its far side, smaller
    in the photo, gets less background than its near side.
    """
    width, height = detect.CARD_SIZE
    dx, dy = (margin - 1) / 2 * (width - 1), (margin - 1) / 2 * (height - 1)
    grown = np.array([[[-dx, -dy], [width - 1 + dx, -dy],
                       [width - 1 + dx, height - 1 + dy], [-dx, height - 1 + dy]]], np.float32)
    to_photo = np.linalg.inv(detect.straightening(np.asarray(corners, np.float32)))
    return cv2.perspectiveTransform(grown, to_photo)[0]


def _warp(image: np.ndarray, corners: np.ndarray, margin: float) -> tuple[np.ndarray, np.ndarray]:
    """The card straightened with `margin` around it, and the transform used.

    Outside the photo, its edge pixels are repeated: a black fill would make
    an edge of its own.
    """
    matrix = detect.straightening(_grown(corners, margin))
    view = cv2.warpPerspective(image, matrix, detect.CARD_SIZE, borderMode=cv2.BORDER_REPLICATE)
    return view, matrix


def _refine(
    image: np.ndarray, corners: np.ndarray, margin: float, window: float, slopes: np.ndarray,
    search: Search,
) -> np.ndarray | None:
    view, matrix = _warp(image, corners, margin)
    lab = _lab(view)
    window *= view.shape[1]
    candidates, middles = {}, {}
    for side in SIDES:
        found = _lines(lab, side, search)
        expected = (1 - 1 / margin) / 2 * _side_size(side, view.shape)
        candidates[side] = [edge for edge in _edges(found, slopes, expected - window,
                                                     expected + window)
                            if search.is_card_edge(edge)]
        if not candidates[side]:
            return None
        middles[side] = float(found.along.mean())
    lines = {side: _line(side, edge, middles[side], view.shape)
             for side, edge in _card_edges(candidates).items()}

    meeting = []
    for a, b in (("top", "left"), ("top", "right"), ("bottom", "right"), ("bottom", "left")):
        x, y, w = np.cross(lines[a], lines[b])
        if abs(w) < 1e-9:
            return None
        meeting.append((x / w, y / w))
    found = cv2.perspectiveTransform(np.array([meeting], np.float32), np.linalg.inv(matrix))
    return found[0]


def refine_outline(
    image: np.ndarray, corners: np.ndarray, search: Search = PLAIN
) -> np.ndarray | None:
    """The card's corners where its four edges meet: more exact than find_outline's.

    None if an edge can't be found (e.g. a background the color of the border).
    """
    for margin, window, slopes in REFINING:
        corners = _refine(image, corners, margin, window, slopes, search)
        if corners is None:
            return None
    return corners


# ---------------------------------------------------------------------------
# Centering
# ---------------------------------------------------------------------------


@dataclass
class Borders:
    """Depths in the straightened image, in pixels from each side."""

    outer: dict[str, float]  # the card's edge
    inner: dict[str, float]  # the printed border's inner edge
    image_size: tuple[int, int] | None = None  # width, height of the image, if known

    def width(self, side: str) -> float:
        return self.inner[side] - self.outer[side]

    def px_per_mm(self) -> tuple[float, float] | None:
        """Pixels per mm across and down the card, from its size in the image."""
        if self.image_size is None:
            return None
        width, height = self.image_size
        across = width - self.outer["left"] - self.outer["right"]
        down = height - self.outer["top"] - self.outer["bottom"]
        return across / CARD_MM[0], down / CARD_MM[1]


@dataclass
class Sides:
    """What is found on each side of a straightened card: its edge, and where
    its border ends by color and as the next edge (see _border_ends)."""

    outer: dict[str, float]
    by_color: dict[str, float | None]
    by_edge: dict[str, float | None]

    def seen(self, agree: float) -> list[str]:
        """The sides where the border's end shows plainly: the same by color
        and as an edge, within `agree` pixels."""
        return [side for side in SIDES
                if (color := self.by_color[side]) is not None
                and (edge := self.by_edge[side]) is not None and abs(edge - color) <= agree]


def _sides(lab: np.ndarray, margin: float | None, search: Search = PLAIN) -> Sides | None:
    """The card's edge and the border's end on each side; None if an edge is missing.

    `margin`: the card was straightened with this much background around it
    (see _grown); None for a photo cropped to the card.
    """
    width = lab.shape[1]
    sides = Sides({}, {}, {})
    for side in SIDES:
        lines = _lines(lab, side, search)
        if margin:
            expected = (1 - 1 / margin) / 2 * _side_size(side, lab.shape)
            edge = _first_card_edge(lines, search, expected - 0.025 * width,
                                    expected + 0.025 * width)
            if edge is None:
                return None
            start, after, tilt = edge.depth, edge.end + 1, edge.tilt
        else:
            start, after, tilt = 0.0, 0, 0.0
        low, high = BORDER_RANGE
        begin, stop = max(after, start + low * width), start + high * width
        sides.outer[side] = start
        sides.by_color[side] = _border_end(lines, start, tilt, begin, stop,
                                           round(PERSIST * width))
        inner_edge = _first_edge(lines, FINE_TILTS, begin, stop)
        sides.by_edge[side] = None if inner_edge is None else inner_edge.depth
    return sides


def measure_borders(
    card: np.ndarray, margin: float | None, search: Search = PLAIN
) -> Borders | None:
    """The border on each side of a straightened card; None if one can't be found.

    `margin`: see _sides.
    """
    lab = _lab(card)
    sides = _sides(lab, margin, search)
    if sides is None:
        return None
    size = card.shape[1], card.shape[0]
    for inner in _border_ends(sides.by_color, sides.by_edge, AGREE * card.shape[1]):
        borders = Borders(sides.outer, inner, size)
        if _looks_like_one_border(lab, borders):
            return borders
    return None  # e.g. a full-art card, without a plain border


def _border_ends(
    by_color: dict[str, float | None], by_edge: dict[str, float | None], agree: float
) -> Iterator[dict[str, float]]:
    """Where the border ends on each side, most likely first.

    By color (see _border_end). Then, on a holo border, the color can drift
    with the light and stray from the one near the card's edge before the
    border ends: one side's end is taken where its colors change across an
    edge instead. Only when the other three sides end at about the same place
    either way (within `agree` pixels), a sign of a plain border; and only
    one side: edges in the artwork of a full-art card can happen to add up.
    The side tried first is the one whose edge is furthest past where its
    color strayed.
    """
    def agrees(side: str) -> bool:
        color, edge = by_color[side], by_edge[side]
        return color is not None and edge is not None and abs(edge - color) <= agree

    def gap(side: str) -> float:
        color, edge = by_color[side], by_edge[side]
        return np.inf if color is None or edge is None else edge - color

    if all(depth is not None for depth in by_color.values()):
        yield cast(dict[str, float], by_color)
    drifted = [side for side in SIDES if by_edge[side] is not None
               and all(agrees(other) for other in SIDES if other != side)]
    for side in sorted(drifted, key=gap, reverse=True):
        yield cast(dict[str, float], {**by_color, side: by_edge[side]})


def _border_end(
    lines: Lines, edge: float, tilt: float, start: float, stop: float, persist: int
) -> float | None:
    """Depth where the border that begins at `edge` ends, looked for between start and stop.

    On each line, the border's color is taken a little inside the card's
    edge (past any whitening); the border ends where the color differs from
    it for `persist` pixels.
    """
    count, depth = lines.colors.shape[:2]
    offset = lines.along - lines.along.mean()
    inside = np.rint(edge + tilt * offset).astype(np.intp)[:, None] + np.arange(5, 10)
    inside = np.clip(inside, 0, depth - 1)
    color = np.median(lines.colors[np.arange(count)[:, None], inside], axis=1)
    # Lines whose hue is off (whitening reaching deeper): take the border's
    # color from the nearest other lines. Light may change along the side, hue hardly.
    hue_off = np.linalg.norm(color[:, 1:] - np.median(color[:, 1:], axis=0), axis=1) > EDGE_CHANGE
    if hue_off.any() and not hue_off.all():
        index = np.arange(count)
        for channel in range(3):
            color[hue_off, channel] = np.interp(index[hue_off], index[~hue_off],
                                                color[~hue_off, channel])
    same = np.linalg.norm(lines.colors - color[:, None, :], axis=2) <= EDGE_CHANGE
    # Pixels of the border's color in [d, d + persist): none means a lasting change.
    counts = np.concatenate([np.zeros((count, 1), np.int32), np.cumsum(same, axis=1)], axis=1)
    lasting = counts[:, persist:] - counts[:, :-persist] == 0
    start, stop = max(0, int(start)), min(lasting.shape[1], int(stop))
    if start >= stop:
        return None
    coverage, _ = _coverage(lasting, lines.along, FINE_TILTS, np.arange(start, stop))
    above = np.flatnonzero(coverage >= EDGE_COVERAGE)
    return float(start + above[0]) if len(above) else None


def _border_color(lab: np.ndarray, borders: Borders, side: str) -> np.ndarray:
    """Median a, b of the middle of one side's border."""
    height, width = lab.shape[:2]
    outer, inner = borders.outer[side], borders.inner[side]
    a, b = int(outer + 0.25 * (inner - outer)), int(outer + 0.75 * (inner - outer)) + 1
    rows = slice(int(0.2 * height), int(0.8 * height))
    cols = slice(int(0.2 * width), int(0.8 * width))
    strip = {
        "left": lab[rows, a:b], "right": lab[rows, width - b : width - a],
        "top": lab[a:b, cols], "bottom": lab[height - b : height - a, cols],
    }[side]
    return np.median(strip.reshape(-1, 3)[:, 1:].astype(np.float32), axis=0)


def _looks_like_one_border(lab: np.ndarray, borders: Borders) -> bool:
    """The four sides look like one printed border, not edges inside the card."""
    across = borders.width("left") + borders.width("right")
    down = borders.width("top") + borders.width("bottom")
    if abs(across - down) > SAME_TOTAL * (across + down) / 2:
        return False
    colors = [_border_color(lab, borders, side) for side in SIDES]
    return max(np.linalg.norm(a - b) for a in colors for b in colors) <= SAME_COLOR


def _outlines(photo: np.ndarray) -> list[np.ndarray]:
    """Rough outlines to start from: by brightness, and by color if different."""
    found = [c for c in (detect.find_outline(photo), detect.find_outline(photo, color=True))
             if c is not None]
    if len(found) == 2 and np.abs(found[0] - found[1]).max() < 0.01 * max(photo.shape):
        found.pop()
    return found


def _card_views(
    photo: np.ndarray, search: Search = PLAIN
) -> Iterator[tuple[np.ndarray, float | None]]:
    """Views of the card to measure, most likely first, with their margin (see _sides).

    A photo with a card's proportions is taken as cropped to the card first:
    the only outline inside it is a frame printed on the card. Then the card
    straightened from each refined outline, the largest first: the card's
    edge rather than a frame printed inside it.
    """
    cropped = detect.as_card(photo)
    if cropped is not None:
        yield cropped, None
    refined = [found for corners in _outlines(photo)
               if (found := refine_outline(photo, corners, search)) is not None]
    for corners in sorted(refined, key=cv2.contourArea, reverse=True):
        yield _warp(photo, corners, MARGIN)[0], MARGIN


def card_and_borders(
    photo: np.ndarray, search: Search = PLAIN
) -> tuple[np.ndarray, Borders] | None:
    """The card, cropped or straightened (see _card_views), and its borders.

    None if they can't be measured.
    """
    for card, margin in _card_views(photo, search):
        borders = measure_borders(card, margin, search)
        if borders is not None:
            return card, borders
    return None


# ---------------------------------------------------------------------------
# Centering from the catalog's picture
# ---------------------------------------------------------------------------

OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


def _frame_lines(picture: np.ndarray) -> dict[str, float] | None:
    """Where the border ends on each side of the catalog's picture (CARD_SIZE).

    Taken as an edge, where the change is sharpest; only where the color
    agrees (see _border_end), as on a plain border. None if a side has none.
    """
    lab = _lab(picture)
    width = picture.shape[1]
    low, high = BORDER_RANGE
    found = {}
    for side in SIDES:
        lines = _lines(lab, side, REFERENCE)
        edge = _first_edge(lines, FINE_TILTS, low * width, high * width)
        color = _border_end(lines, 0.0, 0.0, low * width, high * width, round(PERSIST * width))
        if edge is None or color is None or abs(edge.depth - color) > AGREE * width:
            return None
        found[side] = edge.depth
    return found


def _alignment(picture: np.ndarray, card: np.ndarray) -> np.ndarray | None:
    """The perspective from the catalog's picture to the card's view, from
    points found in both; None if too few agree on one, or too loosely."""
    sift = cv2.SIFT_create()
    found = [sift.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None)
             for image in (picture, card)]
    (points_a, features_a), (points_b, features_b) = found
    if features_a is None or features_b is None or len(features_b) < 2:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(features_a, features_b, k=2)
    good = [pair[0] for pair in pairs
            if len(pair) == 2 and pair[0].distance < ALIGN_RATIO * pair[1].distance]
    if len(good) < MIN_ALIGNED:
        return None
    a = np.float32([points_a[m.queryIdx].pt for m in good])
    b = np.float32([points_b[m.trainIdx].pt for m in good])
    matrix, inliers = cv2.findHomography(a, b, cv2.RANSAC, 3.0)
    if matrix is None:
        return None
    kept = inliers.ravel().astype(bool)
    errors = np.linalg.norm(cv2.perspectiveTransform(a[kept][None], matrix)[0] - b[kept], axis=1)
    if kept.sum() < MIN_ALIGNED or float(np.median(errors)) > MAX_ALIGN_ERROR:
        return None
    return matrix


def _carried(
    matrix: np.ndarray, frame: dict[str, float], picture_shape: tuple[int, ...],
    card_shape: tuple[int, ...],
) -> dict[str, float]:
    """The picture's frame lines in the card's view, as depths from its sides
    (at the middle of each side, like the edges found in the photo).

    A card photographed upside down has the picture's left side on its right.
    """
    height, width = picture_shape[:2]
    view_height, view_width = card_shape[:2]

    def place(x: float, y: float) -> np.ndarray:
        return cv2.perspectiveTransform(np.float32([[[x, y]]]), matrix)[0, 0]

    turned = place(width / 2, 0)[1] > place(width / 2, height - 1)[1]
    carried = {}
    for side, depth in frame.items():
        x, y = {"left": (depth, height / 2), "right": (width - 1 - depth, height / 2),
                "top": (width / 2, depth), "bottom": (width / 2, height - 1 - depth)}[side]
        at_x, at_y = place(x, y)
        target = OPPOSITE[side] if turned else side
        carried[target] = float({"left": at_x, "right": view_width - 1 - at_x, "top": at_y,
                                 "bottom": view_height - 1 - at_y}[target])
    return carried


def _confirmed(sides: Sides, carried: dict[str, float], tolerance: float, agree: float) -> bool:
    """The lines carried over lie where the photo shows them, within
    `tolerance` pixels, on every side where the border's end shows plainly;
    and those include one side across and one down, so that both ways of the
    alignment are checked."""
    seen = sides.seen(agree)
    if not {"left", "right"} & set(seen) or not {"top", "bottom"} & set(seen):
        return False
    return all(abs(carried[side] - cast(float, sides.by_edge[side])) <= tolerance
               for side in seen)


def borders_by_reference(photo: np.ndarray, picture: np.ndarray) -> Borders | None:
    """The borders, with their inner edge carried over from the catalog's
    picture of the card (CARD_SIZE) where the photo doesn't show it all around.

    Off-center printing moves the whole print, the border's inner edge with
    the artwork and text: aligned on them, the picture shows where that edge
    is printed on this card, wherever the cut falls. The picture's own cut
    (it may be off center too) is never used: the card's edge comes from the
    photo. The edge carried over must match the photo where it shows (see
    _confirmed): else the alignment is off, or the picture is of another
    print of the card, and None.
    """
    frame = _frame_lines(picture)
    if frame is None:
        return None
    for card, margin in _card_views(photo):
        lab = _lab(card)
        sides = _sides(lab, margin)
        if sides is None:
            continue
        matrix = _alignment(picture, card)
        if matrix is None:
            continue
        carried = _carried(matrix, frame, picture.shape, card.shape)
        borders = Borders(sides.outer, carried, (card.shape[1], card.shape[0]))
        scale = borders.px_per_mm()
        tolerance = CARRIED_AGREE_MM * min(cast(tuple[float, float], scale))
        if (_confirmed(sides, carried, tolerance, AGREE * card.shape[1])
                and _looks_like_one_border(lab, borders)):
            return borders
    return None


def centering(borders: Borders, upside_down: bool = False) -> Centering:
    left, right = borders.width("left"), borders.width("right")
    top, bottom = borders.width("top"), borders.width("bottom")
    left_pct, top_pct = 100 * left / (left + right), 100 * top / (top + bottom)
    scale = borders.px_per_mm()
    across = down = None
    if scale is not None:
        across, down = (left - right) / scale[0], (top - bottom) / scale[1]
    if upside_down:
        left_pct, top_pct = 100 - left_pct, 100 - top_pct
        across, down = (None if mm is None else -mm for mm in (across, down))
    return Centering(
        left_pct=round(left_pct, 1), top_pct=round(top_pct, 1),
        left_minus_right_mm=None if across is None else round(across, 2),
        top_minus_bottom_mm=None if down is None else round(down, 2),
    )


def worse_side(value: Centering) -> float:
    """60 for 60/40 or 40/60, whichever way it is off."""
    return 50 + max(abs(value.left_pct - 50), abs(value.top_pct - 50))


# ---------------------------------------------------------------------------
# Whitening
# ---------------------------------------------------------------------------

REGIONS = ("top-left", "top-right", "bottom-left", "bottom-right",
           "top", "bottom", "left", "right")


def _card_box(shape: tuple[int, ...], borders: Borders) -> tuple[int, int, int, int]:
    height, width = shape[:2]
    return (round(borders.outer["left"]), round(borders.outer["top"]),
            width - 1 - round(borders.outer["right"]), height - 1 - round(borders.outer["bottom"]))


def _card_mask(shape: tuple[int, ...], borders: Borders) -> np.ndarray:
    """The card's shape in the image: a rectangle with rounded corners."""
    left, top, right, bottom = _card_box(shape, borders)
    radius = max(1, round(CORNER_RADIUS * (right - left)))
    mask = np.zeros(shape[:2], np.uint8)
    cv2.rectangle(mask, (left + radius, top), (right - radius, bottom), 255, -1)
    cv2.rectangle(mask, (left, top + radius), (right, bottom - radius), 255, -1)
    for x in (left + radius, right - radius):
        for y in (top + radius, bottom - radius):
            cv2.circle(mask, (x, y), radius, 255, -1)
    return mask


def _regions(shape: tuple[int, ...], borders: Borders) -> np.ndarray:
    """Index in REGIONS of every pixel: the corners, then the nearest side."""
    left, top, right, bottom = _card_box(shape, borders)
    corner = round(0.08 * (right - left))
    ys, xs = np.mgrid[0 : shape[0], 0 : shape[1]]
    distances = np.stack([xs - left, right - xs, ys - top, bottom - ys])
    region = np.array([6, 7, 4, 5])[distances.argmin(axis=0)]  # left, right, top, bottom
    near_left, near_right = xs < left + corner, xs > right - corner
    near_top, near_bottom = ys < top + corner, ys > bottom - corner
    for index, (vertical, horizontal) in enumerate(
        ((near_top, near_left), (near_top, near_right),
         (near_bottom, near_left), (near_bottom, near_right))
    ):
        region[vertical & horizontal] = index
    return region


@dataclass
class Whitening:
    percent: float  # of the edge band
    by_region: dict[str, float]  # percent of each region's band
    is_card_back: bool  # the border is the back's dark blue
    light_background: bool  # it blurs into the band and looks like whitening
    # Blue in the band, if not the border's dark blue: the band may be on a
    # back's artwork, its edge missed, rather than on another photo (a front).
    bluish: bool = False

    @property
    def worst_region(self) -> float:
        return max(self.by_region.values(), default=0.0)


def _distance(mask: np.ndarray) -> np.ndarray:
    """Distance of each pixel of the mask from its edge, the image's edge
    included (a photo cropped to the card)."""
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    return cv2.distanceTransform(padded, cv2.DIST_L2, 3)[1:-1, 1:-1]


def measure_whitening(back: np.ndarray, borders: Borders) -> Whitening:
    """White on the edge of a card back."""
    inside = _card_mask(back.shape, borders)
    depth = _distance(inside)
    band = (depth >= BAND[0]) & (depth <= BAND[1])
    outside = _distance(255 - inside)
    background = (outside >= BACKGROUND[0]) & (outside <= BACKGROUND[1])

    hsv = cv2.cvtColor(back, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    white = (saturation < 60) & (value > 165)

    # The back's border is dark blue: OpenCV hue about 100-130 of 180.
    blue = band & ~white
    bluish = bool(blue.sum() > 0.5 * band.sum()) and 95 <= float(np.median(hue[blue])) <= 135
    is_back = bluish and float(np.median(saturation[blue])) > 80
    light = bool(background.any()) and float(white[background].mean()) > 0.3

    region = _regions(back.shape, borders)
    by_region = {}
    for index, name in enumerate(REGIONS):
        in_region = band & (region == index)
        by_region[name] = round(100 * float(white[in_region].mean()), 1) if in_region.any() else 0.0
    percent = round(100 * float(white[band].mean()), 1) if band.any() else 0.0
    return Whitening(percent, by_region, is_back, light, bluish)


def _measure_back(back: np.ndarray) -> tuple[Borders, Whitening] | str:
    """The back's borders and whitening, or the key of the note to give instead.

    Looked for as any card first, then with the BACK search (see SOFT_STEP).
    """
    first = None
    for search in (PLAIN, BACK):
        measured = card_and_borders(back, search)
        if measured is None:
            continue
        card, borders = measured
        found = measure_whitening(card, borders)
        if found.is_card_back:
            return borders, found
        first = first or found
    # Blue where the border was looked for: a back whose edge was missed.
    return "no_back_card" if first is None or first.bluish else "not_a_back"


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------

NOTES = {
    "front_centering": {
        "it": "Centratura fronte {lr}/{rl} sinistra-destra, {tb}/{bt} alto-basso.",
        "en": "Front centering {lr}/{rl} left-right, {tb}/{bt} top-bottom.",
    },
    "back_centering": {
        "it": "Centratura retro {lr}/{rl} sinistra-destra, {tb}/{bt} alto-basso.",
        "en": "Back centering {lr}/{rl} left-right, {tb}/{bt} top-bottom.",
    },
    "no_front_border": {
        "it": "Bordi del fronte non misurabili: carta senza bordo uniforme, o foto poco "
              "nitida o su uno sfondo del colore del bordo.",
        "en": "Front borders not measurable: no plain border, or a blurry photo, or a "
              "background the color of the border.",
    },
    "carried_line": {
        "it": "Nella foto la fine del bordo non si vede su tutti i lati: è stata ritrovata "
              "allineando la foto all'immagine della carta nel catalogo, e verificata sui "
              "lati dove si vede. Il taglio è misurato solo sulla foto.",
        "en": "The border's inner edge doesn't show on every side of the photo: it was "
              "found by aligning the photo with the card's catalog picture, and checked on "
              "the sides where it shows. The cut is measured on the photo alone.",
    },
    "no_back_card": {
        "it": "Bordi del retro non trovati: fotografa la carta intera con più luce, su un "
              "piano uniforme e opaco. Su un piano nero il blu del retro si vede poco.",
        "en": "Back borders not found: photograph the whole card with more light, on a "
              "plain matte surface. On a black one, the back's dark blue hardly shows.",
    },
    "not_a_back": {
        "it": "La seconda foto non sembra il retro di una carta Pokémon.",
        "en": "The second photo doesn't look like the back of a Pokémon card.",
    },
    "whitening": {
        "it": "Sbiancatura: {where}.",
        "en": "Whitening: {where}.",
    },
    "no_whitening": {
        "it": "Nessuna sbiancatura visibile sui bordi del retro.",
        "en": "No whitening visible on the back's edges.",
    },
    "light_background": {
        "it": "Sfondo chiaro: la sbiancatura dei bordi non si distingue. Fotografa il retro "
              "su un piano scuro.",
        "en": "Light background: whitening on the edges can't be told apart. Photograph the "
              "back on a dark surface.",
    },
    "needs_back": {
        "it": "Aggiungi una foto del retro per stimare la condizione: l'usura dei bordi si "
              "vede sul retro.",
        "en": "Add a photo of the back to estimate the condition: worn edges show on the back.",
    },
    "indicative": {
        "it": "Stima indicativa da foto, non una valutazione professionale.",
        "en": "Indicative estimate from photos, not a professional grade.",
    },
}

REGION_NAMES = {
    "it": {"top-left": "angolo in alto a sinistra", "top-right": "angolo in alto a destra",
           "bottom-left": "angolo in basso a sinistra",
           "bottom-right": "angolo in basso a destra",
           "top": "bordo superiore", "bottom": "bordo inferiore",
           "left": "bordo sinistro", "right": "bordo destro"},
    "en": {"top-left": "top-left corner", "top-right": "top-right corner",
           "bottom-left": "bottom-left corner", "bottom-right": "bottom-right corner",
           "top": "top edge", "bottom": "bottom edge", "left": "left edge",
           "right": "right edge"},
}
WORN_REGION = 3.0  # % of a region's band that is worth a note


def _note(key: str, lang: Lang, **values) -> str:
    return NOTES[key][lang].format(**values)


def _centering_note(key: str, value: Centering, lang: Lang) -> str:
    lr, tb = round(value.left_pct), round(value.top_pct)
    return _note(key, lang, lr=lr, rl=100 - lr, tb=tb, bt=100 - tb)


def grade(front: Centering | None, back: Centering | None, whitening: Whitening) -> Condition:
    """The best grade that centering and whitening both allow."""
    front_off = worse_side(front) if front else 50
    back_off = worse_side(back) if back else 50
    for allowed in GRADES:
        if (
            front_off <= allowed.front
            and back_off <= allowed.back
            and whitening.worst_region <= allowed.worst_region
            and whitening.percent <= allowed.whitening
        ):
            return allowed.name
    return "poor"


def assess(
    front: np.ndarray, front_upside_down: bool, back: np.ndarray | None, lang: Lang,
    picture: Callable[[], np.ndarray | None] | None = None,
) -> ConditionEstimate:
    """Condition from the front photo and, if given, the back photo.

    `front_upside_down`: the code was read with the card turned around, so
    left and right (and top and bottom) are swapped in the photo.
    `picture`: gets the catalog's picture of the card (CARD_SIZE), asked for
    only if the front's borders can't be measured from the photo alone (see
    borders_by_reference).
    Without the back, only the front's centering: no estimate.
    """
    notes: list[str] = []
    front_centering = None
    measured = card_and_borders(front)
    borders = measured[1] if measured is not None else None
    carried = False
    if borders is None and picture is not None and (reference := picture()) is not None:
        borders = borders_by_reference(front, reference)
        carried = borders is not None
    if borders is not None:
        front_centering = centering(borders, front_upside_down)
        notes.append(_centering_note("front_centering", front_centering, lang))
        if carried:
            notes.append(_note("carried_line", lang))
    else:
        notes.append(_note("no_front_border", lang))

    back_centering, whitening = None, None
    if back is not None:
        found_back = _measure_back(back)
        if isinstance(found_back, str):
            notes.append(_note(found_back, lang))
        else:
            borders, found = found_back
            back_centering = centering(borders)
            notes.append(_centering_note("back_centering", back_centering, lang))
            if found.light_background:
                notes.append(_note("light_background", lang))
            else:
                whitening = found
                worn = [REGION_NAMES[lang][name] for name, share in found.by_region.items()
                        if share >= WORN_REGION]
                notes.append(_note("whitening", lang, where=", ".join(worn)) if worn
                             else _note("no_whitening", lang))

    estimate = None
    if whitening is not None:
        estimate = grade(front_centering, back_centering, whitening)
        notes.append(_note("indicative", lang))
    elif back is None:
        notes.append(_note("needs_back", lang))
    return ConditionEstimate(
        estimate=estimate,
        front_centering=front_centering,
        back_centering=back_centering,
        edge_whitening_pct=whitening.percent if whitening else None,
        notes=notes,
    )
