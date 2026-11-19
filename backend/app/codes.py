"""The collector code printed at the bottom of a card.

Used by the manual search now, and by the scan once OCR reads the code.
How codes are printed:

    SVI 025/198   set code, number, official set size (Scarlet & Violet onwards)
    136/189       number and official set size (older cards)
    TG05/TG30     subsets such as the Trainer Gallery
    SWSH020       promos: a prefix and a number, no set size
"""

import re

from app.schemas import CollectorCode

CODE = re.compile(
    r"""
    (?:(?P<set_code>(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,5})[\s-]+)?   # SVI, 30C
    (?P<number>(?P<prefix>[A-Z]{0,4})(?P<digits>\d{1,4})[A-Z]?) # 025, TG05, SWSH020
    (?:\s*/\s*(?P<total>[A-Z]{0,4}\d{1,4}))?                     # /198, /TG30
    """,
    re.VERBOSE,
)


def parse_code(text: str) -> CollectorCode | None:
    """Read a collector code; None if the text isn't one (e.g. a card name).

    A bare number like '136' is not a code: it exists in almost every set.
    """
    cleaned = " ".join(text.upper().split())
    match = CODE.fullmatch(cleaned)
    if not match:
        return None
    set_code, total, prefix = match["set_code"], match["total"], match["prefix"]
    if not (set_code or total or prefix):
        return None
    return CollectorCode(raw=text.strip(), set_code=set_code, number=match["number"], total=total)


def code_variants(code: CollectorCode) -> list[CollectorCode]:
    """The code as typed, plus a reading for a set code typed without a space.

    'SVI025' reads as a promo number; it may also mean set SVI, number 025.
    """
    variants = [code]
    match = CODE.fullmatch(code.number)
    if match and match["prefix"] and not code.set_code:
        variants.append(
            code.model_copy(update={"set_code": match["prefix"], "number": match["digits"]})
        )
    return variants


def total_count(code: CollectorCode) -> int | None:
    """Official set size as a number: '198' -> 198, 'TG30' -> 30."""
    if code.total is None:
        return None
    return int(re.sub(r"\D", "", code.total))
