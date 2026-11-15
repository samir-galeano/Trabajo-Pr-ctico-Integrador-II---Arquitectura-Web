import fakeredis
import pytest

from app import common


@pytest.fixture(autouse=True)
def fake_redis():
    """Redis en memoria para todas las pruebas (sin servidor real)."""
    common._redis = fakeredis.FakeRedis(decode_responses=True)
    yield common._redis
    common._redis = None
