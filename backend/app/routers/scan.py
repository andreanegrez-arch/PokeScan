import json
from datetime import datetime
from typing import Annotated

import numpy as np
from fastapi import APIRouter, HTTPException, Query, UploadFile

from app.config import ENV_FILE, settings
from app.db import DbConnection
from app.recognition.detect import UnreadableImage, decode
from app.recognition.identify import identify
from app.schemas import Lang, ScanResult

router = APIRouter(tags=["scan"])

ACCEPTED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_PHOTO_BYTES = 20 * 1024 * 1024  # a phone photo is 2-8 MB


def _read(photo: UploadFile) -> bytes:
    data = photo.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail=f"{photo.filename}: larger than 20 MB")
    return data


def _image(photo: UploadFile, data: bytes) -> np.ndarray:
    try:
        return decode(data)
    except UnreadableImage:
        raise HTTPException(
            status_code=422, detail=f"{photo.filename}: not a readable image"
        ) from None


def _archive(photos: dict[str, tuple[UploadFile, bytes]], lang: Lang, result: ScanResult) -> None:
    """Keep the photos and the result in their own folder (see scan_archive_dir)."""
    root = settings.scan_archive_dir
    if root is None:
        return
    if not root.is_absolute():
        root = ENV_FILE.parent / root
    folder = root / datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    folder.mkdir(parents=True)
    for side, (photo, data) in photos.items():
        (folder / (side + ACCEPTED_IMAGE_TYPES[photo.content_type])).write_bytes(data)
    summary = {"lang": lang, "result": result.model_dump(mode="json")}
    (folder / "result.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                        encoding="utf-8")


# A plain `def`: recognition is CPU work, so FastAPI runs it in a worker thread
# instead of blocking the server.
@router.post(
    "/scan",
    response_model=ScanResult,
    responses={
        413: {"description": "Photo larger than 20 MB"},
        422: {"description": "Not a JPEG, PNG or WebP image"},
    },
)
def scan_card(
    conn: DbConnection,
    front: UploadFile,
    back: UploadFile | None = None,
    lang: Lang = "it",
    set_id: Annotated[str | None, Query(
        description="The card's edition, if the user knows it (e.g. base1): its "
                    "illustrations are compared whatever set size is read",
        max_length=40,
    )] = None,
) -> ScanResult:
    """Identify a card from its printed code.

    Identification never relies on the artwork alone, because the same
    artwork is often reprinted in several sets:
    1. find the card in the photo and straighten it (OpenCV);
    2. read the code at the bottom with OCR, e.g. 'SVI 025/198' or '136/189';
    3. look it up by number + official set size (or the promo number);
    4. several sets share that size: keep the card whose set code is printed
       next to the number, then the one whose name is printed at the top;
    5. where the edition has fingerprints of its illustrations, compare the
       card's: a clear match confirms the number read or corrects it;
    6. still undecided: status 'ambiguous'. No code read and no illustration
       recognized: 'unreadable'.

    When a card is found, or its code is read but no card in the catalog has
    it (status 'not_found'), `condition` holds the front's centering and, with
    the optional back photo, the back's centering, the whitening on its
    edges and an indicative grade (null without the back). For a card found
    whose border's inner edge doesn't show on every side of the photo, that
    edge is carried over from the card's catalog picture, aligned on its
    artwork and text; the cut is always measured on the photo.
    """
    photos = {"front": front, "back": back}
    read: dict[str, tuple[UploadFile, bytes]] = {}
    for side, photo in photos.items():
        if photo is None:
            continue
        if photo.content_type not in ACCEPTED_IMAGE_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"{photo.filename}: upload a JPEG, PNG or WebP image",
            )
        read[side] = (photo, _read(photo))
    images = {side: _image(photo, data) for side, (photo, data) in read.items()}

    result = identify(conn, images["front"], lang, images.get("back"), set_id=set_id)
    _archive(read, lang, result)
    return result
