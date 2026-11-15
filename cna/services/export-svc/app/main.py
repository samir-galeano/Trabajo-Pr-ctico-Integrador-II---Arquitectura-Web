import os
import json
import uuid
import time
import csv
import io
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import boto3
import httpx
from botocore.client import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST


SERVICE = "export-svc"

# MinIO
BUCKET = os.getenv("MINIO_BUCKET", "bookstack-exports")
ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
PUBLIC_ENDPOINT = os.getenv(
    "MINIO_PUBLIC_ENDPOINT",
    "http://192.168.0.6:9000"
)
ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
SECRET_KEY = os.getenv("MINIO_SECRET_KEY")

# BookStack API
BOOKSTACK_URL = os.getenv(
    "BOOKSTACK_BASE_URL",
    "https://bookstack.local"
).rstrip("/")
TOKEN_ID = os.getenv("BOOKSTACK_TOKEN_ID", "")
TOKEN_SECRET = os.getenv("BOOKSTACK_TOKEN_SECRET", "")
VERIFY_TLS = os.getenv(
    "BOOKSTACK_VERIFY_TLS",
    "false"
).lower() == "true"

PAGE_COUNT = 100

jobs = {}
jobs_lock = threading.Lock()

# Pool de trabajadores durante toda la vida del proceso.
# No se apaga al cerrar cada ciclo de pruebas HTTP.
executor = ThreadPoolExecutor(max_workers=2)

REQUESTS = Counter(
    "export_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"]
)

DURATION = Histogram(
    "export_http_request_duration_seconds",
    "HTTP request duration"
)


class ExportRequest(BaseModel):
    format: str = Field(default="json", pattern="^(json|csv)$")
    entity: str = Field(default="pages", pattern="^pages$")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_json(level, message, request_id="system", **extra):
    record = {
        "timestamp": now_iso(),
        "level": level,
        "service": SERVICE,
        "request_id": request_id,
        "message": message,
        **extra
    }
    print(json.dumps(record, ensure_ascii=False), flush=True)


def get_s3(endpoint=None):
    if not ACCESS_KEY or not SECRET_KEY:
        raise RuntimeError("MinIO credentials are not configured")

    return boto3.client(
        "s3",
        endpoint_url=endpoint or ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"}
        ),
        region_name="us-east-1"
    )


def bookstack_headers():
    return {
        "Authorization": f"Token {TOKEN_ID}:{TOKEN_SECRET}",
        "Accept": "application/json",
        "Host": "bookstack.local"
    }


def get_bookstack_client():
    if not TOKEN_ID or not TOKEN_SECRET:
        raise RuntimeError("BookStack API credentials are not configured")

    return httpx.Client(
        verify=VERIFY_TLS,
        timeout=30,
        follow_redirects=False
    )


def fetch_bookstack_pages():
    """Obtiene todas las páginas reales de BookStack usando la API paginada."""
    records = []
    page_number = 1

    with get_bookstack_client() as client:
        while True:
            response = client.get(
                f"{BOOKSTACK_URL}/api/pages",
                params={
                    "count": PAGE_COUNT,
                    "page": page_number
                },
                headers=bookstack_headers()
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f"BookStack API respondió HTTP {response.status_code}"
                )

            payload = response.json()
            pages = payload.get("data", [])

            if not pages:
                break

            for item in pages:
                page_id = item.get("id")

                if page_id is None:
                    continue

                detail = item

                # La lista puede no incluir el HTML completo.
                if not item.get("html"):
                    detail_response = client.get(
                        f"{BOOKSTACK_URL}/api/pages/{page_id}",
                        headers=bookstack_headers()
                    )

                    if detail_response.status_code != 200:
                        raise RuntimeError(
                            "No se pudo consultar el detalle "
                            f"de la página {page_id}: "
                            f"HTTP {detail_response.status_code}"
                        )

                    detail = detail_response.json()

                records.append({
                    "id": detail.get("id", page_id),
                    "name": detail.get("name", item.get("name", "")),
                    "slug": detail.get("slug", item.get("slug", "")),
                    "book_id": detail.get(
                        "book_id",
                        item.get("book_id")
                    ),
                    "chapter_id": detail.get(
                        "chapter_id",
                        item.get("chapter_id")
                    ),
                    "created_at": detail.get(
                        "created_at",
                        item.get("created_at")
                    ),
                    "updated_at": detail.get(
                        "updated_at",
                        item.get("updated_at")
                    ),
                    "url": detail.get("url", item.get("url", "")),
                    "html": detail.get("html", "")
                })

            total = payload.get("total")

            if len(pages) < PAGE_COUNT:
                break

            if total is not None and len(records) >= total:
                break

            page_number += 1

    return records


