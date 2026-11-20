"""The catalog's picture of a card, to measure its centering by (see
condition.borders_by_reference).

TCGdex's largest picture of the card (catalog.py keeps the base URLs),
downloaded on first use and kept in backend/.cache/references/. Any failure
(offline, no picture, not a card's shape): None, and the centering is
measured from the photo alone.
"""

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np

from app import catalog
from app.recognition import detect

log = logging.getLogger(__name__)

CACHE = Path(__file__).resolve().parents[2] / ".cache" / "references"
PICTURE = "/high.png"
TIMEOUT = 10.0
SAFE_PATH = re.compile(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*")


def picture(image_url: str | None) -> np.ndarray | None:
    """The card's picture, straightened like a photo of it (detect.CARD_SIZE).

    `image_url`: a card's image as the API gives it (base URL + catalog.SMALL_IMAGE).
    """
    if not image_url:
        return None
    base = image_url.removesuffix(catalog.SMALL_IMAGE).removesuffix(catalog.LARGE_IMAGE)
    path = urlparse(base).path.strip("/")
    if not SAFE_PATH.fullmatch(path) or ".." in path.split("/"):
        return None
    file = CACHE / f"{path}.png"
    try:
        if file.exists():
            data = file.read_bytes()
        else:
            response = httpx.get(base + PICTURE, timeout=TIMEOUT, follow_redirects=True)
            response.raise_for_status()
            data = response.content
            file.parent.mkdir(parents=True, exist_ok=True)
            partial = file.with_suffix(".part")
            partial.write_bytes(data)
            partial.replace(file)
        return detect.as_card(detect.decode(data))
    except (httpx.HTTPError, OSError, detect.UnreadableImage) as error:
        log.warning("No catalog picture from %s: %s", base, error)
        return None
