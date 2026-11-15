import json
from datetime import datetime, timezone

from app.main import process_event, records


def test_process_event_creates_audit_record(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    records.clear()

    event = {
        "event_id": "audit-test-001",
        "type": "bookstack.page.updated",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "request_id": "req-001",
        "payload": {
            "page_id": 25,
            "name": "Página de prueba",
            "triggered_by": "admin1",
        },
    }

    assert process_event(event) is True
    assert "audit-test-001" in records

    saved = json.loads((tmp_path / "audit.jsonl").read_text())
    assert saved["entity_id"] == "25"
    assert saved["actor"] == "admin1"


def test_duplicate_event_is_ignored(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    records.clear()

    event = {
        "event_id": "audit-test-002",
        "type": "bookstack.page.created",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "request_id": "req-002",
        "payload": {"page_id": 30},
    }

    assert process_event(event) is True
    assert process_event(event) is False

    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(lines) == 1


def test_event_without_id_is_rejected():
    from app.main import process_event

    try:
        process_event({"type": "bookstack.page.updated", "payload": {}})
        assert False, "Debía rechazar el evento sin event_id"
    except ValueError as exc:
        assert "event_id" in str(exc)
