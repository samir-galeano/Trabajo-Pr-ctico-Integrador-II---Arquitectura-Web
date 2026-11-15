import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main


def create_png():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buffer, format="PNG")
    return buffer.getvalue()


def create_jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="blue").save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeS3:
    def __init__(self):
        self.uploads = []
        self.thumbnail = create_jpeg()

    def put_object(self, **kwargs):
        self.uploads.append(kwargs)

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(self.thumbnail)}

    def head_bucket(self, **kwargs):
        return {}


class FakeJetStream:
    def __init__(self):
        self.messages = []

    async def publish(self, subject, payload):
        self.messages.append((subject, payload))


class FakeNats:
    def __init__(self, js):
        self.js = js
        self.is_connected = True

    def jetstream(self):
        return self.js

    async def close(self):
        self.is_connected = False


@pytest.fixture
def media_client(monkeypatch):
    storage = FakeS3()
    jetstream = FakeJetStream()
    connection = FakeNats(jetstream)

    async def fake_connect(url):
        return connection

    monkeypatch.setattr(main.nats, "connect", fake_connect)
    monkeypatch.setattr(main, "s3", storage)

    with TestClient(main.app) as client:
        yield client, storage, jetstream


def test_healthz(media_client):
    client, _, _ = media_client

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "media-svc"
    }


def test_upload_image_and_publish_event(media_client):
    client, storage, jetstream = media_client

    response = client.post(
        "/media/upload",
        headers={"X-Request-ID": "test-media-upload-001"},
        files={
            "file": (
                "test.png",
                create_png(),
                "image/png"
            )
        }
    )

    assert response.status_code == 201

    data = response.json()
    assert data["status"] == "uploaded"
    assert data["event"] == "media.uploaded"

    assert len(storage.uploads) == 2
    assert len(jetstream.messages) == 1

    subject, payload = jetstream.messages[0]
    event = json.loads(payload)

    assert subject == "media.uploaded"
    assert event["type"] == "media.uploaded"
    assert event["request_id"] == "test-media-upload-001"
    assert event["payload"]["media_id"] == data["media_id"]


def test_download_thumbnail(media_client):
    client, storage, _ = media_client

    response = client.get("/media/test-media-id/thumbnail")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == storage.thumbnail


def test_reject_non_image_file(media_client):
    client, _, _ = media_client

    response = client.post(
        "/media/upload",
        files={
            "file": (
                "document.txt",
                b"archivo de texto",
                "text/plain"
            )
        }
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Solo se permiten imágenes"
