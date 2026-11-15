import asyncio
import re
import json
import time
import uuid
import logging
from html.parser import HTMLParser
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from app import auth
from app import bookstack, credentials
from app.bookstack import BOOKSTACK_URL, BookStackError, CircuitOpenError
from app.database import initialize, save_page, delete_page, search_pages, connect

SERVICE_NAME = "search-svc"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(SERVICE_NAME)

REQUESTS = Counter(
    "http_requests_total", "Total de solicitudes HTTP",
    ["service", "method", "path", "status"]
)
LATENCY = Histogram(
    "http_request_duration_seconds", "Duración de solicitudes HTTP",
    ["service", "method", "path"]
)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        return " ".join(" ".join(self.parts).split())


def extract_text(html):
    parser = TextExtractor()
    parser.feed(html or "")
    return parser.text()


@asynccontextmanager
async def lifespan(app):
    initialize()

    from app.events import consume_events

    consumer_task = asyncio.create_task(consume_events())
    app.state.consumer_task = consumer_task

    try:
        yield
    finally:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Search Service",
    version="1.1.0",
    lifespan=lifespan
)


# Bearer (BearerAuth de los contratos). Se registra antes que el middleware
# de métricas para quedar por dentro: los 401 también se miden y se loguean.
app.middleware("http")(auth.middleware)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start = time.time()
    status = 500

    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        elapsed = time.time() - start
        REQUESTS.labels(
            SERVICE_NAME, request.method, request.url.path, str(status)
        ).inc()
        LATENCY.labels(
            SERVICE_NAME, request.method, request.url.path
        ).observe(elapsed)

        logger.info(json.dumps({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": "INFO",
            "service": SERVICE_NAME,
            "request_id": request_id,
            "message": f"{request.method} {request.url.path} {status} {elapsed:.4f}s"
        }))


@app.get("/healthz")
async def health():
    return {"status": "alive", "service": SERVICE_NAME}


@app.get("/readyz")
async def ready():
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        if not credentials.has_token():
            raise RuntimeError("BookStack credentials are not configured")
        # El circuito abierto no saca al servicio de rotación: la búsqueda
        # sigue respondiendo desde el índice local (degradación elegante).
        return {
            "status": "ready",
            "service": SERVICE_NAME,
            "bookstack_circuit": bookstack.breaker.state
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Dependency unavailable: {exc}")


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def get_optional(client, path, request_id):
    """Datos complementarios: si BookStack responde 4xx se omiten."""
    try:
        return await bookstack.get_json(client, path, request_id=request_id)
    except BookStackError:
        return None


@app.post("/sync")
async def sync_pages(request: Request):
    """Recorre todas las páginas de BookStack y reconstruye/actualiza el índice."""
    request_id = request.headers.get("X-Request-ID")
    indexed = 0
    page_number = 1
    count = 100
    book_slugs = {}

    try:
        async with bookstack.new_client() as client:
            while True:
                payload = await bookstack.get_json(
                    client,
                    "/api/pages",
                    params={"count": count, "page": page_number},
                    request_id=request_id
                )
                pages = payload.get("data", [])

                if not pages:
                    break

                for item in pages:
                    page_id = item["id"]
                    detail = item

                    if not item.get("html"):
                        detail = await get_optional(
                            client, f"/api/pages/{page_id}", request_id
                        ) or item

                    content = extract_text(detail.get("html", ""))

                    book_id = detail.get("book_id") or item.get("book_id")
                    if book_id and book_id not in book_slugs:
                        book = await get_optional(
                            client, f"/api/books/{book_id}", request_id
                        )
                        book_slugs[book_id] = (book or {}).get("slug", "")

                    detail["book_slug"] = book_slugs.get(book_id, "")

                    await asyncio.to_thread(save_page, {
                        "id": page_id,
                        "name": detail.get("name", item.get("name", "")),
                        "content": content,
                        "url": (
                            detail.get("url")
                            or item.get("url")
                            or f"{BOOKSTACK_URL}/books/"
                               f"{detail.get('book_slug', '')}/page/"
                               f"{detail.get('slug', item.get('slug', ''))}"
                        ),
                        "updated_at": detail.get("updated_at")
                    })
                    indexed += 1

                if len(pages) < count or indexed >= payload.get("total", indexed):
                    break

                page_number += 1

        return {"status": "completed", "indexed": indexed}

    except CircuitOpenError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": str(int(exc.retry_after) + 1)}
        )
    except Exception as exc:
        logger.exception("Index synchronization failed")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/search")
def search(q: str, limit: int = 20):
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    results = search_pages(q.strip(), min(max(limit, 1), 100))
    output = []

    for page in results:
        text = page["content"] or ""
        match = re.search(re.escape(q.strip()), text, re.IGNORECASE)

        if match:
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 150)
            snippet = text[start:end]
            snippet = re.sub(
                re.escape(q.strip()),
                lambda m: f"<mark>{m.group(0)}</mark>",
                snippet,
                flags=re.IGNORECASE
            )
        else:
            snippet = page["name"]

        output.append({
            "id": page["page_id"],
            "name": page["name"],
            "url": page["url"],
            "highlight": snippet
        })

    return {"query": q, "total": len(output), "results": output}


@app.delete("/pages/{page_id}")
def remove_page(page_id: int):
    delete_page(page_id)
    return {"status": "deleted", "page_id": page_id}
