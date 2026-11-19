from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


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
