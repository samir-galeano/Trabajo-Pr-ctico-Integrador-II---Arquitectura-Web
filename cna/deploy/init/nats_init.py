"""Crea los streams de NATS JetStream que usan los microservicios.

Se ejecuta como contenedor de una sola vez (servicio nats-init del compose)
antes de que arranquen event-bridge, search-svc, audit-svc y media-svc.
Es idempotente: si un stream ya existe, no lo toca.
"""
import asyncio
import os
import sys

import nats
from nats.js.errors import NotFoundError

NATS_URL = os.getenv("NATS_URL", "nats://nats:4222")

STREAMS = [
    ("BOOKSTACK", ["bookstack.content.events"]),
    ("BOOKSTACK_DLQ", ["bookstack.content.dlq"]),
    ("MEDIA", ["media.uploaded"]),
]


async def connect_with_retry(attempts=30, delay=2):
    for attempt in range(1, attempts + 1):
        try:
            return await nats.connect(NATS_URL)
        except Exception as exc:
            print(f"[nats-init] NATS no disponible ({attempt}/{attempts}): {exc}", flush=True)
            await asyncio.sleep(delay)
    print("[nats-init] No se pudo conectar a NATS", flush=True)
    sys.exit(1)


async def main():
    nc = await connect_with_retry()
    js = nc.jetstream()

    for name, subjects in STREAMS:
        try:
            await js.stream_info(name)
            print(f"[nats-init] Stream {name} ya existe", flush=True)
        except NotFoundError:
            await js.add_stream(name=name, subjects=subjects)
            print(f"[nats-init] Stream {name} creado ({', '.join(subjects)})", flush=True)

    await nc.close()


if __name__ == "__main__":
    asyncio.run(main())
