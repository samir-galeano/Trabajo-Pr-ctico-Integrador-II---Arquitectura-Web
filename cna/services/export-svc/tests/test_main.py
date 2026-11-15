from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import common, worker
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def create_job(client, **body):
    response = client.post("/exports", json=body or {"page_id": 7, "format": "pdf"})
    assert response.status_code == 202
    return response.json()["job_id"]


def bookstack_response(status=200, content=b"%PDF-1.7 fake"):
    response = MagicMock(status_code=status, content=content)
    http = MagicMock()
    http.__enter__.return_value.get.return_value = response
    return http


# --- API ------------------------------------------------------------------

def test_create_export_enqueues_job(client, fake_redis):
    response = client.post(
        "/exports",
        json={"page_id": 7, "format": "markdown"},
        headers={"X-Request-ID": "req-123"}
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert fake_redis.lrange(common.QUEUE, 0, -1) == [data["job_id"]]

    job = client.get(f"/exports/{data['job_id']}").json()
    assert job["status"] == "queued"
    assert job["entity_type"] == "page"
    assert job["entity_id"] == 7
    assert job["format"] == "markdown"
    assert job["request_id"] == "req-123"


@pytest.mark.parametrize("body", [
    {"format": "pdf"},
    {"page_id": 1, "book_id": 2, "format": "pdf"},
    {"page_id": 1, "format": "json"},
    {"page_id": 0, "format": "pdf"},
])
def test_invalid_request_returns_400(client, body):
    assert client.post("/exports", json=body).status_code == 400


def test_get_nonexistent_job_returns_404(client):
    assert client.get("/exports/job-inexistente").status_code == 404


def test_queue_down_returns_503(client):
    common._redis = MagicMock(**{"pipeline.side_effect": ConnectionError("down")})
    response = client.post("/exports", json={"page_id": 1, "format": "pdf"})
    assert response.status_code == 503


def test_metrics_expose_queue_depth(client):
    create_job(client)
    body = client.get("/metrics").text
    assert 'export_queue_depth{queue="queued"} 1.0' in body


# --- Worker -----------------------------------------------------------------

def test_worker_completes_job(client, fake_redis):
    job_id = create_job(client, book_id=3, format="pdf")
    s3 = MagicMock()

    with patch.object(worker, "get_bookstack_client", return_value=bookstack_response()) as http, \
            patch.object(worker, "get_s3", return_value=s3), \
            patch("app.main.presigned_url", return_value="http://minio/signed"):
        assert worker.claim() == job_id
        worker.process(job_id)

        url = http.return_value.__enter__.return_value.get.call_args.args[0]
        assert url.endswith("/api/books/3/export/pdf")
        assert s3.put_object.call_args.kwargs["Key"] == f"exports/{job_id}.pdf"

        job = client.get(f"/exports/{job_id}").json()

    assert job["status"] == "done"
    assert job["download_url"] == "http://minio/signed"
    assert fake_redis.llen(common.PROCESSING) == 0
    assert not fake_redis.exists(f"export:lease:{job_id}")


def test_missing_page_fails_without_retry(client, fake_redis):
    job_id = create_job(client)

    with patch.object(worker, "get_bookstack_client", return_value=bookstack_response(404)):
        worker.claim()
        worker.process(job_id)

    job = common.get_job(job_id)
    assert job["status"] == "failed"
    assert "404" in job["error"]
    assert fake_redis.llen(common.QUEUE) == 0


def test_transient_error_requeues_job(client, fake_redis):
    job_id = create_job(client)

    with patch.object(worker, "get_bookstack_client", return_value=bookstack_response(502)):
        worker.claim()
        worker.process(job_id)

    assert common.get_job(job_id)["status"] == "queued"
    assert fake_redis.lrange(common.QUEUE, 0, -1) == [job_id]


def test_dead_worker_job_returns_to_queue(client, fake_redis):
    """X3: el worker muere con el job en running; otro worker lo recupera."""
    job_id = create_job(client)
    worker.claim()
    assert common.get_job(job_id)["status"] == "running"

    # Simula SIGKILL: nadie renueva el lease y vence.
    fake_redis.delete(f"export:lease:{job_id}")

    assert worker.reap_orphans() == [f"{job_id}:requeued"]
    assert common.get_job(job_id)["status"] == "queued"
    assert fake_redis.lrange(common.QUEUE, 0, -1) == [job_id]
    assert fake_redis.llen(common.PROCESSING) == 0


def test_reaper_ignores_jobs_with_live_lease(client, fake_redis):
    job_id = create_job(client)
    worker.claim()

    assert worker.reap_orphans() == []
    assert common.get_job(job_id)["status"] == "running"


def test_dead_worker_after_max_attempts_marks_failed(client, fake_redis):
    job_id = create_job(client)

    for _ in range(common.MAX_ATTEMPTS):
        worker.claim()
        fake_redis.delete(f"export:lease:{job_id}")
        worker.reap_orphans()

    job = common.get_job(job_id)
    assert job["status"] == "failed"
    assert job["attempts"] == common.MAX_ATTEMPTS
    assert fake_redis.llen(common.QUEUE) == 0


# --- Eventos -----------------------------------------------------------------

@pytest.fixture
def published(monkeypatch):
    from app import events
    calls = []
    monkeypatch.setattr(events, "publish", lambda event_type, job, **extra: calls.append((event_type, job["job_id"], extra)))
    return calls


def test_events_requested_and_completed(client, published):
    job_id = create_job(client, page_id=5, format="markdown")
    assert published == [("export.requested", job_id, {})]

    with patch.object(worker, "get_bookstack_client", return_value=bookstack_response(content=b"# Hola")), \
            patch.object(worker, "get_s3", return_value=MagicMock()):
        worker.claim()
        worker.process(job_id)

    event_type, event_job, extra = published[-1]
    assert (event_type, event_job) == ("export.completed", job_id)
    assert extra["object_key"] == f"exports/{job_id}.md"
    assert extra["size_bytes"] == 6


def test_event_failed_only_when_no_more_retries(client, published):
    job_id = create_job(client)

    with patch.object(worker, "get_bookstack_client", return_value=bookstack_response(502)):
        worker.claim()
        worker.process(job_id)
    assert [e[0] for e in published] == ["export.requested"]   # reintento, sin evento

    with patch.object(worker, "get_bookstack_client", return_value=bookstack_response(404)):
        worker.claim()
        worker.process(job_id)
    assert published[-1][0] == "export.failed"
    assert "404" in published[-1][2]["error"]


def test_event_envelope_follows_asyncapi():
    from app import events

    job = {"job_id": "j1", "entity_type": "page", "entity_id": "7", "format": "pdf",
           "attempts": "1", "request_id": "req-9"}
    event = events.build_event("export.completed", job, object_key="exports/j1.pdf")

    assert set(event) == {"event_id", "type", "version", "occurred_at", "request_id", "payload"}
    assert event["type"] == "export.completed"
    assert event["request_id"] == "req-9"
    assert event["payload"]["entity_id"] == 7
    assert event["payload"]["object_key"] == "exports/j1.pdf"


def test_publish_is_disabled_without_nats_url(monkeypatch):
    from app import events
    monkeypatch.setattr(events, "NATS_URL", "")
    assert events.publish("export.requested", {"job_id": "x"}) is False
