from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readyz():
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_search():
    response = client.get("/search?q=nginx")
    assert response.status_code == 200
    assert response.json()["query"] == "nginx"


def test_request_id():
    response = client.get(
        "/healthz",
        headers={"X-Request-ID": "test-123"}
    )
    assert response.headers["X-Request-ID"] == "test-123"
