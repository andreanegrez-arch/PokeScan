"""Tests of jobs/build_fingerprints.py that need neither the network nor the database."""

import datetime as dt

import numpy as np
import pytest

from app.recognition import artwork
from jobs.build_fingerprints import (
    REPRINT_INLIERS, Card, _classic, _first_printing, attach_pictures, era_of, glare,
    layout_for, printed_total, source_set, train_vocabulary,
)
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


@pytest.mark.parametrize(("set_id", "source"), [
    ("base1", "base1"), ("lc", "base6"), ("sv03.5", "sv3pt5"), ("sv10", "sv10"),
    ("me02.5", "me2pt5"), ("swsh12.5gg", "swsh12pt5gg"), ("2011bw", "mcd11"), ("exu", "ex10"),
])
def test_editions_are_asked_by_the_source_s_set_ids(set_id, source):
    assert source_set(set_id) == source


def listed(number: str, name: str = "Pikachu") -> dict:
    return {"number": number, "name": name,
            "images": {"large": f"https://images.example.test/{number}_{name}.png"}}


def test_pictures_are_found_by_number_whatever_the_zero_padding():
    cards = [Card(f"x-{n}", "x", n, None, LAYOUT) for n in ("001", "H01", "FOUR", "%3F", "99")]

    attach_pictures(cards, [listed("1"), listed("H1"), listed("FOUR"), listed("?")])

    assert [bool(c.url) for c in cards] == [True, True, True, True, False]


def test_old_cards_reprinted_in_their_old_frames_are_found_by_name():
    names = ["Charizard", "Palkia", "Darkrai & Cresselia LEGEND", "Darkrai & Cresselia LEGEND"]
    cards = [Card(f"30th-c-00{i}", "30th-c", f"00{i}", None, LAYOUT, {"en": name})
             for i, name in enumerate(names, 1)]

    attach_pictures(cards, [listed("100", "Darkrai & Cresselia LEGEND"),
                            listed("4", "Charizard"), listed("106", "Palkia LV.X"),
                            listed("99", "Darkrai & Cresselia LEGEND")])

    assert [c.url.rsplit("/", 1)[1].split("_")[0] for c in cards] == ["4", "106", "99", "100"]


@pytest.mark.parametrize(("set_id", "series", "released", "category", "rarity", "name", "layout"), [
    ("base1", "base", dt.date(1999, 1, 9), "Pokemon", None, "Raichu", "wizards-pokemon"),
    ("hgss4", "hgss", dt.date(2010, 11, 3), "Pokemon", "LEGEND", "Darkrai", "hgss-legend"),
    ("hgss4", "hgss", dt.date(2010, 11, 3), "Pokemon", "Rare PRIME", "Gengar", "hgss-pokemon"),
    ("ex1", "ex", dt.date(2003, 7, 1), "Trainer", None, "Energy Search", "ex-trainer"),
    ("pl1", "pl", dt.date(2009, 2, 11), "Energy", None, "Call Energy", "dp-energy"),
    ("xy1", "xy", dt.date(2013, 10, 12), "Pokemon", None, "Chespin", "bw-pokemon"),
    ("sv01", "sv", dt.date(2023, 3, 31), "Pokemon", "LEGEND", "Pineco", "modern-pokemon"),
    ("pop6", "pop", dt.date(2007, 11, 1), "Pokemon", None, "Lucario", "dp-pokemon"),
    ("2017sm", "mc", dt.date(2017, 6, 1), "Pokemon", None, "Rowlet", "modern-pokemon"),
    ("xy12", "xy", dt.date(2016, 11, 2), "Pokemon", None, "Charizard", "evolutions-pokemon"),
    ("xy12", "xy", dt.date(2016, 11, 2), "Pokemon", None, "Charizard EX", "bw-pokemon"),
    ("xy12", "xy", dt.date(2016, 11, 2), "Pokemon", None, "M Charizard EX", "bw-pokemon"),
])
def test_layouts_are_known_by_era_and_kind(set_id, series, released, category, rarity, name,
                                           layout):
    assert layout_for(era_of(set_id, series, released), category, rarity, name) == layout


def test_a_card_of_no_known_kind_has_no_layout():
    assert layout_for("wizards", None) is None


def first_edition(drawing: int, name: str, layout: str = LAYOUT) -> dict:
    """What a build knows: one illustration, by its English name."""
    art = artwork.Artwork("old1/14", layout, "https://example.test/old1-14.png",
                          artwork.fingerprint(drawn_card(drawing), layout))
    art.printings.append(artwork.Printing("en", "old1", "102", "14", "old1-14", name))
    return {name.casefold(): [art]}


def reprint(name: str = "Raichu", layout: str = LAYOUT) -> Card:
    return Card("old3-20", "old3", "20", "130", layout, {"en": name})


def test_the_same_illustration_with_the_same_name_is_a_reprint():
    known = first_edition(7, "Raichu")
    # Scanned again for the reprint: a little blurred and lighter.
    picture = np.clip(drawn_card(7).astype(np.int16) + 12, 0, 255).astype(np.uint8)
    fingerprint = artwork.fingerprint(picture, LAYOUT)

    first, inliers = _first_printing(reprint(), fingerprint, known)

    assert first is known["raichu"][0] and inliers >= REPRINT_INLIERS


@pytest.mark.parametrize(("drawing", "name", "layout"), [
    (8, "Raichu", LAYOUT),  # another illustration of the same Pokémon
    (7, "Pikachu", LAYOUT),  # another card
    (7, "Raichu", "wizards-trainer"),  # another layout
])
def test_anything_else_keeps_an_illustration_of_its_own(drawing, name, layout):
    known = first_edition(7, "Raichu")
    fingerprint = artwork.fingerprint(drawn_card(drawing), layout)

    first, _ = _first_printing(reprint(name, layout), fingerprint, known)

    assert first is None


def test_glare_only_brightens():
    card = drawn_card(7)

    lit = glare(card, np.random.default_rng(1))

    assert lit.shape == card.shape and (lit >= card).all() and (lit > card).any()


def test_a_classic_reprint_takes_the_layout_of_its_original():
    known = first_edition(7, "Raichu", "ecard-pokemon")
    card = Card("cel25cc-CC001", "cel25cc", "CC001", None, "modern-pokemon", {"en": "Raichu"})

    layout, fingerprint, first, inliers = _classic(card, drawn_card(7), known)

    assert layout == "ecard-pokemon" and first is known["raichu"][0]
    assert inliers >= REPRINT_INLIERS and len(fingerprint.keypoints) > 0


def test_a_classic_card_without_its_original_keeps_its_era_s_layout():
    card = Card("cel25cc-CC001", "cel25cc", "CC001", None, "modern-pokemon", {"en": "Raichu"})

    assert _classic(card, drawn_card(7), first_edition(8, "Raichu"))[0] is None


def test_the_vocabulary_has_its_size_and_is_made_of_descriptors():
    descriptors = np.random.default_rng(0).integers(0, 256, (2000, 32), dtype=np.uint8)

    words = train_vocabulary(descriptors, 64, np.random.default_rng(1), sample=1000)

    assert words.shape == (64, 32) and words.dtype == np.uint8
