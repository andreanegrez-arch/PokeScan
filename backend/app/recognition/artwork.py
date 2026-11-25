"""Recognize a card by its illustration, to check the number OCR read.

The code at the bottom of old cards is small print, and OCR misreads it:
Raichu's 14/102 read as 4/102 would make Charizard. So the illustration, alone,
is compared with the illustrations of the edition the code points to: it
confirms the number read, or corrects it.

An illustration is kept as a fingerprint, never as a picture: ORB keypoints
(corners of the drawing, with a 32-byte descriptor each) found in the artwork
box only (LAYOUTS). No printed text is in the box (LEGEND halves aside), so
one fingerprint serves
every language the card was printed in, and every edition that reprinted the
illustration (Base Set 2, Legendary Collection): each printing (language,
edition, number) links to it. Photo and picture are straightened to the same
size, so keypoints are matched by position too.

Fingerprints come one file per edition, backend/fingerprints/<set_id>.sqlite,
made by jobs/build_fingerprints.py (see SCHEMA). A reprint's file lists its
printings, linked to the illustration kept in the file of the edition that
printed it first. Which illustrations are compared: those printed with the
set size OCR read (/102: Base Set and Triumphant), or in the edition the user
chose; all of them only when nothing at all was read.
"""

import logging
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

POOLS = Path(__file__).resolve().parents[2] / "fingerprints"
FORMAT = "2"

# Where the illustration is, as fractions of the straightened card (left, top,
# right, bottom). A little inside the frame: a photo straightened a few pixels
# off must still have no frame or text in it.
LAYOUTS = {
    # Wizards of the Coast era (Base Set to Neo Destiny, Legendary Collection).
    "wizards-pokemon": (0.12, 0.14, 0.88, 0.47),
    "wizards-trainer": (0.13, 0.26, 0.87, 0.52),  # under 'TRAINER' and the name
    "wizards-energy": (0.08, 0.15, 0.92, 0.70),  # the symbol on its background
    # e-Card (Expedition, Aquapolis, Skyridge): a lower picture, the card-e strip on the side.
    "ecard-pokemon": (0.10, 0.14, 0.90, 0.45),
    "ecard-trainer": (0.12, 0.19, 0.88, 0.47),
    "ecard-energy": (0.10, 0.12, 0.90, 0.62),
    # HeartGold & SoulSilver (Triumphant). Primes share the Pokémon box.
    "hgss-pokemon": (0.08, 0.11, 0.92, 0.52),
    "hgss-trainer": (0.08, 0.15, 0.92, 0.52),
    "hgss-energy": (0.04, 0.09, 0.96, 0.68),
    # Half of a LEGEND: the picture runs nearly edge to edge, the text on it.
    "hgss-legend": (0.12, 0.06, 0.95, 0.94),
}
# TCGdex series -> era (the prefix of the layouts above).
SERIES_ERAS = {"base": "wizards", "gym": "wizards", "neo": "wizards", "lc": "wizards",
               "ecard": "ecard", "hgss": "hgss"}
CATEGORY_LAYOUTS = {"Pokemon": "pokemon", "Trainer": "trainer", "Energy": "energy"}
RARITY_LAYOUTS = {"LEGEND": "legend"}  # cards whose box isn't their category's

ART_WIDTH = 480  # the box is resized to this width before fingerprinting
DETAIL_SIGMA = 6  # px: shading broader than this is left out of the fingerprint
FEATURES = 800  # ORB keypoints kept per illustration
RATIO = 0.8  # Lowe's ratio test: a match must be clearly better than the next one
MIN_MATCHES = 8  # fewer good matches: no transform is estimated
REPROJECTION = 0.03 * ART_WIDTH  # how far an inlier may land from where it should
# The photo's box and the picture's are straightened alike: the transform
# between them is close to the identity (an inner frame found as the card's
# outline makes it ~12% larger).
MAX_SCALE_CHANGE = 0.3
MAX_SHIFT = 0.25  # of the box's width
MAX_ROTATION = 10  # degrees

