"""Root conftest for the project's pytest suite.

Provides the shared fixtures every test module relies on: an
in-memory SQLite-backed Django DB, a per-test fakeredis instance, and
a session-scoped fakeredis server for tests that need multiple
clients pointing at the same in-memory store.
"""

import fakeredis
import pytest


# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────
@pytest.fixture(scope="session")
def django_db_setup():
    """Use the in-memory SQLite DB defined in test settings.

    No teardown needed – :memory: disappears automatically.
    """
    pass


# ──────────────────────────────────────────────
# Redis / fakeredis
# ──────────────────────────────────────────────
@pytest.fixture
def fake_redis():
    """Yield a fresh fakeredis instance per test.

    Inject this wherever your code would normally receive a Redis client.
    """
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    yield client
    client.flushall()
    client.close()


@pytest.fixture(scope="session")
def fake_redis_server():
    """Yield a shared fakeredis server for the whole test session.

    Useful when you need multiple clients pointing at the same server.
    """
    return fakeredis.FakeServer()


# ──────────────────────────────────────────────
# Async
# ──────────────────────────────────────────────
# pytest-asyncio is already set to auto mode in pytest.ini,
# so no extra fixtures are needed. Just mark async tests with
# @pytest.mark.asyncio (or rely on auto-detection).
