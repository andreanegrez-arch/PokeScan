"""Tests of card recognition, offline.

Cards are drawn with OpenCV (a name at the top, a code at the bottom) and
turned into phone-like photos by jobs/evaluate_scan.py. The OCR models run
for real: the first test that uses them waits a few seconds for loading.
"""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.db import get_conn
from app.main import app
from app.recognition import detect
from app.recognition.identify import by_name, by_set_code, identify
from app.recognition.text import name_in_text, read_code
from app.schemas import CardSummary
from jobs.evaluate_scan import make_photo
from tests.catalog_data import seed

GREEN_TABLE = (60, 90, 60)


def drawn_card(code: str, name: str) -> np.ndarray:
    """A card-shaped image: name at the top, code at the bottom left, and a
    black square at the bottom right to tell which way up it ended."""
    width, height = detect.CARD_SIZE
    card = np.full((height, width, 3), 245, np.uint8)
    cv2.rectangle(card, (0, 0), (width - 1, height - 1), (40, 190, 230), 30)
    cv2.rectangle(card, (60, 150), (width - 60, 560), (150, 120, 90), -1)
    cv2.putText(card, name, (60, 110), cv2.FONT_HERSHEY_DUPLEX, 1.6, (20, 20, 20), 3,
                cv2.LINE_AA)
    cv2.putText(card, code, (60, height - 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20),
                2, cv2.LINE_AA)
    cv2.rectangle(card, (width - 110, height - 110), (width - 50, height - 50), (0, 0, 0), -1)
    return card


def photo(code: str, name: str, turn: int = 0, seed: int = 1) -> bytes:
    return make_photo(drawn_card(code, name), np.random.default_rng(seed), turn=turn,
                      background=GREEN_TABLE)


def summary(card_id: str, set_code: str | None) -> CardSummary:
    return CardSummary(card_id=card_id, name="", set_name="", set_code=set_code,
                       local_number="1", set_total=None, image_url=None, price_eur=None)


# ---------------------------------------------------------------------------
# Reading the code (lines as RapidOCR returned them for real cards)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lines", "number", "total"),
    [
        (["Illus. Tika Matsuno", "SVIEN", "025/198 ", "©2023 Pokémon / Nintendo"], "025", "198"),
        (["Illus. Mitsuhiro Arita ©1995,96,98,99 Nintendo, ©1999Wizards. 25/102"], "25", "102"),
        (["O2015 Pokémon", "Illus. Eske Yoshinob", "19/160*"], "19", "160"),
        (["TG05/TG30"], "TG05", "TG30"),
        (["SWSH020", "cheek sacs"], "SWSH020", None),
        (["O25/I98"], "025", "198"),  # digits read as letters
    ],
)
def test_the_code_is_found_among_the_lines(lines, number, total):
    code = read_code(lines).codes[0]

    assert (code.number, code.total) == (number, total)


def test_letters_stuck_to_the_number_are_also_tried_without():
    codes = read_code(["SVIEN025/198"]).codes

    assert [(c.number, c.total) for c in codes] == [("VIEN025", "198"), ("025", "198")]


def test_misread_slashes_and_symbols_are_also_tried():
    assert ("107", "236") in [(c.number, c.total) for c in read_code(["5107/236"]).codes]
    assert ("136", "189") in [(c.number, c.total) for c in read_code(["1366/89"]).codes]
    assert ("SV42", "SV94") in [(c.number, c.total) for c in read_code(["ASV42/SV94"]).codes]


def test_copyright_lines_are_not_codes():
    assert read_code(["©2023 Pokémon/Nintendo/Creatures/GAME FREAK"]).codes == []


def test_uppercase_words_are_kept_as_set_code_hints():
    assert read_code(["G", "SVIEN", "025/198", "GAME FREAK"]).set_hints == ["SVIEN"]


def test_names_are_found_despite_small_ocr_mistakes():
    assert name_in_text("Toedscool", "BASIC Toedscool HP60")
    assert name_in_text("Colonia di Annullamento", "Colonia di Annulamento")
    assert name_in_text("Pokémon Center", "POKEMON CENTER")
    assert not name_in_text("Furret", "BASIC Seviper Hp100")


# ---------------------------------------------------------------------------
# Choosing between cards with the same code
# ---------------------------------------------------------------------------


def test_the_set_code_read_next_to_the_number_picks_the_set():
    cards = [summary("swsh6-025", "CRE"), summary("sv01-025", "SVI"), summary("x-025", None)]

    assert [c.card_id for c in by_set_code(cards, ["SVIEN"])] == ["sv01-025"]
    assert by_set_code(cards, ["DIAS"]) is cards  # misread: no help


