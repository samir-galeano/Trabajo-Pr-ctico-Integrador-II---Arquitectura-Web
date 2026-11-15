"""Pruebas del cliente resiliente hacia BookStack (X5)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app import bookstack
from app.bookstack import BookStackError, CircuitBreaker, CircuitOpenError


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(bookstack, "breaker", CircuitBreaker(failure_threshold=3, reset_timeout=30, clock=fake))
    monkeypatch.setattr(bookstack, "_semaphore", None)
    monkeypatch.setattr(bookstack, "MAX_RETRIES", 2)
    return fake


def response(status, body=None):
    return MagicMock(status_code=status, json=MagicMock(return_value=body or {}))


def call(client, sleep=None):
    sleep = sleep or AsyncMock()
    return asyncio.run(bookstack.get_json(client, "/api/pages/1", sleep=sleep))


def test_success_returns_json(clock):
    client = MagicMock(get=AsyncMock(return_value=response(200, {"id": 1})))

    assert call(client) == {"id": 1}
    assert bookstack.breaker.state == "closed"


def test_timeout_is_retried_with_exponential_backoff(clock):
    client = MagicMock(get=AsyncMock(side_effect=[
        httpx.ReadTimeout("lento"),
        response(503),
        response(200, {"id": 1}),
    ]))
    sleep = AsyncMock()

    with patch.object(bookstack.random, "uniform", side_effect=lambda low, high: high):
        assert call(client, sleep) == {"id": 1}

    # Topes del jitter: base * 2^0 y base * 2^1
    assert [c.args[0] for c in sleep.call_args_list] == [
        bookstack.BACKOFF_BASE,
        bookstack.BACKOFF_BASE * 2,
    ]
    assert client.get.call_count == 3
    assert bookstack.breaker.failures == 0


def test_client_error_is_not_retried_and_keeps_circuit_closed(clock):
    client = MagicMock(get=AsyncMock(return_value=response(404)))

    with pytest.raises(BookStackError) as error:
        call(client)

    assert error.value.status_code == 404
    assert client.get.call_count == 1
    assert bookstack.breaker.state == "closed"


def test_circuit_opens_and_rejects_without_calling(clock):
    client = MagicMock(get=AsyncMock(side_effect=httpx.ConnectTimeout("caído")))

    with pytest.raises(httpx.ConnectTimeout):
        call(client)

    # 3 fallos seguidos (1 intento + 2 reintentos) abren el circuito.
    assert bookstack.breaker.state == "open"
    assert client.get.call_count == 3

    with pytest.raises(CircuitOpenError) as error:
        call(client)

    assert client.get.call_count == 3
    assert error.value.retry_after == pytest.approx(30)


def test_half_open_probe_closes_circuit_on_success(clock):
    failing = MagicMock(get=AsyncMock(side_effect=httpx.ConnectError("caído")))
    with pytest.raises(httpx.ConnectError):
        call(failing)
    assert bookstack.breaker.state == "open"

    clock.now += 31
    healthy = MagicMock(get=AsyncMock(return_value=response(200, {"ok": True})))

    assert call(healthy) == {"ok": True}
    assert bookstack.breaker.state == "closed"


def test_half_open_probe_failure_reopens_immediately(clock):
    failing = MagicMock(get=AsyncMock(side_effect=httpx.ConnectError("caído")))
    with pytest.raises(httpx.ConnectError):
        call(failing)

    clock.now += 31
    failing.get.reset_mock()

    with pytest.raises(httpx.ConnectError):
        call(failing)

    # Una sola llamada de prueba, sin reintentos, y el circuito vuelve a abrirse.
    assert failing.get.call_count == 1
    assert bookstack.breaker.state == "open"


def test_bulkhead_limits_concurrent_calls(clock, monkeypatch):
    monkeypatch.setattr(bookstack, "MAX_CONCURRENCY", 2)
    active = 0
    peak = 0

    async def slow_get(*_args, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return response(200, {})

    client = MagicMock(get=slow_get)

    async def many():
        await asyncio.gather(*(bookstack.get_json(client, "/api/pages/1") for _ in range(6)))

    asyncio.run(many())
    assert peak == 2


def test_sync_returns_503_when_circuit_open(clock):
    bookstack.breaker.opened_at = clock.now
    bookstack.breaker._set_state("open")

    with patch("app.main.initialize"), \
            patch("app.events.consume_events", AsyncMock()), \
            patch.object(bookstack, "new_client", return_value=MagicMock(
                __aenter__=AsyncMock(return_value=MagicMock()),
                __aexit__=AsyncMock(return_value=False),
            )):
        from app.main import app

        with TestClient(app) as client:
            result = client.post("/sync")

    assert result.status_code == 503
    assert int(result.headers["Retry-After"]) >= 30


def test_consumer_retries_when_nats_is_not_ready(monkeypatch):
    """En Swarm no hay depends_on: el consumidor reintenta en lugar de morir."""
    from app import events

    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionRefusedError("nats todavía no está")
        raise asyncio.CancelledError

    monkeypatch.setattr(events, "consume_once", flaky)
    monkeypatch.setattr(events.asyncio, "sleep", AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(events.consume_events())

    assert len(calls) == 3
