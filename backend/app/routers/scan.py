from fastapi import APIRouter, HTTPException, UploadFile

from app.schemas import Lang, ScanResult

router = APIRouter(tags=["scan"])

ACCEPTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


@router.post("/scan", response_model=ScanResult)
async def scan_card(
    front: UploadFile,
    back: UploadFile | None = None,
    lang: Lang = "it",
) -> ScanResult:
    """Identify a card from its printed code and estimate its condition.

    Identification never relies on the artwork alone, because the same
    artwork is often reprinted in several sets:
    1. find the card in the photo and straighten it (OpenCV);
    2. read the code at the bottom with OCR, e.g. 'SVI 025/198' or '136/189';
    3. look it up by set code + number, or by number + official set size;
    4. if several sets have that size, compare the printed set symbol
       with the symbols of those sets;
    5. still undecided: status 'ambiguous'. No code read: 'unreadable'.

    The optional back photo improves the condition estimate
    (back centering, whitening on the edges).
    """
    for photo in (front, back):
        if photo is not None and photo.content_type not in ACCEPTED_IMAGE_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"{photo.filename}: upload a JPEG, PNG or WebP image",
            )

    # TODO: implement identification and condition estimate
    raise HTTPException(status_code=501, detail="Not implemented yet")
