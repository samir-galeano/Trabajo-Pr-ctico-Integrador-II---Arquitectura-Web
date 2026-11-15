import os
import psycopg2
from psycopg2.extras import RealDictCursor


def connect():
    return psycopg2.connect(
        host=os.environ["DATABASE_HOST"],
        port=os.getenv("DATABASE_PORT", "5432"),
        dbname=os.environ["DATABASE_NAME"],
        user=os.environ["DATABASE_USER"],
        password=os.environ["DATABASE_PASSWORD"],
        connect_timeout=5
    )


def initialize():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS indexed_pages (
                    page_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    updated_at TEXT,
                    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id UUID PRIMARY KEY,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)


def save_page(page):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO indexed_pages
                    (page_id, name, content, url, updated_at, indexed_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (page_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    content = EXCLUDED.content,
                    url = EXCLUDED.url,
                    updated_at = EXCLUDED.updated_at,
                    indexed_at = NOW()
            """, (
                page["id"], page["name"], page.get("content", ""),
                page.get("url", ""), page.get("updated_at")
            ))


def delete_page(page_id):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM indexed_pages WHERE page_id = %s",
                (page_id,)
            )


def search_pages(query, limit=20):
    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT page_id, name, content, url, updated_at
                FROM indexed_pages
                WHERE name ILIKE %s OR content ILIKE %s
                ORDER BY name
                LIMIT %s
            """, (f"%{query}%", f"%{query}%", limit))
            return cur.fetchall()


def apply_event(event_id, page_id, page=None, deleted=False):
    """Aplica el cambio y registra el evento en una sola transacción."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_events (event_id)
                VALUES (%s)
                ON CONFLICT DO NOTHING
                RETURNING event_id
            """, (event_id,))

            if cur.fetchone() is None:
                return False

            if deleted:
                cur.execute(
                    "DELETE FROM indexed_pages WHERE page_id = %s",
                    (page_id,)
                )
            else:
                cur.execute("""
                    INSERT INTO indexed_pages
                        (page_id, name, content, url, updated_at, indexed_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (page_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        content = EXCLUDED.content,
                        url = EXCLUDED.url,
                        updated_at = EXCLUDED.updated_at,
                        indexed_at = NOW()
                """, (
                    page["id"], page["name"], page.get("content", ""),
                    page.get("url", ""), page.get("updated_at")
                ))

            return True
