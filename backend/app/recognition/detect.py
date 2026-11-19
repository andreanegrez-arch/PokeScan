"""Find the card in a photo and straighten it.

The card is the largest four-sided shape with a card's proportions. It is
warped to a fixed upright size, so that the printed code always ends up in
the bottom strip. Whether the card was photographed upside down can't be told
from its outline: the caller reads both ways up.
"""

import cv2
import numpy as np

# Pokémon cards are 63 x 88 mm.
CARD_RATIO = 63 / 88
CARD_SIZE = (750, 1048)  # width, height of the straightened card, in pixels

DETECTION_SIDE = 800  # the outline is searched on a copy this size
MIN_AREA = 0.10  # of the photo: smaller shapes aren't the card
RATIO_TOLERANCE = 0.25  # perspective makes the outline look thinner or wider


class UnreadableImage(ValueError):
    """The upload is not an image OpenCV can decode."""


def decode(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise UnreadableImage("not a readable image")
    return image


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Corners as top-left, top-right, bottom-right, bottom-left, card upright.

    If the card lies sideways, its long side is turned vertical. The order is
    always clockwise, so the card is rotated, never mirrored.
    """
    center = points.mean(axis=0)
    angles = np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0])
    corners = points[np.argsort(angles)]  # clockwise, since image y points down
    corners = np.roll(corners, -int(np.argmin(corners.sum(axis=1))), axis=0)
    tl, tr, br, bl = corners
    width = np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)
    height = np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)
    if width > height:
        corners = np.roll(corners, -1, axis=0)
    return corners.astype(np.float32)


def _looks_like_a_card(corners: np.ndarray) -> bool:
    tl, tr, br, bl = corners
    width = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    height = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    if not width or not height:
        return False
    return abs(width / height - CARD_RATIO) <= RATIO_TOLERANCE * CARD_RATIO


def find_outline(image: np.ndarray) -> np.ndarray | None:
    """The card's four corners in the photo (ordered), or None if not found.

    Of the shapes with a card's proportions, the largest: the card's edge
    rather than a frame printed inside it.
    """
    height, width = image.shape[:2]
    scale = min(1.0, DETECTION_SIDE / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    # The median blur wipes out textures (wood, cloth) but keeps straight edges.
    gray = cv2.medianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 7)
    edges = cv2.Canny(gray, 30, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    min_area = MIN_AREA * small.shape[0] * small.shape[1]
    best, best_area = None, 0.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        if cv2.contourArea(contour) < min_area:
            break
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(polygon) == 4 and cv2.isContourConvex(polygon):
            points = polygon.reshape(4, 2).astype(np.float32)
        else:
            # Rounded corners or a finger over an edge: use the enclosing rectangle.
            points = cv2.boxPoints(cv2.minAreaRect(contour)).astype(np.float32)
        area = cv2.contourArea(points)
        # The whole photo's frame is not a card.
        if area > 0.95 * small.shape[0] * small.shape[1]:
            continue
        corners = _order_corners(points / scale)
        if area > best_area and _looks_like_a_card(corners):
            best, best_area = corners, area
    return best


def enlarge(corners: np.ndarray, factor: float = 1.12) -> np.ndarray:
    """The outline grown around its center: in case it was a frame inside the card."""
    center = corners.mean(axis=0)
    return (center + (corners - center) * factor).astype(np.float32)


def straighten(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    width, height = CARD_SIZE
    target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                      dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(corners, target)
    return cv2.warpPerspective(image, matrix, CARD_SIZE)


def find_card(image: np.ndarray) -> np.ndarray | None:
    """The card, straightened to CARD_SIZE; None if no card outline is found."""
    corners = find_outline(image)
    return None if corners is None else straighten(image, corners)


def as_card(image: np.ndarray) -> np.ndarray | None:
    """A photo already cropped to the card (no background), resized to CARD_SIZE.

    None if its proportions aren't a card's. Such a photo has no outline to
    find, or worse, an inner frame of the card that looks like one.
    """
    height, width = image.shape[:2]
    if width > height:
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        height, width = width, height
    # Tight: a 3:4 phone photo (0.75) must not pass for a card (0.716).
    if abs(width / height - CARD_RATIO) > 0.03 * CARD_RATIO:
        return None
    return cv2.resize(image, CARD_SIZE, interpolation=cv2.INTER_AREA)


def upside_down(card: np.ndarray) -> np.ndarray:
    return cv2.rotate(card, cv2.ROTATE_180)


def bottom_strip(card: np.ndarray) -> np.ndarray:
    """Where the collector code is printed, in every era of the game."""
    return card[int(card.shape[0] * 0.85) :, :]


def top_strip(card: np.ndarray) -> np.ndarray:
    """Where the card name is printed (lowest on old Trainer cards, under 'TRAINER')."""
    return card[: int(card.shape[0] * 0.25), :]
