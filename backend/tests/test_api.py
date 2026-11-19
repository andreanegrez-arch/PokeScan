from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PNG = ("card.png", b"\x89PNG fake image", "image/png")
TEXT = ("notes.txt", b"hello", "text/plain")


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_all_contract_endpoints_are_exposed():
    paths = app.openapi()["paths"]

    assert {
        "/scan",
        "/cards/search",
        "/cards/{card_id}",
        "/cards/{card_id}/prices",
    } <= paths.keys()


def test_scan_accepts_a_front_photo_without_back():
    response = client.post("/scan", files={"front": PNG})

    assert response.status_code != 422


def test_scan_rejects_files_that_are_not_images():
    assert client.post("/scan", files={"front": TEXT}).status_code == 422
    assert client.post("/scan", files={"front": PNG, "back": TEXT}).status_code == 422
