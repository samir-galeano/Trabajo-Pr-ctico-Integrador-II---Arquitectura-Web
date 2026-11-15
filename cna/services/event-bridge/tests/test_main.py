import pytest
from fastapi.testclient import TestClient

from app.main import app, build_event


def test_page_update_event():
    data = {
        "event": "page_update",
        "triggered_at": "2026-11-14T10:00:00Z",
        "related_item": {"id": 2, "name": "Servidor Nginx"}
    }

    event = build_event(data, "request-123")

    assert event["type"] == "bookstack.page.updated"
    assert event["payload"]["page_id"] == 2
    assert event["request_id"] == "request-123"


def test_page_delete_event():
    data = {
        "event": "page_delete",
        "related_item": {"id": 2}
    }

    event = build_event(data, "request-456")

    assert event["type"] == "bookstack.page.deleted"


def test_unknown_event_rejected():
    data = {
        "event": "unknown_event",
        "related_item": {"id": 2}
    }

    with pytest.raises(ValueError):
        build_event(data, "request-789")


def test_publish_carries_trace_context_and_request_id(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app import main

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(main, "tracer", provider.get_tracer("test"))

    js = AsyncMock()
    monkeypatch.setattr(main, "js", js)

    event = build_event({"event": "page_update", "related_item": {"id": 2}}, "req-trace")
    asyncio.run(main.publish_event(event))

    headers = js.publish.call_args.kwargs["headers"]
    span = exporter.get_finished_spans()[0]

    assert headers["X-Request-ID"] == "req-trace"
    # W3C traceparent: 00-<trace_id>-<span_id>-<flags>
    assert headers["traceparent"].split("-")[1] == trace.format_trace_id(span.context.trace_id)
    assert span.kind == trace.SpanKind.PRODUCER
    assert span.attributes["request_id"] == "req-trace"


def test_webhook_token_not_in_metrics_or_trace(monkeypatch):
    from unittest.mock import AsyncMock

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from prometheus_client import generate_latest

    from app import main

    secret = "token-super-secreto"
    monkeypatch.setattr(main, "WEBHOOK_TOKEN", secret)
    monkeypatch.setattr(main, "js", AsyncMock())

    # Span del servidor como lo crea la instrumentación de FastAPI
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("test").start_as_current_span("server") as span:
        span.set_attribute("http.target", f"/webhooks/bookstack/{secret}")
        span.set_attribute("http.url", f"http://x/webhooks/bookstack/{secret}")
        main.redact_webhook_token(span, {"path": f"/webhooks/bookstack/{secret}"})

    client = TestClient(main.app)
    response = client.post(
        f"/webhooks/bookstack/{secret}",
        json={"event": "page_update", "related_item": {"id": 2}},
    )

    assert response.status_code == 200
    assert secret not in generate_latest().decode()
    attributes = exporter.get_finished_spans()[0].attributes
    assert secret not in str(dict(attributes))
