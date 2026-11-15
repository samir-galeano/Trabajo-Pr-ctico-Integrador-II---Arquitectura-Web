import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import nats
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from opentelemetry import propagate, trace
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app import auth
from app import database

SERVICE = "audit-svc"
NATS_URL = os.getenv("NATS_URL", "nats://nats:4222")
EVENT_SUBJECT = os.getenv("EVENT_SUBJECT", "bookstack.content.events")

REQUESTS = Counter("audit_http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("audit_http_request_duration_seconds", "HTTP request latency", ["method", "path"])
EVENTS = Counter("audit_events_total", "Eventos consumidos", ["result"])

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(SERVICE)
tracer = trace.get_tracer(SERVICE)

nc = None
js = None
consumer_task = None
db_ready = False


def log_json(level, message, request_id="-", **extra):
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "service": SERVICE,
        "request_id": request_id,
        "message": message,
        **extra,
    }
    getattr(logger, level.lower(), logger.info)(json.dumps(data, ensure_ascii=False))


def parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_record(event):
    """Convierte el sobre de evento en un registro de auditoría."""
    event_id = event.get("event_id")
    if not event_id:
        raise ValueError("El evento no contiene event_id")

    payload = event.get("payload") or {}
    triggered_by = payload.get("triggered_by", "unknown")
    if isinstance(triggered_by, dict):
        actor = triggered_by.get("name", "unknown")
    else:
        actor = str(triggered_by)

    event_type = event.get("type", "unknown")
    # bookstack.page.updated -> page
    parts = event_type.split(".")
    entity_type = parts[1] if len(parts) >= 3 else ""

    return {
        "event_id": str(event_id),
        "type": event_type,
        "entity_type": entity_type,
        "entity_id": str(payload.get("page_id") or payload.get("id") or ""),
        "entity_name": payload.get("name", ""),
        "actor": actor,
        "occurred_at": parse_timestamp(event.get("occurred_at")),
        "request_id": event.get("request_id", "-"),
        "payload": payload,
    }


def process_event(event):
    """Registra el evento. Devuelve False si ya estaba (idempotencia)."""
    return database.insert_record(build_record(event))


async def ensure_database():
    global db_ready

    while not db_ready:
        try:
            await asyncio.to_thread(database.initialize)
            db_ready = True
            log_json("INFO", "Esquema de auditoría listo")
        except Exception as exc:
            log_json("ERROR", "PostgreSQL no disponible, reintentando", error=str(exc))
            await asyncio.sleep(3)


async def consume_events():
    global nc, js

    await ensure_database()

    while True:
        try:
            if nc is None or not nc.is_connected:
                nc = await nats.connect(NATS_URL)
                js = nc.jetstream()

            try:
                await js.add_consumer(
                    "BOOKSTACK",
                    durable_name="AUDIT_CONSUMER",
                    filter_subject=EVENT_SUBJECT,
                    ack_policy="explicit",
                    max_deliver=5,
                )
            except Exception:
                pass

            subscription = await js.pull_subscribe(
                EVENT_SUBJECT,
                durable="AUDIT_CONSUMER",
                stream="BOOKSTACK",
            )

            log_json("INFO", "Consumidor durable conectado")

            while True:
                try:
                    messages = await subscription.fetch(batch=10, timeout=5)
                except nats.errors.TimeoutError:
                    continue

                for message in messages:
                    headers = dict(message.headers or {})
                    try:
                        event = json.loads(message.data.decode())
                        # Tramo hijo del publish de event-bridge (traceparent en cabeceras NATS)
                        with tracer.start_as_current_span(
                            f"process {EVENT_SUBJECT}",
                            context=propagate.extract(headers),
                            kind=trace.SpanKind.CONSUMER,
                            attributes={
                                "messaging.system": "nats",
                                "messaging.consumer.group.name": "AUDIT_CONSUMER",
                                "messaging.message.id": str(event.get("event_id", "")),
                                "request_id": event.get("request_id", "-"),
                            },
                        ):
                            inserted = await asyncio.to_thread(process_event, event)
                        await message.ack()
                        EVENTS.labels("inserted" if inserted else "duplicate").inc()
                        log_json(
                            "INFO",
                            "Evento procesado" if inserted else "Evento duplicado ignorado",
                            event.get("request_id", "-"),
                            event_id=event.get("event_id"),
                        )
                    except Exception as exc:
                        EVENTS.labels("error").inc()
                        log_json("ERROR", "Error procesando evento", error=str(exc))
                        await message.nak()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_json("ERROR", "Consumidor reconectando", error=str(exc))
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_task

    consumer_task = asyncio.create_task(consume_events())
    yield

    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    if nc and nc.is_connected:
        await nc.close()


app = FastAPI(title="Audit Service", version="2.0.0", lifespan=lifespan)


# Bearer (BearerAuth de los contratos). Se registra antes que el middleware
# de métricas para quedar por dentro: los 401 también se miden y se loguean.
app.middleware("http")(auth.middleware)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    start = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    try:
        response = await call_next(request)
    except Exception:
        REQUESTS.labels(request.method, request.url.path, "500").inc()
        raise

    elapsed = time.perf_counter() - start
    response.headers["X-Request-ID"] = request_id
    REQUESTS.labels(request.method, request.url.path, str(response.status_code)).inc()
    LATENCY.labels(request.method, request.url.path).observe(elapsed)

    log_json(
        "INFO",
        "HTTP request",
        request_id,
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_seconds=round(elapsed, 6),
    )
    return response


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "service": SERVICE}


@app.get("/readyz")
async def readyz():
    if nc is None or not nc.is_connected or js is None:
        raise HTTPException(status_code=503, detail="NATS no disponible")
    try:
        await asyncio.to_thread(database.ping)
    except Exception:
        raise HTTPException(status_code=503, detail="PostgreSQL no disponible")
    return {"status": "ready", "service": SERVICE}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/audit")
async def query_audit(
    entity: str | None = Query(default=None, description="Tipo de entidad: page, book, chapter..."),
    id: str | None = Query(default=None, description="ID de la entidad"),
    entity_id: str | None = Query(default=None, description="Alias de id"),
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    try:
        items = await asyncio.to_thread(
            database.query_records,
            entity_type=entity,
            entity_id=id or entity_id,
            start=start,
            end=end,
            limit=limit,
        )
    except Exception as exc:
        log_json("ERROR", "Consulta de auditoría fallida", error=str(exc))
        raise HTTPException(status_code=503, detail="PostgreSQL no disponible")

    return {"count": len(items), "items": items}


@app.get("/audit/{event_id}")
async def get_audit_record(event_id: str):
    try:
        record = await asyncio.to_thread(database.get_record, event_id)
    except Exception as exc:
        log_json("ERROR", "Consulta de auditoría fallida", error=str(exc))
        raise HTTPException(status_code=503, detail="PostgreSQL no disponible")

    if record is None:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    return record


@app.get("/")
async def root():
    return {"service": SERVICE, "version": "2.0.0"}
