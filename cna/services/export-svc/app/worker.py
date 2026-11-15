"""export-worker: proceso separado de la API que ejecuta los jobs de exportación.

Se arranca con la misma imagen que la API:  python -m app.worker
Puede escalarse a N réplicas (docker compose up -d --scale export-worker=3).

Ciclo de un job:
  1. claim: mueve el job de export:queue a export:processing y crea su
     lease con TTL, todo en un solo script Lua (atómico).
  2. Un hilo renueva el lease cada LEASE_TTL/3 mientras el job corre.
  3. Pide la exportación a la API de BookStack, sube el archivo a MinIO y
     marca el job como done (solo si el lease sigue siendo suyo).
  4. Cada worker revisa periódicamente export:processing: los jobs sin
     lease vivo pertenecían a un worker muerto y vuelven a la cola, o
     pasan a failed si agotaron EXPORT_MAX_ATTEMPTS.
"""
import os
import time
import uuid
import signal
import socket
import threading

from opentelemetry import propagate, trace
from prometheus_client import Counter, Histogram, start_http_server

from app import events
from app.common import (
    BUCKET,
    BOOKSTACK_URL,
    FORMATS,
    LEASE_TTL,
    MAX_ATTEMPTS,
    PROCESSING,
    QUEUE,
    PermanentError,
    bookstack_headers,
    get_bookstack_client,
    get_job,
    get_redis,
    get_s3,
    log_json,
    now_iso,
)


WORKER_ID = os.getenv("WORKER_ID") or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
METRICS_PORT = int(os.getenv("WORKER_METRICS_PORT", "9101"))
POLL_INTERVAL = float(os.getenv("EXPORT_POLL_INTERVAL", "1"))
REAP_INTERVAL = float(os.getenv("EXPORT_REAP_INTERVAL", "5"))
# Demora artificial por job (segundos) para los experimentos X3 y C2.
SIMULATED_DELAY = float(os.getenv("EXPORT_SIMULATED_DELAY", "0"))

tracer = trace.get_tracer("export-worker")

JOBS = Counter(
    "export_worker_jobs_total",
    "Jobs terminados por el worker",
    ["status"]
)

REQUEUED = Counter(
    "export_worker_requeued_total",
    "Jobs devueltos a la cola",
    ["reason"]
)

JOB_DURATION = Histogram(
    "export_worker_job_duration_seconds",
    "Duración de un job de exportación",
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120)
)

# La profundidad de la cola la expone la API (export_queue_depth).


# KEYS: queue, processing   ARGV: worker_id, lease_ttl, now
CLAIM_LUA = """
local id = redis.call('RPOP', KEYS[1])
if not id then return false end
redis.call('LPUSH', KEYS[2], id)
redis.call('SET', 'export:lease:' .. id, ARGV[1], 'EX', ARGV[2])
local job = 'export:job:' .. id
redis.call('HINCRBY', job, 'attempts', 1)
redis.call('HSET', job, 'status', 'running', 'worker', ARGV[1], 'updated_at', ARGV[3])
return id
"""

# KEYS: lease   ARGV: worker_id, lease_ttl
RENEW_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

# KEYS: processing, queue
# ARGV: job_id, worker_id, requeue ('1'/'0'), campo1, valor1, campo2, valor2...
FINISH_LUA = """
local lease = 'export:lease:' .. ARGV[1]
if redis.call('GET', lease) ~= ARGV[2] then return 0 end
redis.call('DEL', lease)
redis.call('LREM', KEYS[1], 0, ARGV[1])
redis.call('HSET', 'export:job:' .. ARGV[1], (unpack or table.unpack)(ARGV, 4))
if ARGV[3] == '1' then redis.call('LPUSH', KEYS[2], ARGV[1]) end
return 1
"""

# KEYS: queue, processing   ARGV: max_attempts, now
REAP_LUA = """
local out = {}
for _, id in ipairs(redis.call('LRANGE', KEYS[2], 0, -1)) do
  if redis.call('EXISTS', 'export:lease:' .. id) == 0 then
    redis.call('LREM', KEYS[2], 0, id)
    local job = 'export:job:' .. id
    local attempts = tonumber(redis.call('HGET', job, 'attempts') or '0')
    if attempts >= tonumber(ARGV[1]) then
      redis.call('HSET', job, 'status', 'failed', 'worker', '',
        'error', 'Worker perdido y reintentos agotados', 'updated_at', ARGV[2])
      table.insert(out, id .. ':failed')
    else
      redis.call('HSET', job, 'status', 'queued', 'worker', '', 'updated_at', ARGV[2])
      redis.call('RPUSH', KEYS[1], id)
      table.insert(out, id .. ':requeued')
    end
  end
end
return out
"""


def claim():
    return get_redis().eval(CLAIM_LUA, 2, QUEUE, PROCESSING, WORKER_ID, LEASE_TTL, now_iso())


def finish(job_id, requeue=False, **fields):
    fields["updated_at"] = now_iso()
    args = [job_id, WORKER_ID, "1" if requeue else "0"]

    for key, value in fields.items():
        args += [key, value]

    return get_redis().eval(FINISH_LUA, 2, PROCESSING, QUEUE, *args) == 1


