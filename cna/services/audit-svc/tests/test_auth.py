"""BearerAuth: con API_AUTH_TOKEN definido, la API exige "Authorization: Bearer <token>"."""
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from app import auth
from app.main import app

PROTECTED = "/audit"


def test_auth_disabled_without_token(monkeypatch):
    monkeypatch.setattr(auth, "TOKENS", [])
    client = TestClient(app)
    assert client.get("/no-existe").status_code == 404


def test_protected_route_requires_bearer(monkeypatch):
    monkeypatch.setattr(auth, "TOKENS", ["token-a", "token-b"])
    client = TestClient(app)

    response = client.get(PROTECTED)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": "Token Bearer ausente o inválido"}

    assert client.get(PROTECTED, headers={"Authorization": "Bearer otro"}).status_code == 401
    assert client.get(PROTECTED, headers={"Authorization": "Basic token-a"}).status_code == 401
    # Con token válido pasa la autenticación (404 = llegó al router)
    for token in ("token-a", "token-b"):
        assert client.get("/no-existe", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_probes_and_metrics_stay_public(monkeypatch):
    monkeypatch.setattr(auth, "TOKENS", ["token-a"])
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_rejections_are_measured(monkeypatch):
    monkeypatch.setattr(auth, "TOKENS", ["token-a"])
    TestClient(app).get(PROTECTED)
    assert '401' in generate_latest().decode()
