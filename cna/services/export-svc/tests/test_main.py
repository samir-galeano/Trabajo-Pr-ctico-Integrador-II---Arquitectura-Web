
from fastapi.testclient import TestClient
from app.main import app, jobs, jobs_lock, executor
from unittest.mock import patch


def clear_jobs():
    with jobs_lock:
        jobs.clear()


def test_create_export_returns_queued():
    clear_jobs()

    with patch.object(executor, "submit") as mock_submit:
        with TestClient(app) as client:
            response = client.post(
                "/exports",
                json={"format": "json", "entity": "pages"}
            )

        assert response.status_code == 202
        data = response.json()
        assert data["job_id"]
        assert data["status"] == "queued"
        assert mock_submit.called


def test_get_existing_job():
    clear_jobs()

    with patch.object(executor, "submit"):
        with TestClient(app) as client:
            response = client.post(
                "/exports",
                json={"format": "csv", "entity": "pages"}
            )

            job_id = response.json()["job_id"]
            result = client.get(f"/exports/{job_id}")

            assert result.status_code == 200
            assert result.json()["job_id"] == job_id
            assert result.json()["format"] == "csv"
            assert result.json()["status"] == "queued"


def test_get_nonexistent_job_returns_404():
    clear_jobs()

    with TestClient(app) as client:
        response = client.get("/exports/job-inexistente")

        assert response.status_code == 404
