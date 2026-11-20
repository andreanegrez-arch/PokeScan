"""Measure how well the scan recognizes cards and measures their centering.

Takes a random sample of catalog cards and, for each, turns TCGdex's image of
the card into a phone-like photo: random background, tilt, perspective,
lighting, blur, noise and JPEG compression. Then scans it and checks the
answer against the card it came from, and the front's centering against the
one measured on the clean image.

Only a stand-in for real photos: no glare, sleeves, fingers or worn cards.
TCGdex images are downloaded once into backend/.cache/scan_evaluation/.

Run from the backend folder:
    python -m jobs.evaluate_scan --cards 300
Results: a summary in the console, one row per card in
analysis/data/scan_evaluation.csv.
"""

import argparse
import csv
import logging
import statistics
import sys
import time
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import httpx
import numpy as np

from app.db import connect
from app.recognition import condition
from app.recognition.detect import CARD_RATIO, as_card, decode
from app.recognition.identify import identify

log = logging.getLogger("evaluate_scan")

BACKEND = Path(__file__).resolve().parents[1]
CACHE = BACKEND / ".cache" / "scan_evaluation"
RESULTS = BACKEND.parent / "analysis" / "data" / "scan_evaluation.csv"

# How each scan ended, best first.
OUTCOMES = {
    "correct": "identified the right card",
    "choice": "several candidates, the right one among them",
    "wrong": "identified another card",
    "choice_without": "several candidates, the right one missing",
    "not_found": "code read, no card has it",
    "unreadable": "no code read",
}


# ---------------------------------------------------------------------------
# Synthetic photos
# ---------------------------------------------------------------------------


