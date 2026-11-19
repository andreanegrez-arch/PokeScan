import pytest

from app.codes import code_variants, parse_code, total_count


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SVI 025/198", ("SVI", "025", "198")),
        ("  svi   025 / 198 ", ("SVI", "025", "198")),
        ("SVI-25", ("SVI", "25", None)),
        ("DAA 136", ("DAA", "136", None)),
        ("136/189", (None, "136", "189")),
        ("TG05/TG30", (None, "TG05", "TG30")),
        ("SWSH020", (None, "SWSH020", None)),
        ("30C 001/128", ("30C", "001", "128")),
    ],
)
def test_codes_are_read(text, expected):
    code = parse_code(text)

    assert code is not None
    assert (code.set_code, code.number, code.total) == expected


@pytest.mark.parametrize("text", ["Pikachu", "Porygon2", "Mr. Mime", "136", "SVI", ""])
def test_names_and_bare_numbers_are_not_codes(text):
    assert parse_code(text) is None


def test_a_set_code_typed_without_space_is_also_tried():
    readings = [(c.set_code, c.number) for c in code_variants(parse_code("SVI025"))]

    assert readings == [(None, "SVI025"), ("SVI", "025")]


def test_set_size_is_read_as_a_number():
    assert total_count(parse_code("025/198")) == 198
    assert total_count(parse_code("TG05/TG30")) == 30
    assert total_count(parse_code("SWSH020")) is None
