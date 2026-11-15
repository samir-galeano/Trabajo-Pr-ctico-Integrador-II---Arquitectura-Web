from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.database import apply_event


def test_healthz():
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_search_highlights_results():
    fake_pages = [{
        "page_id": 2,
        "name": "Servidor Nginx",
        "content": "Nginx funciona como proxy inverso.",
        "url": "https://bookstack.local/books/documentacion-tecnica/page/servidor-nginx",
        "updated_at": None
    }]

    with patch("app.main.search_pages", return_value=fake_pages):
        with TestClient(app) as client:
            response = client.get("/search?q=Nginx")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert "<mark>Nginx</mark>" in data["results"][0]["highlight"]


def test_request_id():
    with TestClient(app) as client:
        response = client.get(
            "/healthz",
            headers={"X-Request-ID": "test-123"}
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-123"


def test_duplicate_event_is_idempotent():
    event_id = "a24e8f25-3b9b-4a1f-9c71-7f8f1d4c5a20"

    fake_page = {
        "id": 999999,
        "name": "Página de prueba",
        "content": "Contenido de prueba",
        "url": "https://bookstack.local/test",
        "updated_at": None
    }

    with patch("app.database.connect") as mock_connect:
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_cursor = mock_conn.cursor.return_value.__enter__.return_value

        mock_cursor.fetchone.side_effect = [
            (event_id,),
            None
        ]

        first = apply_event(event_id, 999999, page=fake_page)
        second = apply_event(event_id, 999999, page=fake_page)

    assert first is True
    assert second is False
    assert mock_cursor.execute.call_count == 3
