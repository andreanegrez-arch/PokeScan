"""Tests of the catalog picture download, offline."""

import cv2
import httpx
import numpy as np
import pytest

from app.recognition import detect, reference

URL = "https://assets.tcgdex.net/it/me/me02/072"


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    """Fake TCGdex: a card-shaped PNG for every URL, the URLs asked for listed."""
    monkeypatch.setattr(reference, "CACHE", tmp_path)
    asked = []
    ok, png = cv2.imencode(".png", np.full((825, 600, 3), 200, np.uint8))
    assert ok

    def get(url, **_):
        asked.append(url)
        return httpx.Response(200, content=png.tobytes(), request=httpx.Request("GET", url))

    monkeypatch.setattr(reference.httpx, "get", get)
    return asked


def test_the_largest_picture_is_downloaded_once_and_straightened(downloads, tmp_path):
    first = reference.picture(f"{URL}/low.webp")
    second = reference.picture(f"{URL}/low.webp")

    assert downloads == [f"{URL}/high.png"]
    assert (tmp_path / "it/me/me02/072.png").exists()
    for found in (first, second):
        assert found is not None
        assert (found.shape[1], found.shape[0]) == detect.CARD_SIZE


@pytest.mark.parametrize("url", [None, "", "https://assets.tcgdex.net/it/../../secret/low.webp"])
def test_no_picture_without_a_safe_url(downloads, url):
    assert reference.picture(url) is None
    assert downloads == []


def test_a_failed_download_gives_no_picture(monkeypatch, tmp_path):
    monkeypatch.setattr(reference, "CACHE", tmp_path)

    def get(url, **_):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(reference.httpx, "get", get)

    assert reference.picture(f"{URL}/low.webp") is None
    assert not any(tmp_path.iterdir())
