"""Código compartido entre la API (app.main) y el worker (app.worker).

Los trabajos viven en Redis, no en memoria del proceso:

  export:job:{id}    hash con el estado del trabajo
  export:jobs        sorted set (score = creación) para listar
  export:queue       lista de job_id pendientes (LPUSH al encolar, RPOP al tomar)
  export:processing  lista de job_id tomados por algún worker
  export:lease:{id}  clave con TTL que el worker dueño renueva (heartbeat)

Si un worker muere a mitad de un job, su lease vence y cualquier otro
worker lo devuelve a la cola (reap_orphans). Así un job nunca queda en
"running" indefinidamente (experimento X3).
"""
import os
import json
from datetime import datetime, timezone

import boto3
import httpx
import redis
from botocore.client import Config

from app import credentials


SERVICE = os.getenv("SERVICE_NAME", "export-svc")

# Redis (cola + estado de los jobs)
REDIS_URL = os.getenv("REDIS_URL", "redis://export-redis:6379/0")

# MinIO
BUCKET = os.getenv("MINIO_BUCKET", "bookstack-exports")
ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
PUBLIC_ENDPOINT = os.getenv(
    "MINIO_PUBLIC_ENDPOINT",
    "http://192.168.0.6:9000"
)
ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
URL_EXPIRES = int(os.getenv("EXPORT_URL_EXPIRES", "900"))

# BookStack API
BOOKSTACK_URL = os.getenv(
    "BOOKSTACK_BASE_URL",
    "https://bookstack.local"
).rstrip("/")
VERIFY_TLS = os.getenv(
    "BOOKSTACK_VERIFY_TLS",
    "false"
).lower() == "true"
BOOKSTACK_TIMEOUT = float(os.getenv("BOOKSTACK_TIMEOUT", "30"))

# Cola
LEASE_TTL = int(os.getenv("EXPORT_LEASE_TTL", "15"))
MAX_ATTEMPTS = int(os.getenv("EXPORT_MAX_ATTEMPTS", "3"))

JOB_KEY = "export:job:{}"
LEASE_KEY = "export:lease:{}"
JOBS_INDEX = "export:jobs"
QUEUE = "export:queue"
PROCESSING = "export:processing"

# Formato pedido -> (segmento de la API de BookStack, extensión, Content-Type)
FORMATS = {
    "pdf": ("pdf", "pdf", "application/pdf"),
    "markdown": ("markdown", "md", "text/markdown; charset=utf-8"),
}

STATUSES = ("queued", "running", "done", "failed")


class PermanentError(Exception):
    """Error que no se arregla reintentando (p. ej. la página no existe)."""


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


_redis = None


def get_redis():
    global _redis

    if _redis is None:
        _redis = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            health_check_interval=30
        )

    return _redis


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


def presigned_url(object_key):
    # La URL prefirmada utiliza la dirección accesible desde
    # el navegador/host, no el nombre interno del contenedor.
    return get_s3(PUBLIC_ENDPOINT).generate_presigned_url(
        "get_object",
        Params={
            "Bucket": BUCKET,
            "Key": object_key
        },
        ExpiresIn=URL_EXPIRES
    )


def bookstack_headers(request_id=None):
    headers = {
        "Authorization": credentials.authorization_header(),
        "Accept": "*/*",
        "Host": "bookstack.local"
    }

    if request_id:
        headers["X-Request-ID"] = request_id

    return headers


def get_bookstack_client():
    if not credentials.has_token():
        raise RuntimeError("BookStack API credentials are not configured")

    return httpx.Client(
        verify=VERIFY_TLS,
        timeout=BOOKSTACK_TIMEOUT,
        follow_redirects=False
    )


# --------------------------------------------------------------------------
# Estado de los jobs
# --------------------------------------------------------------------------

def get_job(job_id):
    job = get_redis().hgetall(JOB_KEY.format(job_id))

    if not job:
        return None

    for field in ("entity_id", "attempts"):
        if field in job:
            job[field] = int(job[field])

    return job


def update_job(job_id, **fields):
    fields["updated_at"] = now_iso()
    get_redis().hset(JOB_KEY.format(job_id), mapping=fields)


def enqueue_job(job):
    created = datetime.fromisoformat(job["created_at"]).timestamp()

    pipe = get_redis().pipeline(transaction=True)
    pipe.hset(JOB_KEY.format(job["job_id"]), mapping=job)
    pipe.zadd(JOBS_INDEX, {job["job_id"]: created})
    pipe.lpush(QUEUE, job["job_id"])
    pipe.execute()


def queue_depths():
    r = get_redis()
    return {
        "queued": r.llen(QUEUE),
        "processing": r.llen(PROCESSING)
    }
