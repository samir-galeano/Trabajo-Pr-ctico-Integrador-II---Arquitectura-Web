"""export-svc (API): recibe pedidos de exportación y los encola en Redis.

No ejecuta exportaciones: eso lo hace export-worker (app.worker), un
proceso separado que comparte la imagen. El gateway publica estas rutas
como /v2/exports.
"""
import uuid
import time
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from opentelemetry import propagate
from pydantic import BaseModel, Field, model_validator
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app import auth
from app import credentials, events
from app.common import (
    BUCKET,
    JOBS_INDEX,
    URL_EXPIRES,
    enqueue_job,
    get_job,
    get_redis,
    get_s3,
    log_json,
    now_iso,
    presigned_url,
    queue_depths,
)


REQUESTS = Counter(
    "export_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"]
)

DURATION = Histogram(
    "export_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"]
)

QUEUE_DEPTH = Gauge(
    "export_queue_depth",
    "Jobs en cada lista de Redis",
    ["queue"]
)

JOBS_CREATED = Counter(
    "export_jobs_created_total",
    "Jobs de exportación encolados",
    ["format"]
)


class ExportRequest(BaseModel):
    page_id: Optional[int] = Field(default=None, ge=1)
    book_id: Optional[int] = Field(default=None, ge=1)
    format: Literal["pdf", "markdown"] = "pdf"

    @model_validator(mode="after")
    def exactly_one_target(self):
        if (self.page_id is None) == (self.book_id is None):
            raise ValueError("Se debe indicar page_id o book_id (solo uno)")
        return self


app = FastAPI(
    title="Export Service",
    version="2.0.0"
)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    # El contrato define 400 para pedidos inválidos (FastAPI usa 422).
    return JSONResponse(
        status_code=400,
        content={"detail": jsonable_errors(exc)}
    )


def jsonable_errors(exc):
    return [
        {"loc": list(error.get("loc", [])), "msg": error.get("msg", "")}
        for error in exc.errors()
    ]


# Bearer (BearerAuth de los contratos). Se registra antes que el middleware
# de métricas para quedar por dentro: los 401 también se miden y se loguean.
app.middleware("http")(auth.middleware)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    status_code = 500

    # Ruta de la plantilla para no crear una serie por cada job_id.
    path = request.url.path

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration = time.perf_counter() - start
        route = request.scope.get("route")
        path = getattr(route, "path", path)

        REQUESTS.labels(
            request.method,
            path,
            str(status_code)
        ).inc()

        DURATION.labels(request.method, path).observe(duration)

        log_json(
            "INFO",
            "HTTP request",
            request_id,
            method=request.method,
            path=request.url.path,
            status=status_code,
            duration=round(duration, 5)
        )


def public_job(job):
    """Respuesta pública de un job; agrega la URL prefirmada si terminó."""
    result = {
        key: job.get(key)
        for key in (
            "job_id",
            "status",
            "entity_type",
            "entity_id",
            "format",
            "attempts",
            "worker",
            "created_at",
            "updated_at",
            "completed_at",
            "request_id",
        )
        if job.get(key) not in (None, "")
    }

    if job.get("error"):
        result["error"] = job["error"]

    if job.get("status") == "done" and job.get("object_key"):
        result["object_key"] = job["object_key"]
        result["download_url"] = presigned_url(job["object_key"])
        result["expires_in"] = URL_EXPIRES

    return result


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "service": "export-svc"
    }


@app.get("/readyz")
def readyz():
    try:
        get_redis().ping()
        get_s3().head_bucket(Bucket=BUCKET)

        if not credentials.has_token():
            raise RuntimeError("BookStack credentials are missing")

        return {
            "status": "ready",
            "service": "export-svc",
            "bucket": BUCKET
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="A required dependency is unavailable"
        )


@app.get("/metrics")
def metrics():
    try:
        for name, depth in queue_depths().items():
            QUEUE_DEPTH.labels(name).set(depth)
    except Exception:
        pass

    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.post("/exports", status_code=202)
def create_export(data: ExportRequest, request: Request):
    job_id = str(uuid.uuid4())
    request_id = request.state.request_id
    entity_type = "page" if data.page_id else "book"
    created = now_iso()

    # El worker continúa la traza de esta petición (W3C traceparent).
    carrier = {}
    propagate.inject(carrier)

    job = {
        "job_id": job_id,
        "status": "queued",
        "entity_type": entity_type,
        "entity_id": data.page_id or data.book_id,
        "format": data.format,
        "attempts": 0,
        "request_id": request_id,
        "traceparent": carrier.get("traceparent", ""),
        "created_at": created,
        "updated_at": created
    }

    try:
        enqueue_job(job)
    except Exception as exc:
        log_json("ERROR", "No se pudo encolar el job", request_id, error=str(exc))
        raise HTTPException(status_code=503, detail="Job queue unavailable")

    JOBS_CREATED.labels(data.format).inc()
    log_json("INFO", "Job encolado", request_id, job_id=job_id, entity_type=entity_type)
    events.publish("export.requested", job)

    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/v2/exports/{job_id}"
    }


@app.get("/exports/{job_id}")
def get_export(job_id: str):
    try:
        job = get_job(job_id)
    except Exception:
        raise HTTPException(status_code=503, detail="Job store unavailable")

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    return public_job(job)


@app.get("/exports")
def list_exports(limit: int = 50):
    limit = max(1, min(limit, 200))

    try:
        r = get_redis()
        total = r.zcard(JOBS_INDEX)
        job_ids = r.zrevrange(JOBS_INDEX, 0, limit - 1)
        items = [job for job in (get_job(job_id) for job_id in job_ids) if job]
    except Exception:
        raise HTTPException(status_code=503, detail="Job store unavailable")

    return {
        "count": total,
        "items": [public_job(job) for job in items]
    }
