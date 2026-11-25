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
chose; all of them only when nothing at all was read, and then only the
SHORTLIST an index of visual words finds most alike (Shortlist).

Only the index and the printings stay in memory; a fingerprint is read from
its file when compared, the LOADED last read kept.
"""

import hashlib
import logging
import re
import sqlite3
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.recognition.detect import CARD_SIZE

log = logging.getLogger(__name__)

POOLS = Path(__file__).resolve().parents[2] / "fingerprints"
FORMAT = "3"

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
    # EX (Ruby & Sapphire to Power Keepers), the ex and Star cards too.
    "ex-pokemon": (0.10, 0.11, 0.90, 0.45),
    "ex-trainer": (0.11, 0.15, 0.89, 0.47),
    "ex-energy": (0.08, 0.12, 0.92, 0.65),
    # Diamond & Pearl and Platinum, LV.X too.
    "dp-pokemon": (0.09, 0.13, 0.91, 0.48),
    "dp-trainer": (0.10, 0.17, 0.89, 0.49),
    "dp-energy": (0.08, 0.12, 0.92, 0.65),
    # Black & White and XY: one frame.
    "bw-pokemon": (0.10, 0.12, 0.90, 0.48),
    "bw-trainer": (0.10, 0.18, 0.90, 0.50),
    "bw-energy": (0.08, 0.15, 0.92, 0.60),
    # Evolutions (XY): Base Set's frame redrawn, a larger picture than Base Set's.
    "evolutions-pokemon": (0.10, 0.09, 0.90, 0.46),
    "evolutions-trainer": (0.11, 0.24, 0.89, 0.55),
    "evolutions-energy": (0.08, 0.15, 0.92, 0.70),
    # Sun & Moon to Mega Evolution: one frame, within a point or two.
    "modern-pokemon": (0.08, 0.10, 0.92, 0.46),
    "modern-trainer": (0.08, 0.16, 0.92, 0.51),
    "modern-energy": (0.04, 0.13, 0.96, 0.63),
}
# Full-art cards (ex, GX, V, illustration rares, full-art trainers) have the
# picture over the whole card: their era's box is used, the part of the
# picture with the least text on it (the name above, the attacks below).

ART_WIDTH = 480  # the box is resized to this width before fingerprinting
DETAIL_SIGMA = 6  # px: shading broader than this is left out of the fingerprint
# ORB keypoints kept per illustration. On the 44 real photos of Base Set cards,
# 500 recognized every card, as 800 did, 400 missed Raichu twice.
FEATURES = 500
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

# Comparing a photo with every illustration takes ~2 ms each: too slow for
# all of them (some 20,000) when no code was read. An index picks the few
# most alike first (Shortlist): each keypoint is given the nearest of
# VOCABULARY_SIZE reference descriptors (its "visual word") and its place in
# the box (a SHORTLIST_GRID x SHORTLIST_GRID grid); illustrations sharing the
# photo's rarest words, in the same places, come first. On 100 simulated
# photos against the 1,523 Wizards-era illustrations, the right one ranked
# first 95 times, never below 8th (4,096 words or a 3 x 3 grid: 99 first,
# but one photo straightened a little off ranked 913th).
VOCABULARY = "vocabulary.bin"  # in the fingerprints' folder: VOCABULARY_SIZE x 32 bytes
VOCABULARY_SIZE = 2048
SHORTLIST_GRID = 2
SHORTLIST = 100  # illustrations compared, at most, when more are searched
LOADED = 4000  # fingerprints kept in memory; the others are read when needed

SCHEMA = """
CREATE TABLE meta (
    key    TEXT PRIMARY KEY,   -- format, set_id, official_count, source, built_at, vocabulary
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
    descriptors  BLOB NOT NULL,     -- 32 bytes per keypoint (ORB)
    words        BLOB NOT NULL      -- uint16 per keypoint: its visual word; empty without
                                    -- a vocabulary (meta 'vocabulary': which one)
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
# Visual words, for the shortlist
# ---------------------------------------------------------------------------


def read_vocabulary(folder: Path) -> np.ndarray | None:
    """The reference descriptors of the fingerprints in `folder`, if it has them."""
    path = folder / VOCABULARY
    if not path.exists():
        return None
    return np.frombuffer(path.read_bytes(), np.uint8).reshape(-1, 32)


def vocabulary_id(vocabulary: np.ndarray | None) -> str:
    """What a file's words were given with (meta 'vocabulary'): '' for none."""
    if vocabulary is None:
        return ""
    return hashlib.sha1(vocabulary.tobytes()).hexdigest()[:16]


def words(descriptors: np.ndarray, vocabulary: np.ndarray) -> np.ndarray:
    """Each keypoint's visual word: the nearest reference descriptor (uint16)."""
    if len(descriptors) == 0:
        return np.zeros(0, np.uint16)
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).match(descriptors, vocabulary)
    found = np.zeros(len(descriptors), np.uint16)
    found[[m.queryIdx for m in matches]] = [m.trainIdx for m in matches]
    return found


