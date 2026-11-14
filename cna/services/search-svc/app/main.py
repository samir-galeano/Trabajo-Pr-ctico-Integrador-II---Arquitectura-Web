import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

SERVICE_NAME = "search-svc"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(SERVICE_NAME)

REQUESTS = Counter(
    "http_requests_total",
    "Total de solicitudes HTTP",
    ["service", "method", "path", "status"]
)

LATENCY = Histogram(
    "http_request_duration_seconds",
    "Duración de solicitudes HTTP",
    ["service", "method", "path"]
)

app = FastAPI(title="Search Service", version="1.0.0")


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start = time.time()
    status = 500

    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        elapsed = time.time() - start
        REQUESTS.labels(SERVICE_NAME, request.method, request.url.path, str(status)).inc()
        LATENCY.labels(SERVICE_NAME, request.method, request.url.path).observe(elapsed)
        logger.info(
            __import__("json").dumps({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "level": "INFO",
                "service": SERVICE_NAME,
                "request_id": request_id,
                "message": f"{request.method} {request.url.path} {status} {elapsed:.4f}s"
            })
        )


@app.get("/healthz")
async def health():
    return {"status": "alive", "service": SERVICE_NAME}


@app.get("/readyz")
async def ready():
    return {"status": "ready", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/search")
async def search(q: str):
    return {
        "query": q,
        "results": [],
        "message": "Servicio de búsqueda inicializado; índice pendiente de implementación."
    }