def test_the_name_picks_the_card_and_the_longest_name_wins():
    cards = [summary("a", None), summary("b", None), summary("c", None)]
    names = {"a": ["Gardevoir"], "b": ["Gardevoir ex"], "c": ["Zekrom"]}

    assert [c.card_id for c in by_name(cards, names, ["Gardevoir ex", "310 HP"])] == ["b"]
    assert by_name(cards, names, ["unreadable"]) is cards


def test_a_name_read_exactly_beats_a_similar_one():
    cards = [summary("sm10-134", None), summary("sm8-134", None)]
    names = {"sm10-134": ["Jigglypuff"], "sm8-134": ["Wigglytuff"]}

    assert [c.card_id for c in by_name(cards, names, ["BASIC", "Jigglypuff", "Hp70"])] == [
        "sm10-134"
    ]


# ---------------------------------------------------------------------------
# Finding the card in the photo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_the_card_is_found_and_straightened_never_mirrored(turn):
    image = detect.decode(photo("025/198", "Snom", turn=turn, seed=turn))

    card = detect.find_card(image)

    assert card is not None
    assert card.shape[:2] == (detect.CARD_SIZE[1], detect.CARD_SIZE[0])
    height, width = card.shape[:2]
    bottom_right = card[height - 100 : height - 60, width - 100 : width - 60].mean()
    top_left = card[60:100, 60:100].mean()
    # The black square ends at the bottom right (upright) or top left (upside down).
    assert min(bottom_right, top_left) < 60 < max(bottom_right, top_left)


def test_a_photo_without_a_card_has_no_outline():
    plain = np.full((1600, 1200, 3), GREEN_TABLE, np.uint8)

    assert detect.find_card(plain) is None


def test_not_an_image_is_refused():
    with pytest.raises(detect.UnreadableImage):
        detect.decode(b"\x89PNG not really")


# ---------------------------------------------------------------------------
# Whole scan, against the test catalog
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog(db):
    seed(db)
    return db


def test_a_code_with_its_set_code_is_matched(catalog):
    result = identify(catalog, photo("SVI EN 025/198", "Pikachu"), "it")

    assert result.status == "matched"
    assert result.match.card_id == "sv01-025"
    assert (result.code.set_code, result.code.number, result.code.total) == ("SVI", "025", "198")


def test_without_set_code_the_name_picks_between_sets_of_the_same_size(catalog):
    # sv01 and swsh6 both have 198 cards: number 025 is Pikachu in one, Snom in the other.
    result = identify(catalog, photo("025/198", "Snom"), "it")

    assert result.status == "matched"
    assert result.match.card_id == "swsh6-025"


def test_an_upside_down_card_is_read(catalog):
    result = identify(catalog, photo("136/189", "Furret", turn=180), "it")

    assert result.status == "matched"
    assert result.match.card_id == "swsh3-136"


def test_cards_with_the_same_code_and_name_are_left_to_the_user(catalog):
    result = identify(catalog, photo("58/102", "Pikachu"), "it")

    assert result.status == "ambiguous"
    assert [c.card_id for c in result.candidates] == ["old2-58", "old1-58"]


def test_a_code_no_card_has_is_not_found(catalog):
    result = identify(catalog, photo("999/999", "Nobody"), "it")

    assert result.status == "not_found"
    assert (result.code.number, result.code.total) == ("999", "999")


def test_a_photo_without_code_is_unreadable(catalog):
    result = identify(catalog, photo("", "Pikachu"), "it")

    assert result.status == "unreadable"
    assert result.code is None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def client(catalog):
    app.dependency_overrides[get_conn] = lambda: catalog
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_scan_endpoint_identifies_the_card_from_the_front_photo_alone(client):
    jpeg = photo("DAA 136/189", "Furret")

    response = client.post("/scan", files={"front": ("card.jpg", jpeg, "image/jpeg")})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "matched"
    assert body["match"]["card_id"] == "swsh3-136"
    assert body["condition"] is None


def test_scan_endpoint_refuses_files_that_are_not_images(client):
    jpeg = ("card.jpg", photo("136/189", "Furret"), "image/jpeg")
    text_file = ("notes.txt", b"hello", "text/plain")
    broken = ("card.png", b"\x89PNG not really", "image/png")

    assert client.post("/scan", files={"front": text_file}).status_code == 422
    assert client.post("/scan", files={"front": jpeg, "back": text_file}).status_code == 422
    response = client.post("/scan", files={"front": broken})
    assert response.status_code == 422
    assert response.json()["detail"] == "card.png: not a readable image"
