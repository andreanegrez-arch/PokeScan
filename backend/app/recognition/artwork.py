"""Recognize a card by its illustration, to check the number OCR read.

The code at the bottom of old cards is small print, and OCR misreads it:
Raichu's 14/102 read as 4/102 would make Charizard. So the illustration, alone,
is compared with the illustrations of the edition the code points to: it
confirms the number read, or corrects it.

An illustration is kept as a fingerprint, never as a picture:
- the artwork box only (LAYOUTS), so that no printed text, which changes with
  the language, is in it: one fingerprint serves every language the card was
  printed in;
- a perceptual hash (DCT of the box, 64 bits), and ORB keypoints: corners of
  the drawing with a 32-byte descriptor each, matched by position too, since
  both the photo and the picture are straightened to the same size.

Fingerprints come one file per edition, backend/fingerprints/<set_id>.sqlite,
made by jobs/build_fingerprints.py (see SCHEMA). An edition is chosen by the
set size OCR read (/102: Base Set), or by the user; only when nothing at all
was read are all editions searched.
"""

import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

POOLS = Path(__file__).resolve().parents[2] / "fingerprints"
FORMAT = "1"

# Where the illustration is, as fractions of the straightened card (left, top,
# right, bottom). A little inside the frame: a photo straightened a few pixels
# off must still have no frame or text in it.
LAYOUTS = {
    # Wizards of the Coast era (Base Set to Neo, Legendary Collection).
    "wizards-pokemon": (0.12, 0.14, 0.88, 0.47),
    "wizards-trainer": (0.13, 0.26, 0.87, 0.52),  # under 'TRAINER' and the name
    "wizards-energy": (0.08, 0.15, 0.92, 0.70),  # the symbol on its background
}
# TCGdex series -> era (the prefix of the layouts above).
SERIES_ERAS = {"base": "wizards", "gym": "wizards", "neo": "wizards", "lc": "wizards"}
CATEGORY_LAYOUTS = {"Pokemon": "pokemon", "Trainer": "trainer", "Energy": "energy"}

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

-- One row per illustration. Pictures in English: the same illustration is
-- printed in every language of the edition. A card printed only in Japanese,
-- Korean or Chinese has its own row, from a source for that language.
CREATE TABLE artworks (
    artwork_id   INTEGER PRIMARY KEY,
    layout       TEXT NOT NULL,     -- where the box is: a key of artwork.LAYOUTS
    source       TEXT NOT NULL,     -- where the picture came from, e.g. 'pokemontcg.io'
    source_url   TEXT NOT NULL,     -- the picture, for a preview loaded from there
    phash        BLOB NOT NULL,     -- 8 bytes
    keypoints    BLOB NOT NULL,     -- float16 x, y per keypoint, in the box at ART_WIDTH
    descriptors  BLOB NOT NULL      -- 32 bytes per keypoint (ORB)
);

-- The printed cards that carry an illustration: one per language it was
-- printed in. Japanese, Korean and Chinese editions have sets and numbers of
-- their own, hence set_id and set_total on each printing.
CREATE TABLE printings (
    artwork_id  INTEGER NOT NULL REFERENCES artworks (artwork_id),
    lang        TEXT NOT NULL,      -- 'en', 'it'; later 'ja', 'ko', 'zh'
    set_id      TEXT NOT NULL,      -- the edition in that language (TCGdex id)
    set_total   INTEGER,            -- printed after the slash, e.g. 102
    number      TEXT NOT NULL,      -- printed before it
    card_id     TEXT NOT NULL,      -- catalog id (TCGdex)
    name        TEXT NOT NULL,      -- from that language's official list, never translated
    PRIMARY KEY (lang, set_id, number)
);
CREATE INDEX printings_artwork ON printings (artwork_id);

-- Reserved: a name for a language the card was never printed in, shown
-- marked as not official next to the original.
CREATE TABLE unofficial_names (
    artwork_id  INTEGER NOT NULL REFERENCES artworks (artwork_id),
    lang        TEXT NOT NULL,
    name        TEXT NOT NULL,
    method      TEXT NOT NULL,      -- how the translation was made
    PRIMARY KEY (artwork_id, lang)
);
"""


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


@dataclass
class Fingerprint:
    phash: bytes  # 8 bytes
    keypoints: np.ndarray  # (n, 2) float32
    descriptors: np.ndarray  # (n, 32) uint8


def layout_for(series_id: str | None, category: str | None) -> str | None:
    """The layout of a card's illustration; None for eras not measured yet."""
    era, kind = SERIES_ERAS.get(series_id or ""), CATEGORY_LAYOUTS.get(category or "")
    return f"{era}-{kind}" if era and kind else None


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


def phash(gray: np.ndarray) -> bytes:
    """64-bit perceptual hash: low frequencies of the DCT above or below their median."""
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:8, :8].flatten()[1:]  # without the mean (DC)
    bits = np.append(low > np.median(low), False)
    return np.packbits(bits).tobytes()


def hamming(a: bytes, b: bytes) -> int:
    return int(np.unpackbits(np.frombuffer(a, np.uint8) ^ np.frombuffer(b, np.uint8)).sum())


_orb = threading.local()


