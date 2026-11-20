"""Tests of the condition estimate, offline.

Fronts and backs are drawn with OpenCV with known borders (and white specks
on the edges for wear), then turned into phone-like photos by
jobs/evaluate_scan.py.
"""

import cv2
import numpy as np
import pytest

from app.recognition import condition, detect
from app.schemas import Centering
from jobs.evaluate_scan import make_photo

DARK_TABLE = (60, 90, 60)
WHITE_TABLE = (235, 235, 235)
YELLOW = (40, 200, 240)  # BGR
BACK_BLUE = (150, 70, 30)


def _rounded(card: np.ndarray) -> np.ndarray:
    """Add an alpha channel with the card's rounded corners."""
    height, width = card.shape[:2]
    radius = round(condition.CORNER_RADIUS * width)
    alpha = np.zeros((height, width), np.uint8)
    cv2.rectangle(alpha, (radius, 0), (width - 1 - radius, height - 1), 255, -1)
    cv2.rectangle(alpha, (0, radius), (width - 1, height - 1 - radius), 255, -1)
    for x in (radius, width - 1 - radius):
        for y in (radius, height - 1 - radius):
            cv2.circle(alpha, (x, y), radius, 255, -1, lineType=cv2.LINE_AA)
    return np.dstack([card, alpha])


def drawn_front(left: int, right: int, top: int, bottom: int) -> np.ndarray:
    """A yellow border of the given widths around a light card with some artwork."""
    width, height = detect.CARD_SIZE
    card = np.full((height, width, 3), YELLOW, np.uint8)
    cv2.rectangle(card, (left, top), (width - 1 - right, height - 1 - bottom), (235, 235, 235), -1)
    cv2.rectangle(card, (left + 30, top + 90), (width - right - 30, 560), (150, 120, 90), -1)
    cv2.putText(card, "Testmon", (left + 30, top + 60), cv2.FONT_HERSHEY_DUPLEX, 1.4,
                (20, 20, 20), 2, cv2.LINE_AA)
    return _rounded(card)


