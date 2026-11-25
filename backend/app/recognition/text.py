"""Read text with OCR and pick out the collector code and the card name.

OCR (RapidOCR: PaddleOCR models on ONNX Runtime, CPU only) reads the numbers
of a card reliably, e.g. '136/189' or 'SWSH020'. The small set code printed
next to them ('SVI EN') is often misread ('SVIEN', 'DIAS'), so it is only used
as a hint to choose between cards sharing the same number.
"""

import re
import threading
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np

from app.schemas import CollectorCode

_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR  # loading the models takes a few seconds

        _engine = RapidOCR(
            params={
                "Global.log_level": "error",  # not "no text found" for each blank strip
                "Global.use_cls": False,  # text is upright: the card was straightened
                # Don't enlarge small regions (the default does, and is much slower).
                "Det.limit_type": "max",
                "Det.limit_side_len": 1000,
            }
        )
    return _engine


def warm_up() -> None:
    """Load the OCR models now, so that the first scan isn't slower."""
    with _engine_lock:
        _get_engine()


def read_lines(image: np.ndarray) -> list[str]:
    """Lines of text found in the image, as OCR reads them."""
    with _engine_lock:  # one image at a time: simple and safe
        result = _get_engine()(image)
    return [str(text) for text in (result.txts or ())]


# ---------------------------------------------------------------------------
# Collector code
# ---------------------------------------------------------------------------

# Number and set size, as printed: 025/198, 19/160, TG05/TG30.
# OCR may read the digits 0 and 1 as the letters O, I or L.
NUMBER_AND_TOTAL = re.compile(
    r"(?P<prefix>[A-Z]{0,4})(?P<digits>[0-9OIL]{1,4})"
    r"\s*/\s*"
    r"(?P<total_prefix>[A-Z]{0,4})(?P<total_digits>[0-9OIL]{1,4})"
)

# Promos have no set size: SWSH020, SM210, XY92, 'SVP EN 173' (language code
# included, often read as 'SVPEN 173').
PROMO = re.compile(
    r"(?<![A-Z0-9])(?P<prefix>SWSH|HGSS|SVP|SM|XY|BW|DP)"
    r"(?:\s?(?:EN|IT|DE|FR|ES|PT))?\s?(?P<digits>[0-9OIL]{2,3})(?![0-9])"
)

LOOKALIKE_DIGITS = str.maketrans("OIL", "011")

# Uppercase words printed near the code that are never a set code.
NOT_SET_CODES = {"GAME", "FREAK", "GAMEFREAK"}

# Digits read without their slash, as the small print of old cards often is:
# '1102' for 1/102 or 11/102, '702' for 7/102, 'B02' for 8/102. Not a year
# ('©1999'), a Pokédex number ('N. 107', '#107') or HP ('120 PV').
SLASHLESS = re.compile(r"(?<![0-9A-Z/.,:-])(?P<digits>[0-9OILB]{3,6})(?![0-9A-Z/])")
SLASHLESS_LOOKALIKES = str.maketrans("OILB", "0118")
NOT_A_CODE_BEFORE = re.compile(r"(?:\bN\.?|#|\bNO\.?)\s*$")
NOT_A_CODE_AFTER = re.compile(r"^\s*(?:PV|HP|PS|KP)\b")
YEAR = re.compile(r"0?(?:19|20)\d\d")  # '©1995' is often read 'O1995'
MAX_SET_SIZE = 400  # the largest official set size is ~300


@dataclass
class CodeReading:
    codes: list[CollectorCode] = field(default_factory=list)  # most likely first
    set_hints: list[str] = field(default_factory=list)  # uppercase words, e.g. 'SVIEN'
    # Codes with digits changed from what was read ('161/5' -> '1/5'): any
    # misreading can be "corrected" into a code some card has, so these need
    # the card's name or set code to agree.
    guesses: list[CollectorCode] = field(default_factory=list)


def _digits(text: str) -> str:
    return text.translate(LOOKALIKE_DIGITS)