def terms(keypoints: np.ndarray, found: np.ndarray, layout: str) -> np.ndarray:
    """The distinct (place, word) pairs of an illustration, as numbers."""
    if len(found) == 0:
        return np.zeros(0, np.int64)
    width, height = _box_size(layout)
    column = np.clip((keypoints[:, 0] / width * SHORTLIST_GRID).astype(int), 0, SHORTLIST_GRID - 1)
    row = np.clip((keypoints[:, 1] / height * SHORTLIST_GRID).astype(int), 0, SHORTLIST_GRID - 1)
    place = row * SHORTLIST_GRID + column
    return np.unique(place.astype(np.int64) * VOCABULARY_SIZE + found)


def _box_size(layout: str) -> tuple[float, float]:
    """The box's width and height once resized to ART_WIDTH (see box)."""
    left, top, right, bottom = LAYOUTS[layout]
    card_width, card_height = CARD_SIZE
    return ART_WIDTH, ART_WIDTH * (bottom - top) * card_height / ((right - left) * card_width)


class Shortlist:
    """Illustrations ranked by the visual words they share with a photo (TF-IDF).

    Words are compared only between the photo's box and illustrations of the
    same layout. `slots[i]` is the i-th illustration indexed.
    """

    def __init__(self, layouts: list[str], documents: list[tuple[str, np.ndarray]],
                 vocabulary: np.ndarray):
        self.vocabulary = vocabulary
        self.layouts = {layout: i for i, layout in enumerate(layouts)}
        span = SHORTLIST_GRID * SHORTLIST_GRID * VOCABULARY_SIZE
        keys = [self.layouts[layout] * span + found for layout, found in documents]
        sizes = np.array([len(k) for k in keys])
        every = np.concatenate(keys) if keys else np.zeros(0, np.int64)
        owners = np.repeat(np.arange(len(keys), dtype=np.int32), sizes)
        order = np.argsort(every, kind="stable")
        self.owners = owners[order]
        total = len(self.layouts) * span
        self.starts = np.searchsorted(every[order], np.arange(total + 1))
        frequency = np.diff(self.starts)
        self.idf = np.log((len(keys) + 1) / (frequency + 1)).astype(np.float32)
        self.norms = np.array([np.sqrt((self.idf[k] ** 2).sum()) for k in keys], np.float32)
        self.span, self.size = span, len(keys)

    def scores(self, photo: dict[str, Fingerprint]) -> np.ndarray:
        """How alike each illustration indexed is to the photo (per layout's box)."""
        scores = np.zeros(self.size, np.float32)
        for layout, fingerprint in photo.items():
            if layout not in self.layouts:
                continue
            asked = (self.layouts[layout] * self.span
                     + terms(fingerprint.keypoints, words(fingerprint.descriptors,
                                                         self.vocabulary), layout))
            if len(asked) == 0:
                continue
            weight = self.idf[asked] ** 2
            lengths = self.starts[asked + 1] - self.starts[asked]
            found = np.concatenate([self.owners[self.starts[k]:self.starts[k + 1]] for k in asked])
            scores += (np.bincount(found, weights=np.repeat(weight, lengths), minlength=self.size)
                       / (np.sqrt(weight.sum()) + 1e-9)).astype(np.float32)
        return scores / (self.norms + 1e-9)


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


@dataclass(eq=False)
class Artwork:
    key: str  # '<set_id>/<number>' of its first printing
    layout: str
    source_url: str
    stored: Fingerprint | None = None  # None: read from `file` when needed
    printings: list[Printing] = field(default_factory=list)
    file: Path | None = None
    slot: int | None = None  # its place in the library's Shortlist

    @property
    def fingerprint(self) -> Fingerprint:
        return self.stored if self.stored is not None else loaded([self])[0]


class Library(list):
    """The illustrations of every edition, with the index to shortlist them."""

    shortlist: Shortlist | None = None


def artwork_key(set_id: str, number: str) -> str:
    return f"{set_id}/{number}"


def same_number(a: str | None, b: str | None) -> bool:
    """'025' is 25, 'H01' is H1: printed and catalog numbers differ in zero padding."""
    def plain(text: str) -> str:
        return re.sub(r"(^|[A-Z])0+(\d)", r"\1\2", text.strip().upper())
    return a is not None and b is not None and plain(a) == plain(b)


