"""Tests of jobs/build_fingerprints.py that need neither the network nor the database."""

import numpy as np
import pytest

from app.recognition import artwork
from jobs.build_fingerprints import Card, _first_printing, glare, printed_total
from tests.test_artwork import LAYOUT, drawn_card


@pytest.mark.parametrize(("number", "official", "expected"), [
    ("14", 102, "102"),
    ("83", 82, "82"),  # a secret card past the set size: still '83/82'
    ("H01", 147, "H3"),  # Aquapolis's holo subset: 'H01/H32'
    ("50a", 147, "147"),
    ("FOUR", 102, None),  # Triumphant's Alph Lithograph: no number, no size
])
def test_the_printed_total_is_what_follows_the_slash(number, official, expected):
    numbers = ["1", "14", "50a", "H01", "H02", "H03"]

    assert printed_total(number, official, numbers) == expected


@pytest.mark.parametrize(("set_id", "number", "url"), [
    ("base1", "4", "https://images.pokemontcg.io/base1/4_hires.png"),
    ("lc", "4", "https://images.pokemontcg.io/base6/4_hires.png"),
    ("ecard2", "H01", "https://images.pokemontcg.io/ecard2/H1_hires.png"),
    ("hgss4", "FOUR", "https://images.pokemontcg.io/hgss4/FOUR_hires.png"),
])
def test_pictures_are_asked_by_the_source_s_set_ids_and_numbers(set_id, number, url):
    assert Card(f"{set_id}-{number}", set_id, number, None, LAYOUT).url == url


def first_edition(drawing: int, name: str) -> artwork.Artwork:
    art = artwork.Artwork("old1/14", LAYOUT, "https://example.test/old1-14.png",
                          artwork.fingerprint(drawn_card(drawing), LAYOUT))
    art.printings.append(artwork.Printing("en", "old1", "102", "14", "old1-14", name))
    return art


def reprint(name: str = "Raichu", layout: str = LAYOUT) -> Card:
    return Card("old3-20", "old3", "20", "130", layout, {"en": name})


def test_the_same_illustration_with_the_same_name_is_a_reprint():
    known = [first_edition(7, "Raichu")]
    # Scanned again for the reprint: a little blurred and lighter.
    picture = np.clip(drawn_card(7).astype(np.int16) + 12, 0, 255).astype(np.uint8)
    fingerprint = artwork.fingerprint(picture, LAYOUT)

    first, inliers = _first_printing(reprint(), fingerprint, known)

    assert first is known[0] and inliers >= 40


@pytest.mark.parametrize(("drawing", "name", "layout"), [
    (8, "Raichu", LAYOUT),  # another illustration of the same Pokémon
    (7, "Pikachu", LAYOUT),  # another card
    (7, "Raichu", "wizards-trainer"),  # another layout
])
def test_anything_else_keeps_an_illustration_of_its_own(drawing, name, layout):
    known = [first_edition(7, "Raichu")]
    fingerprint = artwork.fingerprint(drawn_card(drawing), layout)

    first, _ = _first_printing(reprint(name, layout), fingerprint, known)

    assert first is None


def test_glare_only_brightens():
    card = drawn_card(7)

    lit = glare(card, np.random.default_rng(1))

    assert lit.shape == card.shape and (lit >= card).all() and (lit > card).any()
