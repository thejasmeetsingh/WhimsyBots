import fakeredis
import pytest


# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────
@pytest.fixture(scope="session")
def django_db_setup():
    """
    Use the in-memory SQLite DB defined in test settings.
    No teardown needed – :memory: disappears automatically.
    """
    pass


# ──────────────────────────────────────────────
# Redis / fakeredis
# ──────────────────────────────────────────────
@pytest.fixture
def fake_redis():
    """
    A fresh fakeredis instance per test.
    Inject this wherever your code would normally receive a Redis client.
    """
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    yield client
    client.flushall()
    client.close()


@pytest.fixture(scope="session")
def fake_redis_server():
    """
    A shared fakeredis server for the whole test session.
    Useful when you need multiple clients pointing at the same server.
    """
    return fakeredis.FakeServer()


# ──────────────────────────────────────────────
# Async
# ──────────────────────────────────────────────
# pytest-asyncio is already set to auto mode in pytest.ini,
# so no extra fixtures are needed. Just mark async tests with
# @pytest.mark.asyncio (or rely on auto-detection).
