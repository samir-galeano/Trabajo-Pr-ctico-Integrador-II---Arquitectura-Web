"""Eventos de exportación en NATS JetStream (canal bookstack.export.events).

  export.requested   la API encoló un job
  export.completed   un worker lo terminó (archivo en MinIO)
  export.failed      falló sin más reintentos

Usa el sobre común de AsyncAPI (event_id, type, version, occurred_at,
request_id, payload) y propaga el contexto de traza en las cabeceras.
Publicar es "best effort": si NATS no responde se registra y se cuenta en
export_events_published_total{result="error"}, pero el job sigue su curso.
Sin NATS_URL los eventos quedan desactivados.
"""
import asyncio
import json
import os
import uuid

import nats
from opentelemetry import propagate
from prometheus_client import Counter

from app.common import log_json, now_iso

NATS_URL = os.getenv("NATS_URL", "")
SUBJECT = os.getenv("EXPORT_EVENTS_SUBJECT", "bookstack.export.events")
VERSION = "1.0"

PUBLISHED = Counter(
    "export_events_published_total",
    "Eventos de exportación publicados en NATS",
    ["type", "result"]
)


def build_event(event_type, job, **extra):
    return {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "version": VERSION,
        "occurred_at": now_iso(),
        "request_id": job.get("request_id", "-"),
        "payload": {
            "job_id": job["job_id"],
            "entity_type": job.get("entity_type"),
            "entity_id": int(job["entity_id"]) if job.get("entity_id") not in (None, "") else None,
            "format": job.get("format"),
            "attempts": int(job.get("attempts") or 0),
            **extra,
        },
    }


async def _send(event, headers):
    nc = await nats.connect(NATS_URL, connect_timeout=2, max_reconnect_attempts=0, allow_reconnect=False)
    try:
        await nc.jetstream().publish(SUBJECT, json.dumps(event).encode("utf-8"), headers=headers, timeout=2)
    finally:
        await nc.close()


def publish(event_type, job, **extra):
    """Publica el evento. Devuelve True si JetStream lo confirmó."""
    if not NATS_URL:
        return False

    event = build_event(event_type, job, **extra)
    headers = {"X-Request-ID": event["request_id"]}
    propagate.inject(headers)

    try:
        asyncio.run(_send(event, headers))
    except Exception as exc:
        PUBLISHED.labels(event_type, "error").inc()
        log_json("WARNING", "No se pudo publicar el evento", event["request_id"],
                 event_type=event_type, job_id=job["job_id"], error=str(exc))
        return False

    PUBLISHED.labels(event_type, "ok").inc()
    log_json("INFO", "Evento publicado", event["request_id"],
             event_type=event_type, event_id=event["event_id"], job_id=job["job_id"])
    return True