def write_edition(path: Path, meta: dict[str, str], artworks: list[Artwork],
                  printings: list[tuple[str, Printing]],
                  vocabulary: np.ndarray | None = None) -> None:
    """Save an edition: the illustrations it printed first (with their visual
    words, given a vocabulary), and every printing of it as (artwork key,
    printing). The file is replaced only once complete."""
    partial = path.with_suffix(".part")
    partial.unlink(missing_ok=True)
    db = sqlite3.connect(partial)
    with db:
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO meta VALUES (?, ?)",
                       {**meta, "format": FORMAT,
                        "vocabulary": vocabulary_id(vocabulary)}.items())
        rows = []
        for a in artworks:
            taken = a.fingerprint
            found = (words(taken.descriptors, vocabulary) if vocabulary is not None
                     else np.zeros(0, np.uint16))
            rows.append((a.key, a.layout, meta.get("source", ""), a.source_url,
                         taken.keypoints.astype(np.float16).tobytes(),
                         taken.descriptors.tobytes(), found.astype(np.uint16).tobytes()))
        db.executemany("INSERT INTO artworks VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        db.executemany(
            "INSERT INTO printings VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(key, p.lang, p.set_id, p.set_total, p.number, p.card_id, p.name)
             for key, p in printings],
        )
    db.close()
    partial.replace(path)


def _decoded(points: bytes, descriptors: bytes) -> Fingerprint:
    return Fingerprint(np.frombuffer(points, np.float16).astype(np.float32).reshape(-1, 2),
                       np.frombuffer(descriptors, np.uint8).reshape(-1, 32))


def _open(path: Path, formats: tuple[str, ...]) -> tuple[sqlite3.Connection, dict[str, str]]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    meta = dict(db.execute("SELECT key, value FROM meta"))
    if meta.get("format") not in formats:
        db.close()
        raise ValueError(f"format {meta.get('format')}, expected {FORMAT}")
    return db, meta


def _printings(db: sqlite3.Connection) -> list[tuple[str, Printing]]:
    return [(row[0], Printing(*row[1:])) for row in db.execute(
        "SELECT artwork_key, lang, set_id, set_total, number, card_id, name FROM printings")]


def read_edition(path: Path, formats: tuple[str, ...] = (FORMAT,)
                 ) -> tuple[dict[str, str], list[Artwork], list[tuple[str, Printing]]]:
    """An edition's file, fingerprints included (to rewrite it: `formats`
    may accept older ones)."""
    db, meta = _open(path, formats)
    try:
        artworks = [Artwork(key, layout, url, _decoded(points, descriptors), file=path)
                    for key, layout, url, points, descriptors in db.execute(
                        "SELECT artwork_key, layout, source_url, keypoints, descriptors"
                        " FROM artworks")]
        printings = _printings(db)
    finally:
        db.close()
    return meta, artworks, printings


@dataclass
class _Edition:
    meta: dict[str, str]
    artworks: list[tuple[str, str, str]]  # key, layout, source url
    documents: list[np.ndarray | None]  # each illustration's terms; None without words
    printings: list[tuple[str, Printing]]


def _read_index(path: Path, vocabulary: np.ndarray | None) -> _Edition:
    """An edition's file without its fingerprints: only what the index needs."""
    db, meta = _open(path, (FORMAT,))
    usable = vocabulary is not None and meta.get("vocabulary") == vocabulary_id(vocabulary)
    if vocabulary is not None and not usable:
        log.warning("Fingerprints %s: visual words of another vocabulary, never shortlisted"
                    " out (rebuild with --reindex)", path.name)
    try:
        artworks, documents = [], []
        for key, layout, url, points, found in db.execute(
                "SELECT artwork_key, layout, source_url, keypoints, words FROM artworks"):
            artworks.append((key, layout, url))
            documents.append(terms(np.frombuffer(points, np.float16).astype(np.float32)
                                   .reshape(-1, 2), np.frombuffer(found, np.uint16), layout)
                             if usable and found and layout in LAYOUTS else None)
        printings = _printings(db)
    finally:
        db.close()
    return _Edition(meta, artworks, documents, printings)


_cache: OrderedDict[tuple[Path, str], Fingerprint] = OrderedDict()
_cache_lock = threading.Lock()