def reap_orphans():
    """Devuelve a la cola los jobs cuyo worker murió (lease vencido)."""
    results = get_redis().eval(REAP_LUA, 2, QUEUE, PROCESSING, MAX_ATTEMPTS, now_iso())

    for item in results:
        job_id, outcome = item.rsplit(":", 1)
        REQUEUED.labels("lease_expired").inc()
        log_json(
            "WARNING",
            "Job huérfano recuperado",
            job_id=job_id,
            outcome=outcome,
            worker=WORKER_ID
        )
        if outcome == "failed":
            job = get_job(job_id)
            if job:
                events.publish("export.failed", job, error=job.get("error", ""))

    return results


class Heartbeat(threading.Thread):
    """Renueva el lease del job mientras el worker lo procesa."""

    def __init__(self, job_id):
        super().__init__(daemon=True)
        self.lease_key = f"export:lease:{job_id}"
        self.stopped = threading.Event()

    def run(self):
        while not self.stopped.wait(max(LEASE_TTL / 3, 1)):
            try:
                get_redis().eval(RENEW_LUA, 1, self.lease_key, WORKER_ID, LEASE_TTL)
            except Exception as exc:
                log_json("WARNING", "No se pudo renovar el lease", error=str(exc))

    def stop(self):
        self.stopped.set()


def fetch_export(job):
    """Pide a BookStack la exportación de la página o libro del job."""
    segment, extension, content_type = FORMATS[job["format"]]
    url = (
        f"{BOOKSTACK_URL}/api/{job['entity_type']}s/"
        f"{job['entity_id']}/export/{segment}"
    )

    with get_bookstack_client() as client:
        response = client.get(url, headers=bookstack_headers(job.get("request_id")))

    if response.status_code in (400, 401, 403, 404):
        raise PermanentError(
            f"BookStack respondió HTTP {response.status_code} "
            f"para {job['entity_type']} {job['entity_id']}"
        )

    if response.status_code != 200:
        raise RuntimeError(f"BookStack respondió HTTP {response.status_code}")

    return response.content, extension, content_type


def process(job_id):
    job = get_job(job_id)

    if job is None:
        # El hash desapareció: se descarta de processing.
        get_redis().lrem(PROCESSING, 0, job_id)
        return

    # Continúa la traza del POST /exports que creó el job.
    with tracer.start_as_current_span(
        "export job",
        context=propagate.extract({"traceparent": job.get("traceparent", "")}),
        kind=trace.SpanKind.CONSUMER,
        attributes={
            "export.job_id": job_id,
            "export.format": job.get("format", ""),
            "export.attempt": job.get("attempts", 0),
            "request_id": job.get("request_id", ""),
        },
    ):
        run_job(job_id, job)


def run_job(job_id, job):
    request_id = job.get("request_id", "system")
    started = time.perf_counter()
    heartbeat = Heartbeat(job_id)
    heartbeat.start()

    log_json(
        "INFO",
        "Exportación iniciada",
        request_id,
        job_id=job_id,
        attempt=job["attempts"],
        worker=WORKER_ID
    )

    try:
        if SIMULATED_DELAY:
            time.sleep(SIMULATED_DELAY)

        content, extension, content_type = fetch_export(job)
        object_key = f"exports/{job_id}.{extension}"

        get_s3().put_object(
            Bucket=BUCKET,
            Key=object_key,
            Body=content,
            ContentType=content_type
        )

        owned = finish(
            job_id,
            status="done",
            object_key=object_key,
            size_bytes=len(content),
            completed_at=now_iso(),
            error=""
        )

        if owned:
            JOBS.labels("done").inc()
            log_json(
                "INFO",
                "Exportación completada",
                request_id,
                job_id=job_id,
                object_key=object_key,
                size_bytes=len(content),
                worker=WORKER_ID
            )
            events.publish(
                "export.completed",
                job,
                object_key=object_key,
                size_bytes=len(content),
                bucket=BUCKET
            )
        else:
            log_json("WARNING", "Lease perdido; otro worker rehará el job", request_id, job_id=job_id)

    except Exception as exc:
        retry = not isinstance(exc, PermanentError) and job["attempts"] < MAX_ATTEMPTS
        status = "queued" if retry else "failed"

        if finish(job_id, requeue=retry, status=status, error=str(exc), worker=""):
            if retry:
                REQUEUED.labels("error").inc()
            else:
                JOBS.labels("failed").inc()
                events.publish("export.failed", job, error=str(exc))

        log_json(
            "ERROR",
            "Exportación fallida",
            request_id,
            job_id=job_id,
            attempt=job["attempts"],
            retry=retry,
            error=str(exc),
            worker=WORKER_ID
        )

    finally:
        heartbeat.stop()
        JOB_DURATION.observe(time.perf_counter() - started)


def run():
    stopping = threading.Event()

    def handle_signal(signum, _frame):
        # docker stop: termina el job en curso y sale.
        # docker kill (SIGKILL) no llega aquí: lo cubre el lease.
        log_json("INFO", "Señal recibida, terminando", signal=signum, worker=WORKER_ID)
        stopping.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    start_http_server(METRICS_PORT)
    log_json("INFO", "Worker iniciado", worker=WORKER_ID, lease_ttl=LEASE_TTL)

    last_reap = 0.0

    while not stopping.is_set():
        try:
            if time.monotonic() - last_reap >= REAP_INTERVAL:
                reap_orphans()
                last_reap = time.monotonic()

            job_id = claim()

            if job_id:
                process(job_id)
            else:
                stopping.wait(POLL_INTERVAL)

        except Exception as exc:
            log_json("ERROR", "Error en el ciclo del worker", error=str(exc), worker=WORKER_ID)
            stopping.wait(2)

    log_json("INFO", "Worker detenido", worker=WORKER_ID)


if __name__ == "__main__":
    run()
