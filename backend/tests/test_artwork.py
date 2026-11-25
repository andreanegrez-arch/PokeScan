"""Tests of recognition by illustration (app/recognition/artwork.py), offline.

Illustrations are drawn with OpenCV: shapes placed at random from a seed, so
each seed is a different drawing. Photos of the cards come from
jobs/evaluate_scan.py (tilt, perspective, light, blur, noise, JPEG).
"""

import sqlite3

import cv2
import numpy as np
import pytest

from app.recognition import artwork, detect
from app.recognition.identify import identify
from jobs.evaluate_scan import make_photo
from tests.catalog_data import seed

GREEN_TABLE = (60, 90, 60)
LAYOUT = "wizards-pokemon"


def drawn_card(drawing: int, code: str = "", name: str = "") -> np.ndarray:
    """A card with a drawing in the artwork box (different for each `drawing`)."""
    width, height = detect.CARD_SIZE
    card = np.full((height, width, 3), 245, np.uint8)
    cv2.rectangle(card, (0, 0), (width - 1, height - 1), (40, 190, 230), 30)
    rng = np.random.default_rng(drawing)
    left, top, right, bottom = 60, 150, width - 60, 560
    cv2.rectangle(card, (left, top), (right, bottom), (200, 220, 235), -1)
    for _ in range(40):
        color = tuple(int(c) for c in rng.integers(0, 200, 3))
        x, y = int(rng.integers(left, right)), int(rng.integers(top, bottom))
        if rng.random() < 0.5:
            cv2.circle(card, (x, y), int(rng.integers(8, 40)), color, int(rng.choice([-1, 3])))
        else:
            x2, y2 = int(rng.integers(left, right)), int(rng.integers(top, bottom))
            cv2.line(card, (x, y), (x2, y2), color, int(rng.integers(2, 6)))
    cv2.putText(card, name, (60, 110), cv2.FONT_HERSHEY_DUPLEX, 1.6, (20, 20, 20), 3, cv2.LINE_AA)
    cv2.putText(card, code, (60, height - 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2,
                cv2.LINE_AA)
    cv2.rectangle(card, (width - 110, height - 110), (width - 50, height - 50), (0, 0, 0), -1)
    return card


def photo(card: np.ndarray, seed: int = 1, turn: int = 0) -> np.ndarray:
    return detect.decode(make_photo(card, np.random.default_rng(seed), turn=turn,
                                    background=GREEN_TABLE))


def straightened(image: np.ndarray) -> np.ndarray:
    card = detect.find_card(image)
    assert card is not None
    return card


def drawn(drawing: int, key: str, layout: str = LAYOUT) -> artwork.Artwork:
    return artwork.Artwork(key, layout, f"https://example.test/{key}.png",
                           artwork.fingerprint(drawn_card(drawing), layout))


def printing(card_id: str, number: str, total: str = "102", lang: str = "en",
             name: str | None = None) -> artwork.Printing:
    return artwork.Printing(lang, card_id.split("-")[0], total, number, card_id,
                            name or f"Card {number}")


def save(folder, set_id: str, artworks: list[artwork.Artwork],
         printings: list[tuple[str, artwork.Printing]], total: str = "102") -> None:
    meta = {"set_id": set_id, "official_count": total, "source": "drawn"}
    artwork.write_edition(folder / f"{set_id}.sqlite", meta, artworks, printings)


def edition(folder, set_id: str, cards: list[tuple[int, str]], total: str = "102") -> None:
    """An edition that printed each drawing first, as (drawing, number)."""
    keys = [artwork.artwork_key(set_id, number) for _, number in cards]
    save(folder, set_id, [drawn(drawing, key) for (drawing, _), key in zip(cards, keys)],
         [(key, printing(f"{set_id}-{number}", number, total))
          for (_, number), key in zip(cards, keys)], total)


def everything(_: artwork.Printing) -> bool:
    return True


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_photo_matches_its_own_illustration_and_no_other(seed):
    picture = artwork.fingerprint(drawn_card(7), LAYOUT)
    other = artwork.fingerprint(drawn_card(8), LAYOUT)
    taken = artwork.fingerprint(straightened(photo(drawn_card(7, "14/102", "Raichu"), seed)),
                                LAYOUT)

    assert artwork.agreement(taken, picture) >= artwork.MIN_INLIERS
    assert artwork.agreement(taken, other) * artwork.MIN_MARGIN < artwork.agreement(taken, picture)


def test_the_printed_text_is_not_in_the_fingerprint():
    # The same illustration printed in Italian and in English: one fingerprint.
    english = artwork.fingerprint(drawn_card(7, "14/102", "Raichu"), LAYOUT)
    italian = artwork.fingerprint(drawn_card(7, "14/102", "Raichu PV 80"), LAYOUT)

    assert np.array_equal(english.descriptors, italian.descriptors)


def test_a_fingerprint_keeps_at_most_the_set_number_of_keypoints():
    fingerprint = artwork.fingerprint(drawn_card(7), LAYOUT)

    assert 0 < len(fingerprint.keypoints) == len(fingerprint.descriptors) <= artwork.FEATURES


@pytest.mark.parametrize(("a", "b", "same"), [
    ("025", "25", True), ("H01", "H1", True), ("102", "0102", True),
    ("14", "4", False), ("H32", "32", False), (None, "102", False),
])
def test_numbers_are_compared_without_zero_padding(a, b, same):
    assert artwork.same_number(a, b) is same


# ---------------------------------------------------------------------------
# Editions' files
# ---------------------------------------------------------------------------


def test_an_edition_file_keeps_fingerprints_and_printings(fingerprints):
    art = drawn(7, "old1/14")
    save(fingerprints, "old1", [art], [("old1/14", printing("old1-14", "14")),
                                       ("old1/14", printing("old1-14", "14", lang="it",
                                                            name="Raichu"))])

    (read,) = artwork.library()

    assert (read.key, read.layout, read.source_url) == ("old1/14", LAYOUT, art.source_url)
    assert [(p.lang, p.card_id, p.set_total, p.name) for p in read.printings] == [
        ("en", "old1-14", "102", "Card 14"), ("it", "old1-14", "102", "Raichu")]
    assert np.array_equal(read.fingerprint.descriptors, art.fingerprint.descriptors)
    assert np.allclose(read.fingerprint.keypoints, art.fingerprint.keypoints, atol=0.25)


def test_a_reprint_links_to_the_illustration_of_its_first_edition(fingerprints):
    edition(fingerprints, "old1", [(7, "14")])
    # old3 reprinted it as 20/130, with no illustration of its own.
    save(fingerprints, "old3", [], [("old1/14", printing("old3-20", "20", "130"))], "130")

    (read,) = artwork.library()

    assert [(p.card_id, p.number, p.set_total) for p in read.printings] == [
        ("old1-14", "14", "102"), ("old3-20", "20", "130")]


def test_a_printing_whose_illustration_is_in_no_file_is_left_out(fingerprints):
    save(fingerprints, "old3", [], [("old1/14", printing("old3-20", "20", "130"))], "130")

    assert artwork.library() == []


def test_a_new_file_is_read_without_restarting(fingerprints):
    edition(fingerprints, "old1", [(7, "14")])
    assert len(artwork.library()) == 1

    edition(fingerprints, "old2", [(11, "58")])

    assert [a.key for a in artwork.library()] == ["old1/14", "old2/58"]


def test_a_file_of_another_format_is_left_out(fingerprints):
    edition(fingerprints, "old1", [(7, "14")])
    with sqlite3.connect(fingerprints / "old1.sqlite") as db:
        db.execute("UPDATE meta SET value = '0' WHERE key = 'format'")
    db.close()

    assert artwork.library() == []


def test_a_match_must_stand_out_from_the_next_illustration(fingerprints):
    # The same drawing twice in the edition: no way to tell which card it is.
    edition(fingerprints, "old1", [(7, "14"), (7, "15")])
    card = straightened(photo(drawn_card(7)))

    assert artwork.best_match(card, artwork.library(), everything) is None


def test_fingerprints_are_read_from_their_file_only_when_compared(fingerprints):
    art = drawn(7, "old1/14")
    save(fingerprints, "old1", [art], [("old1/14", printing("old1-14", "14"))])

    (read,) = artwork.library()

    assert read.stored is None and read.file == fingerprints / "old1.sqlite"
    assert np.array_equal(read.fingerprint.descriptors, art.fingerprint.descriptors)


def vocabulary(folder, first: int = 0) -> np.ndarray:
    """Visual words made of the descriptors of 20 drawings, saved in `folder`."""
    descriptors = np.vstack([artwork.fingerprint(drawn_card(d), LAYOUT).descriptors
                             for d in range(first, first + 20)])
    words = descriptors[np.random.default_rng(0).choice(len(descriptors), 256, replace=False)]
    (folder / artwork.VOCABULARY).write_bytes(words.tobytes())
    return words


def indexed_edition(folder, drawings) -> None:
    words = vocabulary(folder)
    cards = [(d, str(d)) for d in drawings]
    keys = [artwork.artwork_key("old1", n) for _, n in cards]
    artwork.write_edition(
        folder / "old1.sqlite", {"set_id": "old1"},
        [drawn(d, key) for (d, _), key in zip(cards, keys)],
        [(key, printing(f"old1-{n}", n)) for (_, n), key in zip(cards, keys)], words)


def test_the_index_ranks_first_the_illustration_photographed(fingerprints):
    indexed_edition(fingerprints, range(30, 42))
    library = artwork.library()
    card = straightened(photo(drawn_card(35)))

    scores = library.shortlist.scores({LAYOUT: artwork.fingerprint(card, LAYOUT)})

    assert [a.key for a in library][int(np.argmax(scores))] == "old1/35"


def test_with_more_illustrations_than_the_shortlist_the_right_one_is_still_found(
        fingerprints, monkeypatch):
    monkeypatch.setattr(artwork, "SHORTLIST", 3)
    indexed_edition(fingerprints, range(30, 42))
    card = straightened(photo(drawn_card(38), seed=2))

    match = artwork.best_match(card, artwork.library(), everything)

    assert match is not None and match.artwork.key == "old1/38"


def test_words_of_another_vocabulary_are_not_used_to_leave_illustrations_out(
        fingerprints, monkeypatch):
    monkeypatch.setattr(artwork, "SHORTLIST", 1)
    indexed_edition(fingerprints, [7, 9])
    vocabulary(fingerprints, 50)  # the vocabulary changed since
    card = straightened(photo(drawn_card(9)))

    library = artwork.library()

    assert library.shortlist is None
    assert artwork.best_match(card, library, everything).artwork.key == "old1/9"


def test_only_the_printings_kept_are_matched(fingerprints):
    edition(fingerprints, "old1", [(7, "14")])
    card = straightened(photo(drawn_card(7)))

    def sized(total):
        return lambda p: artwork.same_number(p.set_total, total)

    assert artwork.best_match(card, artwork.library(), sized("64")) is None
    match = artwork.best_match(card, artwork.library(), sized("102"))
    assert [p.card_id for p in match.printings] == ["old1-14"]


# ---------------------------------------------------------------------------
# Whole scan, against the test catalog (old1 and old2 both have 102 cards)
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog(db):
    seed(db)
    db.execute("INSERT INTO cards (card_id, set_id, local_number, category)"
               " VALUES ('old1-14', 'old1', '14', 'Pokemon')")
    db.execute("INSERT INTO card_translations (card_id, lang, name) VALUES"
               " ('old1-14', 'en', 'Raichu')")
    db.commit()
    return db


@pytest.fixture
def old_sets(fingerprints):
    edition(fingerprints, "old1", [(7, "14"), (9, "58")])
    edition(fingerprints, "old2", [(11, "58")])


def test_the_illustration_corrects_a_misread_number(catalog, old_sets):
    # Raichu's 14/102 read as 4/102 (no card has it here; on Base Set, Charizard).
    result = identify(catalog, photo(drawn_card(7, "4/102", "Rachu")), "it")

    assert result.status == "matched"
    assert result.match.card_id == "old1-14"
    assert (result.code.raw, result.code.number, result.code.total) == ("4/102", "14", "102")


def test_the_illustration_picks_between_sets_of_the_same_size(catalog, old_sets):
    # 58/102 is Pikachu in both old sets: the drawing tells which.
    result = identify(catalog, photo(drawn_card(11, "58/102", "Pikachu")), "it")

    assert result.status == "matched"
    assert result.match.card_id == "old2-58"


def test_without_fingerprints_the_user_still_picks(catalog, old_sets):
    result = identify(catalog, photo(drawn_card(11, "58/102", "Pikachu")), "it",
                      illustrations=False)

    assert result.status == "ambiguous"


def test_an_illustration_of_no_edition_changes_nothing(catalog, old_sets):
    result = identify(catalog, photo(drawn_card(99, "58/102", "Pikachu")), "it")

    assert result.status == "ambiguous"


def test_without_any_code_the_illustration_alone_finds_the_card(catalog, old_sets):
    result = identify(catalog, photo(drawn_card(7)), "it")

    assert result.status == "matched"
    assert result.match.card_id == "old1-14"
    assert result.code is None


def test_the_edition_chosen_by_the_user_is_searched_whatever_size_is_read(catalog, old_sets):
    # 58/64: a misread size; the user said it's old2.
    result = identify(catalog, photo(drawn_card(11, "58/64", "Pikachu")), "it", set_id="old2")

    assert result.status == "matched"
    assert result.match.card_id == "old2-58"


def test_the_number_read_picks_the_printing_of_a_reprinted_illustration(catalog, fingerprints):
    # One drawing printed as old1's 14 and old2's 58, both /102: 58 was read.
    edition(fingerprints, "old1", [(7, "14"), (9, "58")])
    save(fingerprints, "old2", [], [("old1/14", printing("old2-58", "58"))])

    result = identify(catalog, photo(drawn_card(7, "58/102", "Pikachu")), "it")

    assert result.status == "matched"
    assert result.match.card_id == "old2-58"
