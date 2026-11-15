"""Persistencia de audit-svc en su propio PostgreSQL (database per service).

La idempotencia la garantiza la base: event_id es PRIMARY KEY y el INSERT
usa ON CONFLICT DO NOTHING, así un evento redelivered no duplica registros
aunque haya varias réplicas del consumidor.
"""
import os
import threading
from contextlib import contextmanager

from psycopg2.extras import Json, RealDictCursor
from psycopg2.pool import ThreadedConnectionPool


_pool = None
_pool_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_records (
    event_id     TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    entity_type  TEXT NOT NULL DEFAULT '',
    entity_id    TEXT NOT NULL DEFAULT '',
    entity_name  TEXT NOT NULL DEFAULT '',
    actor        TEXT NOT NULL DEFAULT 'unknown',
    occurred_at  TIMESTAMPTZ,
    request_id   TEXT NOT NULL DEFAULT '-',
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS audit_records_entity_idx
    ON audit_records (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS audit_records_occurred_idx
    ON audit_records (occurred_at);
"""

COLUMNS = (
    "event_id",
    "type",
    "entity_type",
    "entity_id",
    "entity_name",
    "actor",
    "occurred_at",
    "request_id",
    "payload",
    "received_at",
)


def get_pool():
    global _pool

    with _pool_lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(
                1,
                int(os.getenv("DATABASE_POOL_MAX", "5")),
                host=os.environ["DATABASE_HOST"],
                port=os.getenv("DATABASE_PORT", "5432"),
                dbname=os.environ["DATABASE_NAME"],
                user=os.environ["DATABASE_USER"],
                password=os.environ["DATABASE_PASSWORD"],
                connect_timeout=5
            )

    return _pool


@contextmanager
def connection():
    pool = get_pool()
    conn = pool.getconn()
    broken = False

    try:
        with conn:
            yield conn
    except Exception:
        broken = conn.closed != 0
        raise
    finally:
        pool.putconn(conn, close=broken)


def initialize():
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)


def ping():
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")


def insert_record(record):
    """Inserta el registro. Devuelve False si el event_id ya existía."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_records
                    (event_id, type, entity_type, entity_id, entity_name,
                     actor, occurred_at, request_id, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    record["event_id"],
                    record["type"],
                    record["entity_type"],
                    record["entity_id"],
                    record["entity_name"],
                    record["actor"],
                    record["occurred_at"],
                    record["request_id"],
                    Json(record["payload"]),
                )
            )
            return cur.rowcount == 1


def get_record(event_id):
    rows = query_records(event_id=event_id, limit=1)
    return rows[0] if rows else None


def query_records(event_id=None,entity_type=None, entity_id=None, start=None, end=None, limit=100):
    conditions = []
    params = []

    for column, value, operator in (
        ("event_id", event_id, "="),
        ("entity_type", entity_type, "="),
        ("entity_id", entity_id, "="),
        ("occurred_at", start, ">="),
        ("occurred_at", end, "<="),
    ):
        if value is not None:
            conditions.append(f"{column} {operator} %s")
            params.append(value)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT {', '.join(COLUMNS)} FROM audit_records {where} "
                "ORDER BY occurred_at DESC NULLS LAST, received_at DESC LIMIT %s",
                (*params, limit)
            )
            rows = cur.fetchall()

    for row in rows:
        for column in ("occurred_at", "received_at"):
            if row[column] is not None:
                row[column] = row[column].isoformat()

    return rows