def drawn_back(
    left: int = 36, right: int = 36, top: int = 36, bottom: int = 36,
    worn: tuple[str, ...] = (),
) -> np.ndarray:
    """A dark blue border around a lighter blue back with a ball in the middle.

    `worn`: corners ('top-left', 'bottom-right') or sides ('top') with white
    specks within 6 px (half a millimeter) of the card's edge.
    """
    width, height = detect.CARD_SIZE
    back = np.full((height, width, 3), BACK_BLUE, np.uint8)
    cv2.rectangle(back, (left, top), (width - 1 - right, height - 1 - bottom), (215, 140, 70), -1)
    center = (width // 2, height // 2)
    cv2.ellipse(back, center, (250, 330), 20, 0, 360, (190, 110, 50), 40)
    cv2.ellipse(back, center, (150, 150), 0, 180, 360, (40, 40, 200), -1)
    cv2.ellipse(back, center, (150, 150), 0, 0, 180, (240, 240, 240), -1)
    cv2.line(back, (center[0] - 150, center[1]), (center[0] + 150, center[1]), (20, 20, 20), 14)

    radius = round(condition.CORNER_RADIUS * width)
    mask = _rounded(np.zeros((height, width, 3), np.uint8))[..., 3]
    edge_distance = condition._distance(mask)
    areas = {
        "top-left": (slice(0, 60), slice(0, 60)),
        "bottom-right": (slice(height - 60, height), slice(width - 60, width)),
        "top": (slice(0, 8), slice(200, 550)),
    }
    rng = np.random.default_rng(7)
    for name in worn:
        rows, cols = areas[name]
        for _ in range(400):
            y = rng.integers(rows.start, rows.stop)
            x = rng.integers(cols.start, cols.stop)
            if 0 < edge_distance[y, x] < 6:
                cv2.circle(back, (int(x), int(y)), 2, (235, 235, 235), -1)
    assert radius > 0
    return _rounded(back)


def photo(card: np.ndarray, seed: int = 1, background=DARK_TABLE) -> np.ndarray:
    return detect.decode(make_photo(card, np.random.default_rng(seed), turn=0,
                                    background=background))


# ---------------------------------------------------------------------------
# Centering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_front_centering_is_measured_from_the_border_widths(seed):
    # 26 px on the left, 46 on the right: 26 / 72 = 36.1%. Top and bottom equal.
    measured = condition.card_and_borders(photo(drawn_front(26, 46, 36, 36), seed))

    assert measured is not None
    value = condition.centering(measured[1])
    assert value.left_pct == pytest.approx(36.1, abs=2.5)
    assert value.top_pct == pytest.approx(50.0, abs=2.5)


def test_upside_down_swaps_the_sides():
    borders = condition.Borders(
        outer={"left": 0, "right": 0, "top": 0, "bottom": 0},
        inner={"left": 20, "right": 30, "top": 25, "bottom": 25},
    )

    assert condition.centering(borders) == Centering(left_pct=40.0, top_pct=50.0)
    assert condition.centering(borders, upside_down=True) == Centering(left_pct=60.0, top_pct=50.0)


def test_a_card_without_a_plain_border_is_not_measured():
    width, height = detect.CARD_SIZE
    full_art = np.zeros((height, width, 3), np.uint8)
    for x in range(0, width, 25):  # artwork up to the edge
        cv2.line(full_art, (x, 0), (width - x, height), (int(x % 255), 120, 200), 9)

    assert condition.card_and_borders(photo(_rounded(full_art))) is None


def test_text_printed_on_the_border_is_not_taken_for_its_end():
    card = drawn_front(36, 36, 36, 36)
    width, height = detect.CARD_SIZE
    cv2.putText(card, "(c) 2025 Pokemon / Nintendo / Creatures / GAME FREAK", (90, height - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20, 255), 1, cv2.LINE_AA)

    measured = condition.card_and_borders(photo(card))

    assert measured is not None
    assert condition.centering(measured[1]).top_pct == pytest.approx(50.0, abs=2.5)


def holo_front(left: int, right: int, top: int, bottom: int) -> np.ndarray:
    """A silver border around an orange card, the bottom one catching the
    light like a holo border: its color drifts from silver at the card's edge
    to violet where it ends."""
    width, height = detect.CARD_SIZE
    silver, violet = np.array([185, 185, 190]), np.array([215, 120, 175])  # BGR
    card = np.full((height, width, 3), silver, np.uint8)
    for depth in range(bottom):
        t = depth / (bottom - 1)
        card[height - 1 - depth, left : width - right] = silver * (1 - t) + violet * t
    cv2.rectangle(card, (left, top), (width - 1 - right, height - 1 - bottom), (60, 140, 235), -1)
    return _rounded(card)


def at_an_angle(card: np.ndarray, far: float = 0.75, seed: int = 1) -> np.ndarray:
    """A photo of the card from its bottom side, at an angle: the far, top
    side `far` times as wide as the near one. On a black table, with a
    keyboard-like gray bar just above the card."""
    rng = np.random.default_rng(seed)
    width, height = 1200, 1600
    scene = np.full((height, width, 3), 22, np.float32)
    near, top, bottom = 760.0, 380.0, 1380.0
    scene[: int(top) - 35, 100:1100] = 95  # the keyboard's edge, 35 px above the card
    corners = np.float32([[600 - near * far / 2, top], [600 + near * far / 2, top],
                          [600 + near / 2, bottom], [600 - near / 2, bottom]])
    card_h, card_w = card.shape[:2]
    source = np.float32([[0, 0], [card_w, 0], [card_w, card_h], [0, card_h]])
    matrix = cv2.getPerspectiveTransform(source, corners)
    warped = cv2.warpPerspective(card[:, :, :3], matrix, (width, height)).astype(np.float32)
    mask = cv2.warpPerspective(card[:, :, 3], matrix, (width, height)).astype(np.float32)
    mask = mask[..., None] / 255
    scene = scene * (1 - mask) + warped * mask + rng.normal(0, 2, scene.shape)
    scene = cv2.GaussianBlur(scene, (0, 0), 1.0)
    ok, jpeg = cv2.imencode(".jpg", np.clip(scene, 0, 255).astype(np.uint8),
                            [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return detect.decode(jpeg.tobytes())


def test_a_back_photographed_at_an_angle_below_a_keyboard_is_measured():
    # The real photo behind this test: the keyboard's edge, outside the card's,
    # was taken for the card's top, and the card was off center once
    # straightened, as the margin around it was added in the photo.
    measured = condition.card_and_borders(at_an_angle(drawn_back(30, 42, 36, 36)))

    assert measured is not None
    value = condition.centering(measured[1])
    assert value.left_pct == pytest.approx(41.7, abs=2.5)  # 30 / 72
    assert value.top_pct == pytest.approx(50.0, abs=2.5)


def test_centering_tells_how_much_wider_a_border_is_in_mm():
    # 26 px on the left, 46 on the right, of a card 750 px for 63 mm.
    measured = condition.card_and_borders(photo(drawn_front(26, 46, 36, 36)))

    assert measured is not None
    value = condition.centering(measured[1])
    assert value.left_minus_right_mm == pytest.approx(-20 * 63 / 750, abs=0.2)
    assert value.top_minus_bottom_mm == pytest.approx(0, abs=0.2)
    turned = condition.centering(measured[1], upside_down=True)
    assert turned.left_minus_right_mm == -value.left_minus_right_mm


SILVER = (190, 190, 192)
FRAME = (150, 150, 152)
SHEET_PAD = 40  # print around the card, where it can be cut


def printed_sheet(border: int = 36, seed: int = 5) -> np.ndarray:
    """The print of a silver card, bigger than the card so that it can be cut
    off center: a silver border `border` px wide around a gray frame, with
    artwork and text."""
    width, height = detect.CARD_SIZE
    pad = SHEET_PAD
    sheet = np.full((height + 2 * pad, width + 2 * pad, 3), SILVER, np.uint8)
    x0, y0 = pad + border, pad + border
    x1, y1 = pad + width - 1 - border, pad + height - 1 - border
    cv2.rectangle(sheet, (x0, y0), (x1, y1), FRAME, -1)
    art = (x0 + 30, y0 + 90, x1 - 30, y0 + 560)
    cv2.rectangle(sheet, art[:2], art[2:], (120, 90, 60), -1)
    rng = np.random.default_rng(seed)
    for _ in range(160):
        color = tuple(int(v) for v in rng.integers(0, 255, 3))
        x, y = int(rng.integers(art[0], art[2])), int(rng.integers(art[1], art[3]))
        if rng.random() < 0.5:
            cv2.circle(sheet, (x, y), int(rng.integers(4, 30)), color, -1)
        else:
            cv2.rectangle(sheet, (x, y), (x + int(rng.integers(5, 40)),
                                          y + int(rng.integers(5, 40))), color, -1)
    for i, words in enumerate(["Testmon", "Quick Draw 30", "Flash Spark 90x",
                               "weakness resistance retreat"]):
        cv2.putText(sheet, words, (x0 + 25, y0 + 60 if i == 0 else y0 + 600 + 70 * i),
                    cv2.FONT_HERSHEY_DUPLEX, 1.1 if i < 3 else 0.7, (30, 30, 30), 2, cv2.LINE_AA)
    return sheet


def cut(sheet: np.ndarray, right: int = 0) -> np.ndarray:
    """The card cut from the sheet, `right` px right of center."""
    width, height = detect.CARD_SIZE
    return sheet[SHEET_PAD : SHEET_PAD + height, SHEET_PAD + right : SHEET_PAD + right + width]


def silver_on_silver(card: np.ndarray, right_border: int) -> np.ndarray:
    """The card lit so that the frame looks as light as the border on its
    right side: there, the border's end doesn't show."""
    card = card.copy()
    width, height = detect.CARD_SIZE
    strip = card[right_border : height - right_border,
                 width - right_border - 60 : width - right_border]
    strip[np.all(strip == FRAME, axis=2)] = SILVER
    return _rounded(card)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_border_end_that_doesnt_show_is_carried_over_from_the_catalog(seed):
    # The real photo behind this test: a silver Metal card, its frame as light
    # as the border on one side. Cut 10 px right of center: 26 px left, 46
    # right. The catalog's picture is cut elsewhere (centered): it doesn't matter.
    sheet = printed_sheet()
    front = photo(silver_on_silver(cut(sheet, right=10), right_border=46), seed)

    assert condition.card_and_borders(front) is None
    borders = condition.borders_by_reference(front, cut(sheet))
    assert borders is not None
    value = condition.centering(borders)
    assert value.left_pct == pytest.approx(36.1, abs=2.5)  # 26 / 72
    assert value.top_pct == pytest.approx(50.0, abs=2.5)
    assert value.left_minus_right_mm == pytest.approx(-20 * 63 / 750, abs=0.2)


def test_a_catalog_picture_of_another_print_is_not_used():
    # Same artwork and text, but its border ends 10 px further in: where the
    # photo shows the border's end, the picture's doesn't match.
    front = photo(silver_on_silver(cut(printed_sheet(), right=10), right_border=46))

    assert condition.borders_by_reference(front, cut(printed_sheet(border=46))) is None


def test_the_catalog_picture_is_asked_for_only_when_needed():
    asked = []

    def picture():
        asked.append(True)
        return None

    result = condition.assess(photo(drawn_front(36, 36, 36, 36)), False, None, "en", picture)

    assert result.front_centering is not None
    assert not asked


def test_a_holo_border_drifting_in_color_is_measured_to_its_end():
    # 24 px on the left, 36 on the right: 40%. On the real photo, a holo
    # border went from silver to pink and blue along the bottom.
    measured = condition.card_and_borders(photo(holo_front(24, 36, 30, 30), seed=2))

    assert measured is not None
    value = condition.centering(measured[1])
    assert value.left_pct == pytest.approx(40.0, abs=2.5)
    assert value.top_pct == pytest.approx(50.0, abs=2.5)


# ---------------------------------------------------------------------------
# Whitening and grade
# ---------------------------------------------------------------------------


def test_a_clean_back_has_no_whitening_and_a_centered_card_is_mint():
    result = condition.assess(photo(drawn_front(36, 36, 36, 36)), False,
                              photo(drawn_back(), seed=2), "it")

    assert result.edge_whitening_pct == pytest.approx(0, abs=0.3)
    assert result.estimate == "mint"
    assert result.back_centering.left_pct == pytest.approx(50, abs=2.5)
    assert "Nessuna sbiancatura visibile sui bordi del retro." in result.notes


def test_worn_corners_and_edges_are_found_and_lower_the_grade():
    back = drawn_back(30, 42, 36, 36, worn=("top-left", "bottom-right", "top"))

    result = condition.assess(photo(drawn_front(36, 36, 36, 36)), False, photo(back, seed=3), "it")

    assert result.edge_whitening_pct > 0.5
    assert result.estimate not in ("mint", "near_mint")
    whitening_note = next(note for note in result.notes if note.startswith("Sbiancatura:"))
    for place in ("angolo in alto a sinistra", "angolo in basso a destra", "bordo superiore"):
        assert place in whitening_note
    assert "angolo in basso a sinistra" not in whitening_note
    # 30 / 72 on the back, left to right.
    assert result.back_centering.left_pct == pytest.approx(41.7, abs=2.5)


def test_without_the_back_only_the_front_centering_is_given():
    result = condition.assess(photo(drawn_front(36, 36, 36, 36)), False, None, "en")

    assert result.front_centering is not None
    assert result.estimate is None
    assert result.edge_whitening_pct is None
    assert any(note.startswith("Add a photo of the back") for note in result.notes)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_dark_blue_back_on_a_black_table_in_dim_light_is_measured(seed):
    # The real photo behind this test: the back's edge on a black laptop was
    # too faint for the usual search, and the frame around the artwork was
    # taken for the card's edge. 30 px left, 42 right.
    back = drawn_back(30, 42, 36, 36)
    colors = back[..., :3]
    colors[np.all(colors == BACK_BLUE, axis=2)] = (75, 38, 22)  # darker in dim light
    dim = cv2.GaussianBlur(photo(back, seed, background=(18, 18, 18)), (0, 0), 4.0)

    assert condition.card_and_borders(dim) is None
    result = condition.assess(photo(drawn_front(36, 36, 36, 36)), False, dim, "it")

    assert result.back_centering is not None
    assert result.back_centering.left_pct == pytest.approx(41.7, abs=2.5)
    assert result.back_centering.top_pct == pytest.approx(50.0, abs=2.5)
    assert result.edge_whitening_pct == pytest.approx(0, abs=0.3)


def test_a_back_on_a_white_table_is_not_graded():
    result = condition.assess(photo(drawn_front(36, 36, 36, 36)), False,
                              photo(drawn_back(), background=WHITE_TABLE), "it")

    assert result.estimate is None
    assert any(note.startswith("Sfondo chiaro") for note in result.notes)


def test_a_front_sent_as_the_back_is_noticed():
    front = photo(drawn_front(36, 36, 36, 36))

    result = condition.assess(front, False, front, "en")

    assert result.estimate is None
    assert "The second photo doesn't look like the back of a Pokémon card." in result.notes


@pytest.mark.parametrize(
    ("front", "back", "worst_region", "whitening", "expected"),
    [
        (52, 55, 0, 0, "mint"),
        (58, 55, 0, 0, "near_mint"),  # front 58/42
        (52, 65, 0, 0, "near_mint"),  # back 65/35
        (52, 55, 4, 0.3, "near_mint"),  # a speck on one corner
        (52, 55, 15, 1.0, "excellent"),  # a worn corner
        (52, 55, 40, 6, "good"),
        (75, 55, 0, 0, "good"),  # badly off-center
        (52, 55, 60, 18, "light_played"),
        (52, 55, 80, 50, "poor"),
    ],
)
def test_grade_is_the_best_that_every_measure_allows(front, back, worst_region, whitening,
                                                     expected):
    measured = condition.Whitening(
        percent=whitening, by_region={"top-left": worst_region}, is_card_back=True,
        light_background=False,
    )

    grade = condition.grade(Centering(left_pct=front, top_pct=50),
                            Centering(left_pct=back, top_pct=50), measured)

    assert grade == expected
