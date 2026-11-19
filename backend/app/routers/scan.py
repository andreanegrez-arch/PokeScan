import numpy as np
from fastapi import APIRouter, HTTPException, UploadFile

from app.db import DbConnection
from app.recognition.detect import UnreadableImage, decode
from app.recognition.identify import identify
from app.schemas import Lang, ScanResult

router = APIRouter(tags=["scan"])

ACCEPTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PHOTO_BYTES = 20 * 1024 * 1024  # a phone photo is 2-8 MB


def _image(photo: UploadFile) -> np.ndarray:
    data = photo.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail=f"{photo.filename}: larger than 20 MB")
    try:
        return decode(data)
    except UnreadableImage:
        raise HTTPException(
            status_code=422, detail=f"{photo.filename}: not a readable image"
        ) from None


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
) -> ScanResult:
    """Identify a card from its printed code.

    Identification never relies on the artwork alone, because the same
    artwork is often reprinted in several sets:
    1. find the card in the photo and straighten it (OpenCV);
    2. read the code at the bottom with OCR, e.g. 'SVI 025/198' or '136/189';
    3. look it up by number + official set size (or the promo number);
    4. several sets share that size: keep the card whose set code is printed
       next to the number, then the one whose name is printed at the top;
    5. still undecided: status 'ambiguous'. No code read: 'unreadable'.

    When a card is found, `condition` holds the front's centering and, with
    the optional back photo, the back's centering, the whitening on its
    edges and an indicative grade (null without the back). Photograph the
    back on a dark, plain surface.
    """
    for photo in (front, back):
        if photo is not None and photo.content_type not in ACCEPTED_IMAGE_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"{photo.filename}: upload a JPEG, PNG or WebP image",
            )
    front_image = _image(front)
    back_image = _image(back) if back is not None else None
    return identify(conn, front_image, lang, back_image)
