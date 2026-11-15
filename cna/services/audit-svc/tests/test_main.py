from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.main import app, build_record, process_event


class FakeTable:
    """Imita la tabla audit_records: event_id es PRIMARY KEY."""

    def __init__(self):
        self.rows = {}

    def insert(self, record):
        if record["event_id"] in self.rows:
            return False
        self.rows[record["event_id"]] = record
        return True


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(database, "insert_record", fake.insert)
    return fake


def make_event(event_id="audit-test-001", **payload):
    return {
        "event_id": event_id,
        "type": "bookstack.page.updated",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "request_id": "req-001",
        "payload": {"page_id": 25, "name": "Página de prueba", "triggered_by": "admin1", **payload},
    }


def test_process_event_creates_audit_record(table):
    assert process_event(make_event()) is True

    saved = table.rows["audit-test-001"]
    assert saved["entity_type"] == "page"
    assert saved["entity_id"] == "25"
    assert saved["actor"] == "admin1"
    assert saved["request_id"] == "req-001"
    assert saved["occurred_at"].tzinfo is not None


def test_duplicate_event_is_ignored(table):
    event = make_event("audit-test-002")

    assert process_event(event) is True
    assert process_event(event) is False
    assert len(table.rows) == 1


def test_event_without_id_is_rejected(table):
    with pytest.raises(ValueError, match="event_id"):
        process_event({"type": "bookstack.page.updated", "payload": {}})


def test_actor_from_user_object_and_bad_timestamp():
    event = make_event(triggered_by={"id": 1, "name": "Admin"})
    event["occurred_at"] = "no-es-fecha"

    record = build_record(event)
    assert record["actor"] == "Admin"
    assert record["occurred_at"] is None


@pytest.fixture
def client():
    with patch.object(main, "consume_events", AsyncMock()):
        with TestClient(app) as test_client:
            yield test_client


def test_query_passes_filters_to_database(client):
    rows = [{"event_id": "e1", "entity_type": "page", "entity_id": "25"}]

    with patch.object(database, "query_records", return_value=rows) as query:
        response = client.get(
            "/audit",
            params={"entity": "page", "id": "25", "start": "2026-11-01T00:00:00Z", "limit": 10},
            headers={"X-Request-ID": "req-xyz"},
        )

    assert response.status_code == 200
    assert response.json() == {"count": 1, "items": rows}
    assert response.headers["X-Request-ID"] == "req-xyz"

    kwargs = query.call_args.kwargs
    assert kwargs["entity_type"] == "page"
    assert kwargs["entity_id"] == "25"
    assert kwargs["start"] == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert kwargs["end"] is None
    assert kwargs["limit"] == 10


def test_query_returns_503_when_database_down(client):
    with patch.object(database, "query_records", side_effect=ConnectionError("down")):
        assert client.get("/audit").status_code == 503


def test_get_single_record(client):
    with patch.object(database, "get_record", return_value={"event_id": "e1"}):
        assert client.get("/audit/e1").json() == {"event_id": "e1"}

    with patch.object(database, "get_record", return_value=None):
        assert client.get("/audit/nope").status_code == 404


def test_readyz_without_nats_returns_503(client):
    assert client.get("/readyz").status_code == 503
