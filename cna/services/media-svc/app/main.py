import io
import os
import json
import uuid
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import boto3
import nats
from botocore.config import Config
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

SERVICE = "media-svc"
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "bookstack-media")
ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
NATS_URL = os.getenv("NATS_URL", "nats://nats:4222")
MAX_SIZE = 10 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(SERVICE)

REQUESTS = Counter("media_http_requests_total", "HTTP requests", ["method", "path", "status"])
DURATION = Histogram("media_http_request_duration_seconds", "HTTP request duration")
UPLOADS = Counter("media_uploads_total", "Successful media uploads")


def log_json(level, message, request_id, **extra):
    logger.log(
        level,
        json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": logging.getLevelName(level),
            "service": SERVICE,
            "request_id": request_id,
            "message": message,
            **extra
        })
    )


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="us-east-1",
        config=Config(signature_version="s3v4")
    )


s3 = s3_client()
nc = None
js = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global nc, js
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    yield
    if nc and nc.is_connected:
        await nc.close()


app = FastAPI(title="Media Service", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = __import__("time").perf_counter()
    response = await call_next(request)
    duration = __import__("time").perf_counter() - start
    response.headers["X-Request-ID"] = request_id
    REQUESTS.labels(request.method, request.url.path, str(response.status_code)).inc()
    DURATION.observe(duration)
    log_json(
        logging.INFO,
        "HTTP request",
        request_id,
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration=round(duration, 5)
    )
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": SERVICE}


@app.get("/readyz")
async def readyz():
    try:
        s3.head_bucket(Bucket=MINIO_BUCKET)
        if not nc or not nc.is_connected:
            raise RuntimeError("NATS no conectado")
        return {"status": "ready", "service": SERVICE, "bucket": MINIO_BUCKET}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Dependencia no disponible: {exc}")


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/media/upload", status_code=201)
async def upload_media(request: Request, file: UploadFile = File(...)):
    request_id = request.state.request_id

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Solo se permiten imágenes")

    content = await file.read(MAX_SIZE + 1)
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="La imagen supera 10 MB")

    try:
        image = Image.open(io.BytesIO(content))
        image.verify()
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="Archivo de imagen inválido")

    media_id = str(uuid.uuid4())
    extension = (image.format or "JPEG").lower()
    content_type = file.content_type
    original_key = f"originals/{media_id}.{extension}"
    thumbnail_key = f"thumbnails/{media_id}.jpg"

    thumb = image.copy()
    thumb.thumbnail((300, 300))
    if thumb.mode not in ("RGB", "L"):
        thumb = thumb.convert("RGB")

    thumbnail_buffer = io.BytesIO()
    thumb.save(thumbnail_buffer, format="JPEG", quality=85)
    thumbnail_buffer.seek(0)

    try:
        s3.put_object(
            Bucket=MINIO_BUCKET,
            Key=original_key,
            Body=content,
            ContentType=content_type
        )
        s3.put_object(
            Bucket=MINIO_BUCKET,
            Key=thumbnail_key,
            Body=thumbnail_buffer.getvalue(),
            ContentType="image/jpeg"
        )

        event = {
            "event_id": str(uuid.uuid4()),
            "type": "media.uploaded",
            "version": "1.0",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "payload": {
                "media_id": media_id,
                "filename": file.filename,
                "content_type": content_type,
                "original_key": original_key,
                "thumbnail_key": thumbnail_key
            }
        }
        await js.publish("media.uploaded", json.dumps(event).encode())

        UPLOADS.inc()
        log_json(logging.INFO, "Imagen cargada y miniatura generada",
                 request_id, media_id=media_id, original_key=original_key,
                 thumbnail_key=thumbnail_key)

        return {
            "media_id": media_id,
            "status": "uploaded",
            "original_key": original_key,
            "thumbnail_key": thumbnail_key,
            "thumbnail_url": f"/media/{media_id}/thumbnail",
            "event": "media.uploaded"
        }
    except Exception as exc:
        log_json(logging.ERROR, "Error al almacenar imagen",
                 request_id, error=str(exc))
        raise HTTPException(status_code=502, detail="Error al almacenar o publicar el evento")


@app.get("/media/{media_id}/thumbnail")
def get_thumbnail(media_id: str):
    key = f"thumbnails/{media_id}.jpg"
    try:
        result = s3.get_object(Bucket=MINIO_BUCKET, Key=key)
        data = result["Body"].read()
        return Response(content=data, media_type="image/jpeg")
    except Exception:
        raise HTTPException(status_code=404, detail="Miniatura no encontrada")