def read_code(lines: list[str], slash_lost: bool = True) -> CodeReading:
    """Collector codes found in OCR lines, plus words that may be the set code.

    `slash_lost`: also guess codes from digits read without their slash (see
    SLASHLESS), after every code read with one: the copyright line above the
    code holds digits too ('2000' read '200': 2/100, Blastoise δ of another
    set, would beat Blastoise's 2/102 on the next line).
    """
    reading = CodeReading()
    slashless: list[tuple[str, str, str]] = []

    def add(raw: str, number: str, total: str | None, guess: bool = False) -> None:
        code = CollectorCode(raw=raw, set_code=None, number=number, total=total)
        if code not in reading.codes:
            reading.codes.append(code)
            if guess:
                reading.guesses.append(code)

    for line in lines:
        text = line.upper()
        for match in NUMBER_AND_TOTAL.finditer(text):
            if not (re.search(r"\d", match["digits"]) and re.search(r"\d", match["total_digits"])):
                continue  # e.g. 'NINTENDO/CREATURES'
            prefix, digits = _split_lookalikes(match["prefix"], match["digits"])
            total_prefix, total_digits = _split_lookalikes(
                match["total_prefix"], match["total_digits"]
            )
            total = total_prefix + total_digits
            raw = match.group(0)
            add(raw, prefix + digits, total)
            # Less likely readings, tried if the first finds no card:
            if prefix and not total_prefix:
                # letters stuck to the number ('SVIEN025/198') aren't part of it;
                add(raw, digits, total)
            if total_prefix and prefix != total_prefix and prefix.endswith(total_prefix):
                # a symbol read as a letter ('ASV42/SV94' is SV42/SV94);
                add(raw, total_prefix + digits, total)
            if not prefix and not total_prefix and len(digits) > len(total_digits):
                # a symbol read as a digit ('5107/236' is 107/236);
                add(raw, digits[-len(total_digits) :], total, guess=True)
                # the slash read as a digit and a 1 as the slash ('1366/89' is 136/189).
                add(raw, digits[:-1], "1" + total_digits, guess=True)
        for match in PROMO.finditer(text):
            if re.search(r"\d", match["digits"]):
                add(match.group(0), match["prefix"] + _digits(match["digits"]), None)
        if slash_lost:
            for match in SLASHLESS.finditer(text):
                digits = match["digits"]
                if (len(re.findall(r"\d", digits)) < 2
                        or YEAR.fullmatch(digits.translate(SLASHLESS_LOOKALIKES))
                        or NOT_A_CODE_BEFORE.search(text[: match.start()])
                        or NOT_A_CODE_AFTER.match(text[match.end() :])):
                    continue
                slashless += [(match.group(0), n, t) for n, t in slashless_codes(digits)]

        for word in line.split():
            letters = re.match(r"[A-Z]{2,}", word)
            if letters and word == word.upper() and letters.group(0) not in NOT_SET_CODES:
                reading.set_hints.append(letters.group(0))
    for raw, number, total in slashless:
        add(raw, number, total, guess=True)
    return reading


def slashless_codes(read: str) -> list[tuple[str, str]]:
    """(number, total) pairs that digits read without a slash may be.

    The slash was read as nothing ('1102': 1/102), as a digit ('127102':
    12/102), or with the 1 after it, as they look like one stroke ('702':
    7/102; '1102': 11/102).
    """
    digits = read.translate(SLASHLESS_LOOKALIKES)
    codes = []
    for cut in range(1, len(digits) - 1):
        number, rest = digits[:cut], digits[cut:]
        for total in (rest, rest[1:], "1" + rest):
            if (len(total) >= 2 and 10 <= int(total) <= MAX_SET_SIZE
                    and int(number) <= int(total) + 60 and (number, total) not in codes):
                codes.append((number, total))
    return codes


def _split_lookalikes(prefix: str, digits: str) -> tuple[str, str]:
    """('O', '25') -> ('', '025'): a prefix of O, I, L only is misread digits."""
    if prefix and set(prefix) <= set("OIL"):
        return "", _digits(prefix + digits)
    return prefix, _digits(digits)


# ---------------------------------------------------------------------------
# Card name
# ---------------------------------------------------------------------------

NAME_SIMILARITY = 0.8


def normalize(text: str) -> str:
    """Lowercase letters and digits only, without accents: 'Pokémon ex' -> 'pokemonex'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if c.isalnum() and not unicodedata.combining(c)).lower()


EXACT, CLOSE, ABSENT = 2, 1, 0


def find_name(name: str, text: str) -> int:
    """How `name` appears in `text`: EXACT, CLOSE (a few OCR mistakes) or ABSENT.

    Telling them apart matters: 'Wigglytuff' is CLOSE to 'Jigglypuff'.
    """
    name, text = normalize(name), normalize(text)
    if len(name) < 3 or not text:
        return ABSENT
    if name in text:
        return EXACT
    size = len(name)
    windows = [text[i : i + size] for i in range(max(1, len(text) - size + 1))]
    if any(SequenceMatcher(None, name, window).ratio() >= NAME_SIMILARITY for window in windows):
        return CLOSE
    return ABSENT


def name_in_text(name: str, text: str) -> bool:
    """Whether `name` appears in `text`, allowing a few OCR mistakes."""
    return find_name(name, text) != ABSENT
