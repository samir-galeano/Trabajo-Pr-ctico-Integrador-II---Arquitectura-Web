import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import nats
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from opentelemetry import propagate, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

SERVICE = "event-bridge"
NATS_URL = os.getenv("NATS_URL", "nats://nats:4222")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
SUBJECT = "bookstack.content.events"

REQUESTS = Counter(
    "http_requests_total",
    "Total de solicitudes HTTP",
    ["service", "method", "path", "status_code"]
)

LATENCY = Histogram(
    "http_request_duration_seconds",
    "Duracion de solicitudes HTTP",
    ["service", "method", "path"]
)

# Solo el mensaje: cada línea ya es un JSON completo.
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(SERVICE)
tracer = trace.get_tracer(SERVICE)

nc = None
js = None

EVENT_TYPES = {
    "page_create": "bookstack.page.created",
    "page_update": "bookstack.page.updated",
    "page_delete": "bookstack.page.deleted",
}


def build_event(data, request_id):
    event_name = data.get("event")
    event_type = EVENT_TYPES.get(event_name)

    if not event_type:
        raise ValueError(f"Evento no soportado: {event_name}")

    item = data.get("related_item") or {}
    page_id = item.get("id")

    if not page_id:
        raise ValueError("El webhook no contiene related_item.id")

    return {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "version": "1.0",
        "occurred_at": data.get("triggered_at")
            or datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "payload": {
            "page_id": int(page_id),
            "name": item.get("name"),
            "slug": item.get("slug"),
            "book_id": item.get("book_id"),
            "triggered_by": data.get("triggered_by"),
            "text": data.get("text"),
            "url": data.get("url"),
        }
    }


async def publish_event(event):
    """Publica en JetStream con el contexto de traza en las cabeceras NATS,
    para que la traza siga en search-svc y audit-svc."""
    with tracer.start_as_current_span(
        f"publish {SUBJECT}",
        kind=trace.SpanKind.PRODUCER,
        attributes={
            "messaging.system": "nats",
            "messaging.destination.name": SUBJECT,
            "messaging.message.id": event["event_id"],
            "bookstack.event_type": event["type"],
            "request_id": event["request_id"],
        },
    ):
        headers = {"X-Request-ID": event["request_id"]}
        propagate.inject(headers)
        await js.publish(SUBJECT, json.dumps(event).encode("utf-8"), headers=headers)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global nc, js

    if not WEBHOOK_TOKEN:
        raise RuntimeError("WEBHOOK_TOKEN no configurado")

    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": SERVICE,
        "request_id": "system",
        "message": "Conectado a NATS JetStream"
    }))

    yield

    if nc and not nc.is_closed:
        await nc.close()


app = FastAPI(title="Event Bridge", lifespan=lifespan)

WEBHOOK_PREFIX = "/webhooks/bookstack/"
REDACTED_PATH = WEBHOOK_PREFIX + "[REDACTED]"


def redact_webhook_token(span, scope):
    """La URL del webhook lleva el token: no debe llegar a Jaeger."""
    if span is None or not span.is_recording():
        return
    if not scope.get("path", "").startswith(WEBHOOK_PREFIX):
        return
    for key in ("http.target", "url.path"):
        span.set_attribute(key, REDACTED_PATH)
    for key in ("http.url", "url.full"):
        span.set_attribute(key, REDACTED_PATH)


# Instrumentación explícita (la automática de FastAPI está desactivada en el
# Dockerfile con OTEL_PYTHON_DISABLED_INSTRUMENTATIONS) para poder redactar.
FastAPIInstrumentor.instrument_app(
    app,
    server_request_hook=redact_webhook_token,
    excluded_urls="healthz,readyz,metrics",
)


@app.middleware("http")
async def metrics_and_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()

    response = await call_next(request)

    duration = time.perf_counter() - start
    response.headers["X-Request-ID"] = request_id

    # Plantilla de la ruta (/webhooks/bookstack/{token}): el token del
    # webhook no debe quedar en logs ni en etiquetas de Prometheus.
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)

    REQUESTS.labels(
        SERVICE, request.method, path, str(response.status_code)
    ).inc()

    LATENCY.labels(SERVICE, request.method, path).observe(duration)

    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": SERVICE,
        "request_id": request_id,
        "message": f"{request.method} {path} "
                   f"{response.status_code} {duration:.4f}s"
    }))

    return response


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "service": SERVICE}


@app.get("/readyz")
async def readyz():
    if nc is None or nc.is_closed or not nc.is_connected:
        raise HTTPException(status_code=503, detail="NATS no disponible")

    try:
        await js.stream_info("BOOKSTACK")
    except Exception:
        raise HTTPException(status_code=503, detail="Stream BOOKSTACK no disponible")

    return {"status": "ready", "service": SERVICE}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/webhooks/bookstack/{token}")
async def receive_webhook(
    token: str,
    request: Request,
    x_request_id: str | None = Header(default=None)
):
    if not hmac.compare_digest(token, WEBHOOK_TOKEN):
        raise HTTPException(status_code=401, detail="Token invalido")

    data = await request.json()
    request_id = x_request_id or str(uuid.uuid4())

    try:
        event = build_event(data, request_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        await publish_event(event)
    except Exception:
        logger.exception("No se pudo publicar el evento en NATS")
        raise HTTPException(status_code=503, detail="Broker no disponible")

    return {
        "status": "published",
        "event_id": event["event_id"],
        "type": event["type"],
        "request_id": request_id
    }
