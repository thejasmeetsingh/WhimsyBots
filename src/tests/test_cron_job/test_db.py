"""Tests for 'src/cron_job/db.py'.

'_build_db_url()' is a pure function: it reads from 'os.environ' and
returns a URL string. 'get_session()' is an async context manager
that wraps an 'AsyncSession' and rolls back on 'SQLAlchemyError'.

The module's top-level imports call '_build_db_url()' once and bind
the result to a module-level engine — so we test '_build_db_url' in
isolation (with patched env vars) and 'get_session' via mocks for the
async machinery.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ──────────────────────────────────────────────
# _build_db_url
# ──────────────────────────────────────────────


def _build_db_url(**env):
    """Re-import the helper against a controlled environment.

    Importing the module triggers a top-level engine build, so we test
    '_build_db_url' by reloading the module with mocked env vars.
    """

    # Save and clear all DB_* env vars first.
    saved = {}
    for k in ("DB_DRIVER", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        saved[k] = os.environ.pop(k, None)
    try:
        for k, v in env.items():
            os.environ[k] = v
        # Force re-import so module-level `_build_db_url()` runs again.
        import cron_job.db as db_mod

        importlib.reload(db_mod)
        return db_mod._build_db_url()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


def test_build_db_url_uses_defaults_when_only_creds_set():
    url = _build_db_url(DB_NAME="x", DB_USER="u", DB_PASSWORD="p")
    # Default driver is postgresql+asyncpg.
    assert url.startswith("postgresql+asyncpg://")
    # Default host/port.
    assert "@localhost:5432/" in url
    # Credentials present.
    assert "u:p@" in url
    assert url.endswith("/x")


def test_build_db_url_honors_custom_driver_host_port():
    url = _build_db_url(
        DB_DRIVER="postgresql+asyncpg",
        DB_HOST="db.example.com",
        DB_PORT="6543",
        DB_NAME="prod",
        DB_USER="alice",
        DB_PASSWORD="secret",
    )
    assert url == "postgresql+asyncpg://alice:secret@db.example.com:6543/prod"


def test_build_db_url_raises_environment_error_when_name_missing():
    with pytest.raises(EnvironmentError) as exc_info:
        _build_db_url(DB_USER="u", DB_PASSWORD="p")
    assert "DB_NAME" in str(exc_info.value)


def test_build_db_url_raises_environment_error_when_user_missing():
    with pytest.raises(EnvironmentError):
        _build_db_url(DB_NAME="x", DB_PASSWORD="p")


def test_build_db_url_raises_environment_error_when_password_missing():
    with pytest.raises(EnvironmentError):
        _build_db_url(DB_NAME="x", DB_USER="u")


def test_build_db_url_error_message_mentions_all_required_vars():
    """When all three are missing, the error message should still
    mention the names of the variables."""

    with pytest.raises(EnvironmentError) as exc_info:
        _build_db_url()
    msg = str(exc_info.value)
    assert "DB_NAME" in msg
    assert "DB_USER" in msg
    assert "DB_PASSWORD" in msg


# ──────────────────────────────────────────────
# get_session — async context manager
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_session_yields_a_session():
    """'get_session' must yield an 'AsyncSession' and close on exit."""

    from cron_job.db import get_session

    fake_session = MagicMock(name="AsyncSession")
    fake_session.close = AsyncMock()

    # The source uses `async with AsyncSessionLocal() as session:`.
    # We mock 'AsyncSessionLocal' so its __aenter__ returns our fake_session
    # and __aexit__ calls close.
    enter = AsyncMock(return_value=fake_session)
    exit_ = AsyncMock(return_value=None)
    fake_session_local = MagicMock()
    fake_session_local.return_value.__aenter__ = enter
    fake_session_local.return_value.__aexit__ = exit_

    with patch("cron_job.db.AsyncSessionLocal", fake_session_local):
        async with get_session() as session:
            assert session is fake_session

    enter.assert_awaited_once()
    exit_.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_get_session_closes_session_even_on_success():
    """`finally: await session.close()` must run on the happy path."""

    from cron_job.db import get_session

    fake_session = MagicMock(name="AsyncSession")
    fake_session.close = AsyncMock()

    enter = AsyncMock(return_value=fake_session)
    exit_ = AsyncMock(return_value=None)
    fake_session_local = MagicMock()
    fake_session_local.return_value.__aenter__ = enter
    fake_session_local.return_value.__aexit__ = exit_

    with patch("cron_job.db.AsyncSessionLocal", fake_session_local):
        async with get_session() as _session:
            pass

    fake_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_session_rolls_back_on_sqlalchemy_error():
    """A 'SQLAlchemyError' inside the 'with' block must trigger a
    `session.rollback()` AND re-raise the error."""

    from sqlalchemy.exc import SQLAlchemyError

    from cron_job.db import get_session

    fake_session = MagicMock(name="AsyncSession")
    fake_session.rollback = AsyncMock()
    fake_session.close = AsyncMock()

    enter = AsyncMock(return_value=fake_session)
    exit_ = AsyncMock(return_value=None)
    fake_session_local = MagicMock()
    fake_session_local.return_value.__aenter__ = enter
    fake_session_local.return_value.__aexit__ = exit_

    with patch("cron_job.db.AsyncSessionLocal", fake_session_local):
        with pytest.raises(SQLAlchemyError):
            async with get_session() as _session:
                raise SQLAlchemyError("db boom")

    fake_session.rollback.assert_awaited_once()
    # And the session is still closed in 'finally'.
    fake_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_session_does_not_rollback_on_generic_exception():
    """Non-SQLAlchemy exceptions must propagate WITHOUT triggering rollback."""

    from cron_job.db import get_session

    fake_session = MagicMock(name="AsyncSession")
    fake_session.rollback = AsyncMock()
    fake_session.close = AsyncMock()

    enter = AsyncMock(return_value=fake_session)
    exit_ = AsyncMock(return_value=None)
    fake_session_local = MagicMock()
    fake_session_local.return_value.__aenter__ = enter
    fake_session_local.return_value.__aexit__ = exit_

    with patch("cron_job.db.AsyncSessionLocal", fake_session_local):
        with pytest.raises(RuntimeError):
            async with get_session() as _session:
                raise RuntimeError("not a db error")

    fake_session.rollback.assert_not_called()
    fake_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_session_closes_session_even_on_error():
    """`finally: await session.close()` must run regardless of error type."""

    from cron_job.db import get_session

    fake_session = MagicMock(name="AsyncSession")
    fake_session.rollback = AsyncMock()
    fake_session.close = AsyncMock()

    enter = AsyncMock(return_value=fake_session)
    exit_ = AsyncMock(return_value=None)
    fake_session_local = MagicMock()
    fake_session_local.return_value.__aenter__ = enter
    fake_session_local.return_value.__aexit__ = exit_

    with patch("cron_job.db.AsyncSessionLocal", fake_session_local):
        with pytest.raises(RuntimeError):
            async with get_session() as _session:
                raise RuntimeError("boom")

    fake_session.close.assert_awaited_once()
