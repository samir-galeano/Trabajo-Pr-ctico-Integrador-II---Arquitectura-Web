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