def loaded(artworks: list[Artwork]) -> list[Fingerprint]:
    """The fingerprints of these illustrations, read from their files if not
    in memory; the LOADED last read are kept."""
    found: dict[tuple[Path | None, str], Fingerprint] = {}
    asked: dict[Path, list[str]] = defaultdict(list)
    with _cache_lock:
        for art in artworks:
            if art.stored is not None or art.file is None:
                continue
            key = (art.file, art.key)
            if key in _cache:
                _cache.move_to_end(key)
                found[key] = _cache[key]
            else:
                asked[art.file].append(art.key)
    for path, keys in asked.items():
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for start in range(0, len(keys), 500):
                chunk = keys[start:start + 500]
                for key, points, descriptors in db.execute(
                        "SELECT artwork_key, keypoints, descriptors FROM artworks"
                        f" WHERE artwork_key IN ({','.join('?' * len(chunk))})", chunk):
                    found[(path, key)] = _decoded(points, descriptors)
        finally:
            db.close()
        with _cache_lock:
            for key in keys:
                if (path, key) in found:
                    _cache[(path, key)] = found[(path, key)]
            while len(_cache) > LOADED:
                _cache.popitem(last=False)
    empty = Fingerprint(np.zeros((0, 2), np.float32), np.zeros((0, 32), np.uint8))
    return [art.stored if art.stored is not None else found.get((art.file, art.key), empty)
            for art in artworks]


_files: dict[Path, tuple[float, str, _Edition]] = {}  # mtime, vocabulary id, contents
_library: tuple[tuple, Library] | None = None
_lock = threading.Lock()


def library(folder: Path | None = None) -> Library:
    """Every edition's illustrations, each with all its printings (POOLS by default).

    Files are read once, again when they change; fingerprints are read only
    when compared (see loaded). A printing whose illustration is in no file
    found (its first edition's file is missing) is left out.
    """
    global _library
    folder = POOLS if folder is None else folder
    with _lock:
        vocabulary = read_vocabulary(folder)
        words_of = vocabulary_id(vocabulary)
        state = [("vocabulary", words_of)]
        for path in sorted(folder.glob("*.sqlite")):
            mtime = path.stat().st_mtime
            if path not in _files or _files[path][:2] != (mtime, words_of):
                with _cache_lock:  # fingerprints of the file before
                    for key in [k for k in _cache if k[0] == path]:
                        del _cache[key]
                try:
                    _files[path] = (mtime, words_of, _read_index(path, vocabulary))
                except (sqlite3.Error, ValueError, KeyError) as error:
                    _files.pop(path, None)
                    log.warning("Fingerprints %s not usable: %s", path.name, error)
                    continue
            state.append((path, mtime))
        state = tuple(state)
        if _library is None or _library[0] != state:
            artworks: dict[str, Artwork] = {}
            documents: dict[str, np.ndarray | None] = {}
            for path, _ in state[1:]:
                edition = _files[path][2]
                for (key, layout, url), terms_ in zip(edition.artworks, edition.documents):
                    artworks[key] = Artwork(key, layout, url, file=path)
                    documents[key] = terms_
            for path, _ in state[1:]:
                for key, printing in _files[path][2].printings:
                    if key in artworks:
                        artworks[key].printings.append(printing)
                    else:
                        log.warning("%s: %s links to %s, in no file", path.name,
                                    printing.card_id, key)
            found = Library(a for a in artworks.values() if a.printings)
            indexed = [a for a in found if documents[a.key] is not None]
            if vocabulary is not None and indexed:
                for slot, art in enumerate(indexed):
                    art.slot = slot
                found.shortlist = Shortlist(sorted({a.layout for a in indexed}),
                                            [(a.layout, documents[a.key]) for a in indexed],
                                            vocabulary)
            _library = (state, found)
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
    printed with the set size read), and only those printings returned. More
    than SHORTLIST of them: only the SHORTLIST the library's index finds most
    alike (and any it doesn't index).
    """
    photo: dict[str, Fingerprint] = {}  # per layout
    candidates: list[tuple[Artwork, list[Printing]]] = []
    for art in artworks:
        printings = [p for p in art.printings if keep(p)]
        if printings:
            candidates.append((art, printings))
            if art.layout not in photo:
                photo[art.layout] = fingerprint(card, art.layout)
    if not candidates:
        return None
    shortlist = getattr(artworks, "shortlist", None)
    if shortlist is not None and len(candidates) > SHORTLIST:
        scores = shortlist.scores(photo)
        ranked = [c for c in candidates if c[0].slot is not None]
        cut = sorted((scores[c[0].slot] for c in ranked), reverse=True)[SHORTLIST - 1] \
            if len(ranked) >= SHORTLIST else -1.0
        candidates = [c for c in candidates if c[0].slot is None or scores[c[0].slot] >= cut]
    prints = loaded([art for art, _ in candidates])
    scored = sorted(((agreement(photo[art.layout], print_), art, printings)
                     for (art, printings), print_ in zip(candidates, prints)),
                    key=lambda s: s[0], reverse=True)
    inliers, art, printings = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if inliers < min_inliers or inliers < MIN_MARGIN * runner_up:
        return None
    return Match(art, printings, inliers, runner_up)
