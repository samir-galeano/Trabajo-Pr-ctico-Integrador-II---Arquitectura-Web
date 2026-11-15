from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import apply_event


@pytest.fixture
def client():
    # Sin PostgreSQL ni NATS: se omite la inicialización del lifespan.
    with patch("app.main.initialize"), \
            patch("app.events.consume_events", AsyncMock()):
        with TestClient(app) as test_client:
            yield test_client


def test_healthz(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_search_highlights_results(client):
    fake_pages = [{
        "page_id": 2,
        "name": "Servidor Nginx",
        "content": "Nginx funciona como proxy inverso.",
        "url": "https://bookstack.local/books/documentacion-tecnica/page/servidor-nginx",
        "updated_at": None
    }]

    with patch("app.main.search_pages", return_value=fake_pages):
        response = client.get("/search?q=Nginx")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert "<mark>Nginx</mark>" in data["results"][0]["highlight"]


def test_request_id(client):
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


def test_consumer_span_continues_publisher_trace(monkeypatch):
    from unittest.mock import MagicMock

    from opentelemetry import propagate, trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app import events

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(events, "tracer", tracer)

    # Cabeceras como las publica event-bridge
    with tracer.start_as_current_span("publish") as parent:
        headers = {"X-Request-ID": "req-1"}
        propagate.inject(headers)

    message = MagicMock(headers=headers, subject="bookstack.content.events")
    with events.consumer_span(message):
        pass

    consumer = [s for s in exporter.get_finished_spans() if s.kind == trace.SpanKind.CONSUMER][0]
    assert consumer.context.trace_id == parent.get_span_context().trace_id
    assert consumer.parent.span_id == parent.get_span_context().span_id
    assert consumer.attributes["request_id"] == "req-1"
