import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import nats
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

SERVICE = "audit-svc"
NATS_URL = os.getenv("NATS_URL", "nats://nats:4222")
EVENT_SUBJECT = os.getenv("EVENT_SUBJECT", "bookstack.content.events")
AUDIT_FILE = os.getenv("AUDIT_FILE", "/data/audit.jsonl")

REQUESTS = Counter("audit_http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("audit_http_request_duration_seconds", "HTTP request latency")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(SERVICE)

records = {}
nc = None
js = None
consumer_task = None


def log_json(level, message, request_id="-", **extra):
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "service": SERVICE,
        "request_id": request_id,
        "message": message,
        **extra,
    }
    getattr(logger, level.lower(), logger.info)(json.dumps(data))


def persist(record):
    os.makedirs(os.path.dirname(AUDIT_FILE), exist_ok=True)
    with open(AUDIT_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def process_event(event):
    event_id = event.get("event_id")
    if not event_id:
        raise ValueError("El evento no contiene event_id")

    if event_id in records:
        return False

    payload = event.get("payload") or {}
    triggered_by = payload.get("triggered_by", "unknown")
    if isinstance(triggered_by, dict):
        actor = triggered_by.get("name", "unknown")
    else:
        actor = str(triggered_by)

    record = {
        "event_id": event_id,
        "type": event.get("type", "unknown"),
        "occurred_at": event.get("occurred_at"),
        "request_id": event.get("request_id", "-"),
        "entity_id": str(payload.get("page_id") or payload.get("id") or ""),
        "entity_name": payload.get("name", ""),
        "actor": actor,
        "payload": payload,
    }

    persist(record)
    records[event_id] = record
    return True


async def consume_events():
    global nc, js

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
                    try:
                        event = json.loads(message.data.decode())
                        inserted = process_event(event)
                        await message.ack()
                        log_json(
                            "INFO",
                            "Evento procesado" if inserted else "Evento duplicado ignorado",
                            event.get("request_id", "-"),
                            event_id=event.get("event_id"),
                        )
                    except Exception as exc:
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

    if os.path.exists(AUDIT_FILE):
        with open(AUDIT_FILE, encoding="utf-8") as file:
            for line in file:
                try:
                    record = json.loads(line)
                    records[record["event_id"]] = record
                except (ValueError, KeyError):
                    continue

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


app = FastAPI(title="Audit Service", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    start = time.perf_counter()
    request_id = request.headers.get("X-Request-ID", "-")

    try:
        response = await call_next(request)
    except Exception:
        REQUESTS.labels(request.method, request.url.path, "500").inc()
        raise

    elapsed = time.perf_counter() - start
    response.headers["X-Request-ID"] = request_id
    REQUESTS.labels(request.method, request.url.path, str(response.status_code)).inc()
    LATENCY.observe(elapsed)

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
    return {"status": "ready", "service": SERVICE}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/audit")
async def query_audit(
    entity_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
):
    result = list(records.values())

    if entity_id:
        result = [r for r in result if r["entity_id"] == entity_id]
    if start:
        result = [r for r in result if r.get("occurred_at") and datetime.fromisoformat(r["occurred_at"].replace("Z", "+00:00")) >= start]
    if end:
        result = [r for r in result if r.get("occurred_at") and datetime.fromisoformat(r["occurred_at"].replace("Z", "+00:00")) <= end]

    return {"count": len(result), "items": result}


@app.get("/")
async def root():
    return {"service": SERVICE, "version": "1.0.0"}