# A match is trusted with this many keypoints in agreement, and clearly more
# than the next illustration. On 42 real phone photos of Base Set cards, the
# right illustration had 16 to 144, any other at most 6; cards of other sets,
# at most 4 with any Base Set illustration.
MIN_INLIERS = 12
MIN_MARGIN = 2.0
MIN_INLIERS_ANYWHERE = 20  # searching every edition: stricter

SCHEMA = """
CREATE TABLE meta (
    key    TEXT PRIMARY KEY,   -- format, set_id, official_count, source, built_at
    value  TEXT NOT NULL
);

-- The illustrations this edition printed first. Pictures in English: the
-- same illustration is printed in every language of the edition. A card
-- printed only in Japanese, Korean or Chinese has its own row, from a source
-- for that language.
CREATE TABLE artworks (
    artwork_key  TEXT PRIMARY KEY,  -- '<set_id>/<number>' of its first printing, e.g. 'base1/4'
    layout       TEXT NOT NULL,     -- where the box is: a key of artwork.LAYOUTS
    source       TEXT NOT NULL,     -- where the picture came from, e.g. 'pokemontcg.io'
    source_url   TEXT NOT NULL,     -- the picture, for a preview loaded from there
    keypoints    BLOB NOT NULL,     -- float16 x, y per keypoint, in the box at ART_WIDTH
    descriptors  BLOB NOT NULL      -- 32 bytes per keypoint (ORB)
);

-- This edition's printed cards, in each language the edition was printed in.
-- A reprint links to the illustration in the file of the edition that
-- printed it first. Japanese, Korean and Chinese editions have sets and
-- numbers of their own, hence set_id and set_total on each printing.
CREATE TABLE printings (
    artwork_key  TEXT NOT NULL,     -- in this file's artworks or another edition's
    lang         TEXT NOT NULL,     -- 'en', 'it'; later 'ja', 'ko', 'zh'
    set_id       TEXT NOT NULL,     -- the edition in that language (TCGdex id)
    set_total    TEXT,              -- as printed after the slash: '102', 'H32'; NULL if none
    number       TEXT NOT NULL,     -- as printed before it: '14', 'H01'
    card_id      TEXT NOT NULL,     -- catalog id (TCGdex)
    name         TEXT NOT NULL,     -- from that language's official list, never translated
    PRIMARY KEY (lang, set_id, number)
);
CREATE INDEX printings_artwork ON printings (artwork_key);

-- Reserved: a name for a language the card was never printed in, shown
-- marked as not official next to the original.
CREATE TABLE unofficial_names (
    artwork_key  TEXT NOT NULL,
    lang         TEXT NOT NULL,
    name         TEXT NOT NULL,
    method       TEXT NOT NULL,     -- how the translation was made
    PRIMARY KEY (artwork_key, lang)
);
"""


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


@dataclass
class Fingerprint:
    keypoints: np.ndarray  # (n, 2) float32
    descriptors: np.ndarray  # (n, 32) uint8


def layout_for(series_id: str | None, category: str | None,
               rarity: str | None = None) -> str | None:
    """The layout of a card's illustration; None for eras not measured yet."""
    era = SERIES_ERAS.get(series_id or "")
    kind = RARITY_LAYOUTS.get(rarity or "") or CATEGORY_LAYOUTS.get(category or "")
    layout = f"{era}-{kind}" if era and kind else None
    return layout if layout in LAYOUTS else None


