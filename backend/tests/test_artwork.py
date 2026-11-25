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


def entry(artwork_id: int, drawing: int, card_id: str, number: str,
          total: int = 102) -> artwork.Artwork:
    set_id = card_id.split("-")[0]
    printings = [artwork.Printing("en", set_id, total, number, card_id, f"Card {number}")]
    return artwork.Artwork(artwork_id, LAYOUT, f"https://example.test/{card_id}.png",
                           artwork.fingerprint(drawn_card(drawing), LAYOUT), printings)


def save(folder, set_id: str, artworks: list[artwork.Artwork], total: int = 102) -> None:
    meta = {"set_id": set_id, "official_count": str(total), "source": "drawn"}
    artwork.write_pool(folder / f"{set_id}.sqlite", meta, artworks)


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

    assert english.phash == italian.phash
    assert np.array_equal(english.descriptors, italian.descriptors)


def test_the_perceptual_hash_is_64_bits():
    fingerprint = artwork.fingerprint(drawn_card(7), LAYOUT)

    assert len(fingerprint.phash) == 8
    assert artwork.hamming(fingerprint.phash, fingerprint.phash) == 0


def test_layouts_are_known_by_era_and_category():
    assert artwork.layout_for("base", "Pokemon") == "wizards-pokemon"
    assert artwork.layout_for("neo", "Trainer") == "wizards-trainer"
    assert artwork.layout_for("sv", "Pokemon") is None  # not measured yet


# ---------------------------------------------------------------------------
# Editions
# ---------------------------------------------------------------------------


def test_an_edition_file_keeps_fingerprints_and_printings(fingerprints):
    art = entry(1, 7, "old1-14", "14")
    art.printings.append(artwork.Printing("it", "old1", 102, "14", "old1-14", "Raichu"))
    save(fingerprints, "old1", [art])

    (pool,) = artwork.pools()

    assert (pool.set_id, pool.official_count, pool.totals) == ("old1", 102, {102})
    (read,) = pool.artworks
    assert read.layout == LAYOUT and read.source_url == art.source_url
    assert [(p.lang, p.card_id, p.name) for p in read.printings] == [
        ("en", "old1-14", "Card 14"), ("it", "old1-14", "Raichu")]
    assert read.fingerprint.phash == art.fingerprint.phash
    assert np.array_equal(read.fingerprint.descriptors, art.fingerprint.descriptors)
    assert np.allclose(read.fingerprint.keypoints, art.fingerprint.keypoints, atol=0.25)


def test_a_file_of_another_format_is_left_out(fingerprints):
    save(fingerprints, "old1", [entry(1, 7, "old1-14", "14")])
    with sqlite3.connect(fingerprints / "old1.sqlite") as db:
        db.execute("UPDATE meta SET value = '0' WHERE key = 'format'")
    db.close()

    assert artwork.pools() == []


def test_a_match_must_stand_out_from_the_next_illustration(fingerprints):
    # The same drawing twice in the edition: no way to tell which card it is.
    save(fingerprints, "old1", [entry(1, 7, "old1-14", "14"), entry(2, 7, "old1-15", "15")])
    card = straightened(photo(drawn_card(7)))

    assert artwork.best_match(card, artwork.pools(), 102) is None


def test_only_printings_of_the_size_read_are_matched(fingerprints):
    save(fingerprints, "old1", [entry(1, 7, "old1-14", "14")])
    card = straightened(photo(drawn_card(7)))

    assert artwork.best_match(card, artwork.pools(), 64) is None
    assert artwork.best_match(card, artwork.pools(), 102).printings[0].card_id == "old1-14"


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
    save(fingerprints, "old1", [entry(1, 7, "old1-14", "14"), entry(2, 9, "old1-58", "58")])
    save(fingerprints, "old2", [entry(1, 11, "old2-58", "58")])


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
