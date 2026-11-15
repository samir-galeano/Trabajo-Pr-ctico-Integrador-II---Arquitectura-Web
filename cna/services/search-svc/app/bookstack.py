"""Cliente resiliente hacia la API REST de BookStack (experimento X5).

Cada llamada pasa por tres protecciones, de afuera hacia adentro:

  1. Interruptor de circuito: tras BOOKSTACK_CB_FAILURES fallos seguidos se
     abre y rechaza las llamadas al instante (CircuitOpenError) durante
     BOOKSTACK_CB_RESET segundos; luego deja pasar una llamada de prueba
     (half-open) y, según el resultado, se cierra o se vuelve a abrir.
  2. Reintentos con backoff exponencial y jitter ante errores de red,
     timeouts, 429 y 5xx. Los 4xx no se reintentan ni abren el circuito.
  3. Bulkhead: un semáforo limita las llamadas simultáneas, así una
     BookStack lenta no acapara el event loop ni las conexiones.
"""
import asyncio
import os
import random
import time

import httpx
from prometheus_client import Counter, Gauge

from app import credentials

BOOKSTACK_URL = os.getenv("BOOKSTACK_BASE_URL", "https://bookstack.local").rstrip("/")
VERIFY_TLS = os.getenv("BOOKSTACK_VERIFY_TLS", "false").lower() == "true"

CONNECT_TIMEOUT = float(os.getenv("BOOKSTACK_CONNECT_TIMEOUT", "3"))
READ_TIMEOUT = float(os.getenv("BOOKSTACK_READ_TIMEOUT", "5"))
MAX_RETRIES = int(os.getenv("BOOKSTACK_MAX_RETRIES", "3"))
BACKOFF_BASE = float(os.getenv("BOOKSTACK_BACKOFF_BASE", "0.5"))
BACKOFF_MAX = float(os.getenv("BOOKSTACK_BACKOFF_MAX", "8"))
CB_FAILURES = int(os.getenv("BOOKSTACK_CB_FAILURES", "5"))
CB_RESET = float(os.getenv("BOOKSTACK_CB_RESET", "30"))
MAX_CONCURRENCY = int(os.getenv("BOOKSTACK_MAX_CONCURRENCY", "4"))

CALLS = Counter(
    "bookstack_client_requests_total",
    "Llamadas a la API de BookStack por resultado",
    ["outcome"]
)
RETRIES = Counter(
    "bookstack_client_retries_total",
    "Reintentos hacia la API de BookStack"
)
CIRCUIT_STATE = Gauge(
    "bookstack_circuit_state",
    "Estado del interruptor hacia BookStack (0=closed, 1=half_open, 2=open)"
)

STATE_VALUES = {"closed": 0, "half_open": 1, "open": 2}


class CircuitOpenError(Exception):
    """El circuito está abierto: no se llama a BookStack."""

    def __init__(self, retry_after):
        super().__init__(f"Circuito abierto hacia BookStack; reintentar en {retry_after:.1f}s")
        self.retry_after = retry_after


class BookStackError(Exception):
    """BookStack respondió con un error que no se arregla reintentando (4xx)."""

    def __init__(self, status_code, url):
        super().__init__(f"BookStack respondió HTTP {status_code} para {url}")
        self.status_code = status_code


class CircuitBreaker:
    def __init__(self, failure_threshold=CB_FAILURES, reset_timeout=CB_RESET, clock=time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.clock = clock
        self.failures = 0
        self.opened_at = None
        self.probe_in_flight = False
        self._set_state("closed")

    def _set_state(self, state):
        self.state = state
        CIRCUIT_STATE.set(STATE_VALUES[state])

    def retry_after(self):
        if self.state != "open":
            return 0.0
        return max(0.0, self.reset_timeout - (self.clock() - self.opened_at))

    def before_call(self):
        """Lanza CircuitOpenError si la llamada no debe hacerse."""
        if self.state == "open":
            if self.retry_after() > 0:
                raise CircuitOpenError(self.retry_after())
            self._set_state("half_open")
            self.probe_in_flight = False

        if self.state == "half_open":
            # Solo una llamada de prueba a la vez.
            if self.probe_in_flight:
                raise CircuitOpenError(1.0)
            self.probe_in_flight = True

    def record_success(self):
        self.failures = 0
        self.probe_in_flight = False
        self._set_state("closed")

    def record_failure(self):
        self.failures += 1
        self.probe_in_flight = False

        if self.state == "half_open" or self.failures >= self.failure_threshold:
            self.opened_at = self.clock()
            self._set_state("open")


breaker = CircuitBreaker()
_semaphore = None


def semaphore():
    # Se crea perezosamente para quedar ligado al event loop en uso.
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    return _semaphore


def headers(request_id=None):
    result = {
        "Authorization": credentials.authorization_header(),
        "Accept": "application/json",
        "Host": "bookstack.local",
    }
    if request_id:
        result["X-Request-ID"] = request_id
    return result


def new_client():
    if not credentials.has_token():
        raise RuntimeError("Credenciales de BookStack no configuradas")

    return httpx.AsyncClient(
        verify=VERIFY_TLS,
        timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
        follow_redirects=False,
    )


def backoff(attempt):
    """Backoff exponencial con jitter completo: U(0, min(max, base * 2^n))."""
    return random.uniform(0, min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt)))


async def get_json(client, path, params=None, request_id=None, sleep=asyncio.sleep):
    """GET a la API de BookStack con circuito, reintentos y bulkhead."""
    url = f"{BOOKSTACK_URL}{path}"
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            breaker.before_call()
        except CircuitOpenError:
            CALLS.labels("rejected").inc()
            raise

        try:
            async with semaphore():
                response = await client.get(url, params=params, headers=headers(request_id))
        except httpx.TransportError as exc:
            # Incluye timeouts y errores de conexión.
            last_error = exc
            outcome = "timeout" if isinstance(exc, httpx.TimeoutException) else "network_error"
        else:
            if response.status_code < 400:
                breaker.record_success()
                CALLS.labels("success").inc()
                return response.json()

            if response.status_code != 429 and response.status_code < 500:
                # El servidor respondió bien; el error es del pedido.
                breaker.record_success()
                CALLS.labels("client_error").inc()
                raise BookStackError(response.status_code, url)

            last_error = BookStackError(response.status_code, url)
            outcome = "server_error"

        breaker.record_failure()
        CALLS.labels(outcome).inc()

        if attempt < MAX_RETRIES and breaker.state == "closed":
            RETRIES.inc()
            await sleep(backoff(attempt))
        else:
            break

    raise last_error