def generate_export(job):
    job_id = job["job_id"]
    request_id = job["request_id"]

    with jobs_lock:
        job["status"] = "running"
        job["updated_at"] = now_iso()

    log_json(
        "INFO",
        "Exportación iniciada",
        request_id,
        job_id=job_id
    )

    try:
        records = fetch_bookstack_pages()

        if job["format"] == "csv":
            buffer = io.StringIO()

            fieldnames = [
                "id",
                "name",
                "slug",
                "book_id",
                "chapter_id",
                "created_at",
                "updated_at",
                "url",
                "html"
            ]

            writer = csv.DictWriter(
                buffer,
                fieldnames=fieldnames,
                extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(records)

            content = buffer.getvalue().encode("utf-8-sig")
            content_type = "text/csv; charset=utf-8"

        else:
            content = json.dumps(
                records,
                ensure_ascii=False,
                indent=2
            ).encode("utf-8")
            content_type = "application/json"

        object_key = f"exports/{job_id}.{job['format']}"

        s3 = get_s3()
        s3.put_object(
            Bucket=BUCKET,
            Key=object_key,
            Body=content,
            ContentType=content_type
        )

        # La URL prefirmada utiliza la dirección accesible desde
        # el navegador/host, no el nombre interno del contenedor.
        public_s3 = get_s3(PUBLIC_ENDPOINT)

        download_url = public_s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": BUCKET,
                "Key": object_key
            },
            ExpiresIn=3600
        )

        with jobs_lock:
            job["status"] = "done"
            job["record_count"] = len(records)
            job["object_key"] = object_key
            job["download_url"] = download_url
            job["updated_at"] = now_iso()

        log_json(
            "INFO",
            "Exportación completada",
            request_id,
            job_id=job_id,
            record_count=len(records),
            object_key=object_key
        )

    except Exception as exc:
        with jobs_lock:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["updated_at"] = now_iso()

        log_json(
            "ERROR",
            "Exportación fallida",
            request_id,
            job_id=job_id,
            error=str(exc)
        )


app = FastAPI(
    title="Export Service",
    version="1.1.0"
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start = time.perf_counter()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration = time.perf_counter() - start

        REQUESTS.labels(
            request.method,
            request.url.path,
            str(status_code)
        ).inc()

        DURATION.observe(duration)

        log_json(
            "INFO",
            "HTTP request",
            request_id,
            method=request.method,
            path=request.url.path,
            status=status_code,
            duration=round(duration, 5)
        )


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "service": SERVICE
    }


@app.get("/readyz")
def readyz():
    try:
        get_s3().head_bucket(Bucket=BUCKET)

        if not TOKEN_ID or not TOKEN_SECRET:
            raise RuntimeError("BookStack credentials are missing")

        return {
            "status": "ready",
            "service": SERVICE,
            "bucket": BUCKET
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="A required dependency is unavailable"
        )


@app.get("/metrics")
def metrics():
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.post("/exports", status_code=202)
def create_export(data: ExportRequest, request: Request):
    job_id = str(uuid.uuid4())
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    job = {
        "job_id": job_id,
        "status": "queued",
        "format": data.format,
        "entity": data.entity,
        "request_id": request_id,
        "created_at": now_iso(),
        "updated_at": now_iso()
    }

    with jobs_lock:
        jobs[job_id] = job

    executor.submit(generate_export, job)

    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/exports/{job_id}"
    }


@app.get("/exports/{job_id}")
def get_export(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)

        if job is None:
            raise HTTPException(
                status_code=404,
                detail="Job not found"
            )

        return dict(job)


@app.get("/exports")
def list_exports():
    with jobs_lock:
        return {
            "count": len(jobs),
            "items": [dict(job) for job in jobs.values()]
        }