def _detector() -> cv2.ORB:
    if not hasattr(_orb, "detector"):  # ORB objects aren't shared between threads
        _orb.detector = cv2.ORB_create(nfeatures=FEATURES, edgeThreshold=15, patchSize=31)
    return _orb.detector


def fingerprint(card: np.ndarray, layout: str) -> Fingerprint:
    """The fingerprint of a straightened card's illustration (see detect.CARD_SIZE)."""
    gray = box(card, layout)
    keypoints, descriptors = _detector().detectAndCompute(detail(gray), None)
    if descriptors is None:
        keypoints, descriptors = (), np.zeros((0, 32), np.uint8)
    points = np.array([k.pt for k in keypoints], np.float32).reshape(-1, 2)
    return Fingerprint(phash(gray), points, descriptors)


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
# Editions (pools of fingerprints)
# ---------------------------------------------------------------------------


@dataclass
class Printing:
    lang: str
    set_id: str
    set_total: int | None
    number: str
    card_id: str
    name: str


@dataclass
class Artwork:
    artwork_id: int
    layout: str
    source_url: str
    fingerprint: Fingerprint
    printings: list[Printing] = field(default_factory=list)


@dataclass
class Pool:
    set_id: str
    official_count: int | None
    artworks: list[Artwork]

    @property
    def totals(self) -> set[int]:
        return {p.set_total for a in self.artworks for p in a.printings if p.set_total}


def write_pool(path: Path, meta: dict[str, str], artworks: list[Artwork]) -> None:
    """Save an edition's fingerprints (replacing the file only once complete)."""
    partial = path.with_suffix(".part")
    partial.unlink(missing_ok=True)
    with sqlite3.connect(partial) as db:
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO meta VALUES (?, ?)", {"format": FORMAT, **meta}.items())
        for art in artworks:
            fp = art.fingerprint
            db.execute(
                "INSERT INTO artworks VALUES (?, ?, ?, ?, ?, ?, ?)",
                (art.artwork_id, art.layout, meta.get("source", ""), art.source_url, fp.phash,
                 fp.keypoints.astype(np.float16).tobytes(), fp.descriptors.tobytes()),
            )
            db.executemany(
                "INSERT INTO printings VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(art.artwork_id, p.lang, p.set_id, p.set_total, p.number, p.card_id, p.name)
                 for p in art.printings],
            )
    db.close()
    partial.replace(path)


def read_pool(path: Path) -> Pool:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        meta = dict(db.execute("SELECT key, value FROM meta"))
        if meta.get("format") != FORMAT:
            raise ValueError(f"{path.name}: format {meta.get('format')}, expected {FORMAT}")
        artworks = {
            row[0]: Artwork(
                artwork_id=row[0], layout=row[1], source_url=row[2],
                fingerprint=Fingerprint(
                    phash=row[3],
                    keypoints=np.frombuffer(row[4], np.float16).astype(np.float32).reshape(-1, 2),
                    descriptors=np.frombuffer(row[5], np.uint8).reshape(-1, 32),
                ),
            )
            for row in db.execute(
                "SELECT artwork_id, layout, source_url, phash, keypoints, descriptors"
                " FROM artworks"
            )
        }
        for row in db.execute(
            "SELECT artwork_id, lang, set_id, set_total, number, card_id, name FROM printings"
        ):
            artworks[row[0]].printings.append(Printing(*row[1:]))
    db.close()
    count = meta.get("official_count")
    return Pool(meta["set_id"], int(count) if count else None, list(artworks.values()))


_pools: dict[Path, tuple[float, Pool]] = {}
_pools_lock = threading.Lock()


def pools(folder: Path | None = None) -> list[Pool]:
    """Every edition with fingerprints (in POOLS), read once (again if its file changes)."""
    folder = POOLS if folder is None else folder
    found = []
    with _pools_lock:
        for path in sorted(folder.glob("*.sqlite")):
            mtime = path.stat().st_mtime
            cached = _pools.get(path)
            if cached is None or cached[0] != mtime:
                try:
                    _pools[path] = (mtime, read_pool(path))
                except (sqlite3.Error, ValueError, KeyError) as error:
                    log.warning("Fingerprints %s not usable: %s", path.name, error)
                    continue
            found.append(_pools[path][1])
    return found


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


@dataclass
class Match:
    artwork: Artwork
    printings: list[Printing]  # of the editions searched
    inliers: int
    runner_up: int  # the next illustration's inliers
    distance: int  # perceptual hash distance (0-64), for the record


def best_match(card: np.ndarray, editions: list[Pool], total: int | None = None,
               min_inliers: int = MIN_INLIERS) -> Match | None:
    """The illustration of these editions the straightened card shows, if clearly one.

    `total`: only printings of an edition of that size are returned.
    """
    photo: dict[str, Fingerprint] = {}  # per layout
    scored: list[tuple[int, Artwork, list[Printing]]] = []
    for pool in editions:
        for art in pool.artworks:
            printings = [p for p in art.printings if total is None or p.set_total == total]
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
    distance = hamming(photo[art.layout].phash, art.fingerprint.phash)
    return Match(art, printings, inliers, runner_up, distance)