def _background(rng: np.random.Generator, width: int, height: int) -> np.ndarray:
    """A table, a mat or a sheet: plain, a gradient, or a blurred texture."""
    kind = rng.integers(3)
    if kind == 0:
        image = np.full((height, width, 3), rng.integers(0, 256, 3), np.float32)
    elif kind == 1:
        start, end = rng.integers(0, 256, 3), rng.integers(0, 256, 3)
        ramp = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
        image = np.broadcast_to(start + (end - start) * ramp, (height, width, 3)).copy()
    else:
        noise = rng.random((height // 16, width // 16, 3)).astype(np.float32)
        noise = cv2.resize(noise, (width, height), interpolation=cv2.INTER_CUBIC)
        image = rng.integers(0, 200, 3) + noise * rng.integers(30, 120)
    return image.astype(np.float32)


def make_photo(
    card: np.ndarray,
    rng: np.random.Generator,
    *,
    turn: int | None = None,
    background: tuple[int, int, int] | None = None,
) -> bytes:
    """A phone-like JPEG photo of `card` (BGR, or BGRA with rounded corners).

    `turn`: 0, 90, 180 or 270 degrees, before a random tilt; random if None.
    `background`: a plain BGR color; random if None.
    """
    width, height = (1200, 1600) if rng.random() < 0.8 else (1600, 1200)
    if background is None:
        photo = _background(rng, width, height)
    else:
        photo = np.full((height, width, 3), background, np.float32)

    card_h, card_w = card.shape[:2]
    if turn is None:
        turn = int(rng.choice([0, 0, 0, 0, 0, 0, 90, 180, 270]))
    angle = np.deg2rad(turn + rng.uniform(-15, 15))
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])

    size = rng.uniform(0.55, 0.85) * height  # card height in the photo
    corners = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float64) - 0.5
    corners *= [size * CARD_RATIO, size]
    corners += rng.uniform(-0.05, 0.05, (4, 2)) * size  # perspective
    corners = corners @ rotation.T
    # Shrink if it doesn't fit, then place it off-center.
    span = corners.max(axis=0) - corners.min(axis=0)
    corners *= min(1.0, 0.92 * min(width / span[0], height / span[1]))
    room = (np.array([width, height]) - (corners.max(axis=0) - corners.min(axis=0))) / 2
    corners += [width / 2, height / 2] + rng.uniform(-1, 1, 2) * room * 0.8

    source = np.array([[0, 0], [card_w, 0], [card_w, card_h], [0, card_h]], np.float32)
    matrix = cv2.getPerspectiveTransform(source, corners.astype(np.float32))
    alpha = card[:, :, 3] if card.shape[2] == 4 else np.full((card_h, card_w), 255, np.uint8)
    warped = cv2.warpPerspective(card[:, :, :3], matrix, (width, height)).astype(np.float32)
    mask = cv2.warpPerspective(alpha, matrix, (width, height)).astype(np.float32)[..., None] / 255
    photo = photo * (1 - mask) + warped * mask

    # Light: exposure, an uneven gradient across the photo, sensor noise, focus.
    photo = photo * rng.uniform(0.75, 1.15) + rng.uniform(-20, 20)
    gradient = np.linspace(-1, 1, width, dtype=np.float32)[None, :, None]
    photo += gradient * rng.uniform(0, 30)
    photo += rng.normal(0, rng.uniform(1, 5), photo.shape)
    sigma = rng.uniform(0, 1.3)
    if sigma > 0.3:
        photo = cv2.GaussianBlur(photo, (0, 0), sigma)

    quality = int(rng.integers(60, 92))
    ok, jpeg = cv2.imencode(".jpg", np.clip(photo, 0, 255).astype(np.uint8),
                            [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return jpeg.tobytes()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

SAMPLE_QUERY = """
SELECT c.card_id, t.lang, t.image_url, s.series_name
FROM cards c
JOIN sets s ON s.set_id = c.set_id
JOIN card_translations t ON t.card_id = c.card_id
WHERE t.image_url IS NOT NULL
ORDER BY c.card_id, t.lang
"""


def sample_cards(conn, count: int, rng: np.random.Generator) -> list[tuple]:
    """`count` random cards, each in one of the languages it has an image in."""
    by_card: dict[str, list[tuple]] = defaultdict(list)
    for row in conn.execute(SAMPLE_QUERY):
        by_card[row[0]].append(row)
    card_ids = sorted(by_card)
    chosen = rng.choice(len(card_ids), size=min(count, len(card_ids)), replace=False)
    return [by_card[card_ids[i]][rng.integers(len(by_card[card_ids[i]]))] for i in chosen]


def download(client: httpx.Client, card_id: str, lang: str, image_url: str) -> bytes | None:
    path = CACHE / lang / f"{card_id}.png"
    if not path.exists():
        response = client.get(f"{image_url}/high.png")
        if response.status_code != 200:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    return path.read_bytes()


def outcome(card_id: str, result) -> str:
    if result.status == "matched":
        return "correct" if result.match.card_id == card_id else "wrong"
    if result.status == "ambiguous":
        found = any(c.card_id == card_id for c in result.candidates)
        return "choice" if found else "choice_without"
    return result.status


def clean_centering(card: np.ndarray):
    """Centering measured on TCGdex's image: None without a plain border."""
    cropped = as_card(card[:, :, :3])
    borders = condition.measure_borders(cropped, None) if cropped is not None else None
    return condition.centering(borders) if borders is not None else None


def centering_columns(truth, measured) -> dict:
    """Both centerings, and the error: the worse of left-right and top-bottom, in points."""
    error = ""
    if truth and measured:
        error = round(max(abs(truth.left_pct - measured.left_pct),
                          abs(truth.top_pct - measured.top_pct)), 1)
    return {
        "clean_left_pct": truth.left_pct if truth else "",
        "clean_top_pct": truth.top_pct if truth else "",
        "photo_left_pct": measured.left_pct if measured else "",
        "photo_top_pct": measured.top_pct if measured else "",
        "centering_error": error,
    }


def run(count: int, seed: int, clean: bool) -> int:
    rng = np.random.default_rng(seed)
    with connect() as conn:
        sample = sample_cards(conn, count, rng)
        log.info("Downloading %d card images (cached after the first run)", len(sample))
        with httpx.Client(timeout=30, headers={"User-Agent": "PokeScan evaluation"}) as client:
            with ThreadPoolExecutor(6) as pool:
                images = list(pool.map(lambda row: download(client, *row[:3]), sample))

        rows = []
        for (card_id, lang, _, series), image in zip(sample, images):
            if image is None:
                continue
            card = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_UNCHANGED)
            if card is None or card.ndim != 3:
                continue
            # One generator per card: any card's photo can be made again.
            photo_rng = np.random.default_rng([seed, zlib.crc32(card_id.encode())])
            photo = image if clean else make_photo(card, photo_rng)
            started = time.perf_counter()
            # The photo is made from the catalog's own picture: aligned to it
            # (see condition.borders_by_reference), it would match perfectly.
            # Measured from the photo alone, as on a card unlike its picture.
            result = identify(conn, decode(photo), lang, pictures=False)
            seconds = time.perf_counter() - started
            measured = result.condition.front_centering if result.condition else None
            rows.append({
                "card_id": card_id,
                "lang": lang,
                "series": series,
                "outcome": outcome(card_id, result),
                "read_code": result.code.raw if result.code else "",
                "answer": result.match.card_id if result.match else
                          " ".join(c.card_id for c in result.candidates),
                "seconds": round(seconds, 3),
                **centering_columns(clean_centering(card), measured),
            })
            if len(rows) % 25 == 0:
                log.info("Scanned %d/%d", len(rows), len(sample))

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(row["outcome"] for row in rows)
    print(f"\n{len(rows)} cards, {'clean images' if clean else 'synthetic photos'}, seed {seed}")
    for name, meaning in OUTCOMES.items():
        share = counts[name] / len(rows)
        print(f"  {name:15} {counts[name]:4}  {share:6.1%}  {meaning}")
    times = sorted(row["seconds"] for row in rows)
    print(f"  time per scan: median {statistics.median(times):.2f}s, "
          f"slowest {times[-1]:.2f}s")

    found = [row for row in rows if row["outcome"] in ("correct", "choice")]
    bordered = [row for row in found if row["clean_left_pct"] != ""]
    errors = sorted(row["centering_error"] for row in bordered if row["centering_error"] != "")
    print(f"\nFront centering, on the {len(bordered)} cards found that have a measurable "
          "border on the clean image:")
    if errors:
        print(f"  measured on the photo: {len(errors)} ({len(errors) / len(bordered):.1%})")
        print(f"  error, worse of left-right and top-bottom: median {statistics.median(errors)}, "
              f"90th percentile {errors[int(0.9 * (len(errors) - 1))]}, "
              f"over 5 points {sum(e > 5 for e in errors)}")
    unchecked = sum(row["photo_left_pct"] != "" and row["clean_left_pct"] == "" for row in found)
    print(f"  measured on the photo but not on the clean image (can't be checked): {unchecked}")
    print("\nBy series (correct / cards):")
    by_series: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_series[row["series"]].append(row["outcome"])
    for series, results in sorted(by_series.items(), key=lambda item: -len(item[1])):
        print(f"  {series:28} {results.count('correct'):4} / {len(results)}")
    print(f"\nDetails: {RESULTS}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cards", type=int, default=300, help="how many cards (default 300)")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    parser.add_argument("--clean", action="store_true",
                        help="scan the clean images, without making photos of them")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return run(args.cards, args.seed, args.clean)


if __name__ == "__main__":
    sys.exit(main())
