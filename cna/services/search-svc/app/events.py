import asyncio
import json
import logging
import os

import httpx
import nats

from app.database import apply_event
from app.main import (
    BOOKSTACK_URL,
    TOKEN_ID,
    TOKEN_SECRET,
    VERIFY_TLS,
    extract_text,
)

logger = logging.getLogger("search-svc.events")

NATS_URL = os.getenv("NATS_URL", "nats://nats:4222")
STREAM = "BOOKSTACK"
DURABLE = "SEARCH_CONSUMER"
SUBJECT = "bookstack.content.events"
DLQ_STREAM = "BOOKSTACK_DLQ"
DLQ_SUBJECT = "bookstack.content.dlq"
MAX_DELIVERIES = 5


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
        if not TOKEN_ID or not TOKEN_SECRET:
            raise RuntimeError("Credenciales BookStack no configuradas")

        headers = {
            "Authorization": f"Token {TOKEN_ID}:{TOKEN_SECRET}",
            "Accept": "application/json",
            "Host": "bookstack.local",
        }

        async with httpx.AsyncClient(
            verify=VERIFY_TLS,
            timeout=30
        ) as client:
            response = await client.get(
                f"{BOOKSTACK_URL}/api/pages/{page_id}",
                headers=headers
            )
            response.raise_for_status()
            page = response.json()

            book_slug = ""
            book_id = page.get("book_id")
            if book_id:
                book_response = await client.get(
                    f"{BOOKSTACK_URL}/api/books/{book_id}",
                    headers=headers
                )
                book_response.raise_for_status()
                book_slug = book_response.json().get("slug", "")

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

    logger.info(json.dumps({
        "level": "INFO",
        "service": "search-svc",
        "request_id": request_id,
        "message": (
            f"Evento aplicado: {event_type}"
            if applied else f"Evento duplicado ignorado: {event_id}"
        )
    }))

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

    logger.error(json.dumps({
        "level": "ERROR",
        "service": "search-svc",
        "message": "Evento enviado a DLQ",
        "stream_sequence": metadata.sequence.stream,
        "delivery_count": metadata.num_delivered,
        "reason": reason
    }))


async def consume_events():
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    subscription = await js.pull_subscribe(
        SUBJECT,
        durable=DURABLE,
        stream=STREAM
    )

    logger.info("Consumidor durable SEARCH_CONSUMER conectado")

    try:
        while True:
            try:
                messages = await subscription.fetch(batch=10, timeout=2)
                for message in messages:
                    try:
                        await process_message(message)
                    except Exception as error:
                        metadata = message.metadata
                        if metadata.num_delivered >= MAX_DELIVERIES:
                            try:
                                await send_to_dlq(js, message, error)
                            except Exception:
                                logger.exception("No se pudo publicar en DLQ")
                                await message.nak()
                        else:
                            logger.exception("Error procesando evento")
                            await message.nak()
            except asyncio.TimeoutError:
                continue
    finally:
        await nc.close()