def box(card: np.ndarray, layout: str) -> np.ndarray:
    """The illustration of a straightened card, ART_WIDTH wide, in gray."""
    left, top, right, bottom = LAYOUTS[layout]
    height, width = card.shape[:2]
    art = card[int(top * height) : int(bottom * height), int(left * width) : int(right * width)]
    art_height = round(ART_WIDTH * art.shape[0] / art.shape[1])
    art = cv2.resize(art, (ART_WIDTH, art_height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(art, cv2.COLOR_BGR2GRAY)


def detail(gray: np.ndarray) -> np.ndarray:
    """The drawing's lines without its shading: what a photo and a picture share.

    On a holo card the background swings from dark (the picture) to light
    (a photo catching the foil): brightness alone, ORB keypoints of Raichu's
    picture and photos had nothing in common. Its lines stay the same.
    """
    image = gray.astype(np.float32)
    lines = image - cv2.GaussianBlur(image, (0, 0), DETAIL_SIGMA)
    lines = np.clip(lines / (3 * lines.std() + 1e-6), -1, 1)  # the same contrast for all
    return ((lines + 1) * 127.5).astype(np.uint8)


_orb = threading.local()


def _detector() -> cv2.ORB:
    if not hasattr(_orb, "detector"):  # ORB objects aren't shared between threads
        _orb.detector = cv2.ORB_create(nfeatures=FEATURES, edgeThreshold=15, patchSize=31)
    return _orb.detector


def fingerprint(card: np.ndarray, layout: str) -> Fingerprint:
    """The fingerprint of a straightened card's illustration (see detect.CARD_SIZE)."""
    keypoints, descriptors = _detector().detectAndCompute(detail(box(card, layout)), None)
    if descriptors is None:
        keypoints, descriptors = (), np.zeros((0, 32), np.uint8)
    points = np.array([k.pt for k in keypoints], np.float32).reshape(-1, 2)
    return Fingerprint(points, descriptors)


def agreement(photo: Fingerprint, picture: Fingerprint) -> int:
    """How many keypoints of the photo land where the picture has them (0 to FEATURES)."""
    if len(photo.descriptors) < 2 or len(picture.descriptors) < 2:
        return 0
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(photo.descriptors, picture.descriptors, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < RATIO * p[1].distance]
    if len(good) < MIN_MATCHES:
        return 0
    source = photo.keypoints[[m.queryIdx for m in good]]
    target = picture.keypoints[[m.trainIdx for m in good]]
    transform, inliers = cv2.estimateAffinePartial2D(
        source, target, method=cv2.RANSAC, ransacReprojThreshold=REPROJECTION
    )
    if transform is None or inliers is None:
        return 0
    scale = float(np.hypot(transform[0, 0], transform[1, 0]))
    angle = float(np.degrees(np.arctan2(transform[1, 0], transform[0, 0])))
    shift = float(np.hypot(transform[0, 2], transform[1, 2])) / ART_WIDTH
    if abs(scale - 1) > MAX_SCALE_CHANGE or abs(angle) > MAX_ROTATION or shift > MAX_SHIFT:
        return 0
    return int(inliers.sum())


# ---------------------------------------------------------------------------
# Editions' files
# ---------------------------------------------------------------------------


@dataclass
class Printing:
    lang: str
    set_id: str
    set_total: str | None  # as printed: '102', 'H32'
    number: str
    card_id: str
    name: str


@dataclass
class Artwork:
    key: str  # '<set_id>/<number>' of its first printing
    layout: str
    source_url: str
    fingerprint: Fingerprint
    printings: list[Printing] = field(default_factory=list)


def artwork_key(set_id: str, number: str) -> str:
    return f"{set_id}/{number}"


def same_number(a: str | None, b: str | None) -> bool:
    """'025' is 25, 'H01' is H1: printed and catalog numbers differ in zero padding."""
    def plain(text: str) -> str:
        return re.sub(r"(^|[A-Z])0+(\d)", r"\1\2", text.strip().upper())
    return a is not None and b is not None and plain(a) == plain(b)


def write_edition(path: Path, meta: dict[str, str], artworks: list[Artwork],
                  printings: list[tuple[str, Printing]]) -> None:
    """Save an edition: the illustrations it printed first, and every printing
    of it as (artwork key, printing). The file is replaced only once complete."""
    partial = path.with_suffix(".part")
    partial.unlink(missing_ok=True)
    db = sqlite3.connect(partial)
    with db:
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO meta VALUES (?, ?)", {"format": FORMAT, **meta}.items())
        db.executemany(
            "INSERT INTO artworks VALUES (?, ?, ?, ?, ?, ?)",
            [(a.key, a.layout, meta.get("source", ""), a.source_url,
              a.fingerprint.keypoints.astype(np.float16).tobytes(),
              a.fingerprint.descriptors.tobytes()) for a in artworks],
        )
        db.executemany(
            "INSERT INTO printings VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(key, p.lang, p.set_id, p.set_total, p.number, p.card_id, p.name)
             for key, p in printings],
        )
    db.close()
    partial.replace(path)


def read_edition(path: Path) -> tuple[dict[str, str], list[Artwork], list[tuple[str, Printing]]]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        meta = dict(db.execute("SELECT key, value FROM meta"))
        if meta.get("format") != FORMAT:
            raise ValueError(f"format {meta.get('format')}, expected {FORMAT}")
        artworks = [
            Artwork(key, layout, url, Fingerprint(
                np.frombuffer(points, np.float16).astype(np.float32).reshape(-1, 2),
                np.frombuffer(descriptors, np.uint8).reshape(-1, 32)))
            for key, layout, url, points, descriptors in db.execute(
                "SELECT artwork_key, layout, source_url, keypoints, descriptors FROM artworks")
        ]
        printings = [(row[0], Printing(*row[1:])) for row in db.execute(
            "SELECT artwork_key, lang, set_id, set_total, number, card_id, name FROM printings")]
    finally:
        db.close()
    return meta, artworks, printings


_files: dict[Path, tuple[float, tuple]] = {}
_library: tuple[tuple, list[Artwork]] | None = None
_lock = threading.Lock()


def library(folder: Path | None = None) -> list[Artwork]:
    """Every edition's illustrations, each with all its printings (POOLS by default).

    Files are read once, again when they change. A printing whose illustration
    is in no file found (its first edition's file is missing) is left out.
    """
    global _library
    folder = POOLS if folder is None else folder
    with _lock:
        state = []
        for path in sorted(folder.glob("*.sqlite")):
            mtime = path.stat().st_mtime
            if path not in _files or _files[path][0] != mtime:
                try:
                    _files[path] = (mtime, read_edition(path))
                except (sqlite3.Error, ValueError, KeyError) as error:
                    log.warning("Fingerprints %s not usable: %s", path.name, error)
                    continue
            state.append((path, mtime))
        state = tuple(state)
        if _library is None or _library[0] != state:
            artworks: dict[str, Artwork] = {}
            for path, _ in state:
                for art in _files[path][1][1]:
                    artworks[art.key] = Artwork(art.key, art.layout, art.source_url,
                                                art.fingerprint)
            for path, _ in state:
                for key, printing in _files[path][1][2]:
                    if key in artworks:
                        artworks[key].printings.append(printing)
                    else:
                        log.warning("%s: %s links to %s, in no file", path.name,
                                    printing.card_id, key)
            _library = (state, [a for a in artworks.values() if a.printings])
        return _library[1]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


@dataclass
class Match:
    artwork: Artwork
    printings: list[Printing]  # those searched
    inliers: int
    runner_up: int  # the next illustration's inliers


def best_match(card: np.ndarray, artworks: list[Artwork], keep: Callable[[Printing], bool],
               min_inliers: int = MIN_INLIERS) -> Match | None:
    """The illustration the straightened card shows, if clearly one.

    Only illustrations with a printing `keep` accepts are compared (e.g. those
    printed with the set size read), and only those printings returned.
    """
    photo: dict[str, Fingerprint] = {}  # per layout
    scored: list[tuple[int, Artwork, list[Printing]]] = []
    for art in artworks:
        printings = [p for p in art.printings if keep(p)]
        if not printings:
            continue
        if art.layout not in photo:
            photo[art.layout] = fingerprint(card, art.layout)
        scored.append((agreement(photo[art.layout], art.fingerprint), art, printings))
    if not scored:
        return None
    scored.sort(key=lambda s: s[0], reverse=True)
    inliers, art, printings = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if inliers < min_inliers or inliers < MIN_MARGIN * runner_up:
        return None
    return Match(art, printings, inliers, runner_up)
