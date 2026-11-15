import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import nats
from opentelemetry import propagate, trace

from app import bookstack
from app.bookstack import BOOKSTACK_URL, CircuitOpenError
from app.database import apply_event
from app.main import extract_text

logger = logging.getLogger("search-svc.events")

NATS_URL = os.getenv("NATS_URL", "nats://nats:4222")
STREAM = "BOOKSTACK"
DURABLE = "SEARCH_CONSUMER"
SUBJECT = "bookstack.content.events"
DLQ_STREAM = "BOOKSTACK_DLQ"
DLQ_SUBJECT = "bookstack.content.dlq"
MAX_DELIVERIES = 5

tracer = trace.get_tracer("search-svc")


def log_json(level, message, request_id="-", **extra):
    """Una línea JSON con request_id, para correlacionar en Loki."""
    logger.log(getattr(logging, level), json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "service": "search-svc",
        "request_id": request_id,
        "message": message,
        **extra
    }, ensure_ascii=False))


def message_request_id(message):
    headers = dict(message.headers or {})
    if headers.get("X-Request-ID"):
        return headers["X-Request-ID"]
    try:
        return json.loads(message.data.decode()).get("request_id", "-")
    except (ValueError, AttributeError):
        return "-"


def consumer_span(message):
    """Tramo de consumo hijo del publish de event-bridge (traceparent en las cabeceras NATS)."""
    headers = dict(message.headers or {})
    return tracer.start_as_current_span(
        f"process {SUBJECT}",
        context=propagate.extract(headers),
        kind=trace.SpanKind.CONSUMER,
        attributes={
            "messaging.system": "nats",
            "messaging.destination.name": message.subject,
            "messaging.consumer.group.name": DURABLE,
            "request_id": headers.get("X-Request-ID", ""),
        },
    )


async def process_message(message):
    event = json.loads(message.data.decode())
    event_id = event["event_id"]
    event_type = event["type"]
    payload = event.get("payload", {})
    request_id = event.get("request_id", "")

    page_id = payload.get("page_id") or payload.get("id")
    if not page_id:
        raise ValueError("El evento no contiene page_id ni id")

    page_id = int(page_id)
    deleted = event_type.endswith("page.deleted")

    if deleted:
        applied = apply_event(event_id, page_id, deleted=True)
    elif (
        event_type.endswith("page.updated")
        or event_type.endswith("page.created")
    ):
        async with bookstack.new_client() as client:
            page = await bookstack.get_json(
                client, f"/api/pages/{page_id}", request_id=request_id
            )

            book_slug = ""
            book_id = page.get("book_id")
            if book_id:
                book = await bookstack.get_json(
                    client, f"/api/books/{book_id}", request_id=request_id
                )
                book_slug = book.get("slug", "")

            page_data = {
                "id": page["id"],
                "name": page.get("name", ""),
                "content": extract_text(page.get("html", "")),
                "url": (
                    page.get("url")
                    or f"{BOOKSTACK_URL}/books/{book_slug}/page/"
                       f"{page.get('slug', '')}"
                ),
                "updated_at": page.get("updated_at")
            }

        applied = apply_event(event_id, page_id, page=page_data)
    else:
        raise ValueError(f"Tipo de evento no soportado: {event_type}")

    log_json(
        "INFO",
        f"Evento aplicado: {event_type}" if applied else "Evento duplicado ignorado",
        request_id,
        event_id=event_id,
        page_id=page_id
    )

    await message.ack()



async def send_to_dlq(js, message, error):
    metadata = message.metadata
    reason = " ".join(str(error).split())[:500]
    message_id = f"dlq-{metadata.stream}-{metadata.sequence.stream}"

    headers = {
        "Nats-Msg-Id": message_id,
        "Nats-Expected-Stream": DLQ_STREAM,
        "X-DLQ-Original-Stream": metadata.stream,
        "X-DLQ-Original-Subject": message.subject,
        "X-DLQ-Delivery-Count": str(metadata.num_delivered),
        "X-DLQ-Reason": reason,
    }

    await js.publish(DLQ_SUBJECT, message.data, headers=headers)
    await message.ack()

    log_json(
        "ERROR",
        "Evento enviado a DLQ",
        message_request_id(message),
        stream_sequence=metadata.sequence.stream,
        delivery_count=metadata.num_delivered,
        reason=reason
    )


async def consume_events():
    """Consume para siempre: si NATS o el stream no están (arranque en Swarm,
    reinicio de NATS en X4), reintenta en lugar de dejar el consumidor muerto."""
    while True:
        try:
            await consume_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_json(
                "ERROR",
                "Consumidor desconectado, reintentando en 3 s",
                error=f"{type(error).__name__}: {error}"
            )
            await asyncio.sleep(3)


async def consume_once():
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    subscription = await js.pull_subscribe(
        SUBJECT,
        durable=DURABLE,
        stream=STREAM
    )

    log_json("INFO", "Consumidor durable SEARCH_CONSUMER conectado")

    try:
        while True:
            # Con el circuito abierto no se piden mensajes: quedan en el
            # stream (durables) hasta que BookStack vuelva a responder.
            wait = bookstack.breaker.retry_after()
            if wait > 0:
                await asyncio.sleep(wait)
                continue

            try:
                messages = await subscription.fetch(batch=10, timeout=2)
                for message in messages:
                    try:
                        with consumer_span(message):
                            await process_message(message)
                    except CircuitOpenError as error:
                        # No es culpa del evento: no cuenta para la DLQ.
                        log_json(
                            "WARNING",
                            "Circuito abierto, evento devuelto al stream",
                            message_request_id(message),
                            retry_after=round(error.retry_after, 1)
                        )
                        await message.nak(delay=max(error.retry_after, 1))
                    except Exception as error:
                        metadata = message.metadata
                        if metadata.num_delivered >= MAX_DELIVERIES:
                            try:
                                await send_to_dlq(js, message, error)
                            except Exception as dlq_error:
                                log_json(
                                    "ERROR",
                                    "No se pudo publicar en DLQ",
                                    message_request_id(message),
                                    error=str(dlq_error)
                                )
                                await message.nak()
                        else:
                            log_json(
                                "ERROR",
                                "Error procesando evento",
                                message_request_id(message),
                                error=f"{type(error).__name__}: {error}",
                                delivery=metadata.num_delivered
                            )
                            await message.nak()
            except asyncio.TimeoutError:
                continue
    finally:
        await nc.close()
