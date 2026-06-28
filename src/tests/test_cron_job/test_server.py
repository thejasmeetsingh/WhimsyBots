"""Tests for `src/cron_job/server.py` (Tier 6 — MCP tool implementations).

We test the four `@mcp.tool()`-decorated functions directly. The FastMCP
decorator leaves the underlying coroutine callable, so we can invoke them
as plain async functions in tests.

`get_session` is patched with an async context manager mock that yields
a fake session capable of running select/update/delete operations.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError


# ──────────────────────────────────────────────
# Async session mock factory
# ──────────────────────────────────────────────


def make_fake_session(
    *, scalars_result=None, fetchone_result=None, scalar_one_or_none=None
):
    """Build an AsyncMock that mimics SQLAlchemy AsyncSession:

    - `execute(stmt).scalars().all()` returns `scalars_result`
    - `execute(stmt).fetchone()` returns `fetchone_result`
    - `execute(stmt).scalar_one_or_none()` returns `scalar_one_or_none`
    - `add`, `delete`, `commit`, `refresh`, `rollback`, `close` are AsyncMocks
    """

    session = MagicMock(name="AsyncSession")
    session.add = MagicMock(name="add")
    session.delete = AsyncMock(name="delete")
    session.commit = AsyncMock(name="commit")
    session.refresh = AsyncMock(name="refresh")
    session.rollback = AsyncMock(name="rollback")
    session.close = AsyncMock(name="close")

    execute_result = MagicMock(name="ExecuteResult")

    # scalars() returns a ScalarsResult mock
    scalars_chain = MagicMock(name="scalars")
    scalars_chain.all = MagicMock(return_value=scalars_result or [])
    execute_result.scalars = MagicMock(return_value=scalars_chain)
    execute_result.fetchone = MagicMock(return_value=fetchone_result)
    execute_result.scalar_one_or_none = MagicMock(return_value=scalar_one_or_none)

    session.execute = AsyncMock(return_value=execute_result)
    return session


@asynccontextmanager
async def _fake_session_ctx(session):
    """Async context manager that yields the supplied session."""

    try:
        yield session
    finally:
        await session.close()


def _patch_session(session):
    """Replace `cron_job.db.get_session` with a context-manager that yields
    the supplied fake session."""

    @asynccontextmanager
    async def _ctx():
        try:
            yield session
        finally:
            await session.close()

    return patch("cron_job.server.get_session", _ctx)


# ──────────────────────────────────────────────
# list_cron_jobs
# ──────────────────────────────────────────────


BOT_ID = "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_list_cron_jobs_returns_error_on_invalid_uuid():
    from cron_job.server import list_cron_jobs

    out = await list_cron_jobs("not-a-uuid")
    assert "## Error" in out
    assert "bot_id" in out


@pytest.mark.asyncio
async def test_list_cron_jobs_returns_empty_when_no_jobs():
    from cron_job.server import list_cron_jobs

    session = make_fake_session(scalars_result=[])
    with _patch_session(session):
        out = await list_cron_jobs(BOT_ID)

    assert "No cron jobs found" in out
    assert "All" in out  # default status label


@pytest.mark.asyncio
async def test_list_cron_jobs_active_label_when_is_active_true():
    from cron_job.server import list_cron_jobs

    session = make_fake_session(scalars_result=[])
    with _patch_session(session):
        out = await list_cron_jobs(BOT_ID, is_active=True)
    assert "Active" in out


@pytest.mark.asyncio
async def test_list_cron_jobs_inactive_label_when_is_active_false():
    from cron_job.server import list_cron_jobs

    session = make_fake_session(scalars_result=[])
    with _patch_session(session):
        out = await list_cron_jobs(BOT_ID, is_active=False)
    assert "Inactive" in out


@pytest.mark.asyncio
async def test_list_cron_jobs_returns_db_error_message_on_sqlalchemy_error():
    from cron_job.server import list_cron_jobs

    session = MagicMock(name="AsyncSession")
    session.execute = AsyncMock(side_effect=SQLAlchemyError("db down"))
    session.close = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        try:
            yield session
        finally:
            await session.close()

    with patch("cron_job.server.get_session", _ctx):
        out = await list_cron_jobs(BOT_ID)

    assert "## Database Error" in out
    assert "db down" in out


# ──────────────────────────────────────────────
# create_cron_job
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_cron_job_returns_error_on_invalid_bot_uuid():
    from cron_job.server import create_cron_job

    out = await create_cron_job(
        bot_id="bad", name="x", description="y", cron_expression="0 9 * * *"
    )
    assert "## Error" in out


@pytest.mark.asyncio
async def test_create_cron_job_returns_validation_error_on_bad_cron():
    from cron_job.server import create_cron_job

    session = make_fake_session()
    with _patch_session(session):
        out = await create_cron_job(
            bot_id=BOT_ID,
            name="x",
            description="y",
            cron_expression="not-cron",
        )

    assert "## Validation Error" in out
    # We never reach the DB session.
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_cron_job_persists_and_returns_confirmation():
    from cron_job.server import create_cron_job

    captured = {}

    real_make_fake_session = make_fake_session

    async def refresh_hook(obj):
        # Simulate SQLAlchemy refreshing the job with its DB-assigned state.
        if not getattr(obj, "_refreshed", False):
            obj._refreshed = True
            captured["refreshed"] = True

    session = real_make_fake_session()
    session.refresh = AsyncMock(side_effect=refresh_hook)

    with _patch_session(session):
        out = await create_cron_job(
            bot_id=BOT_ID,
            name="Daily Report",
            description="Sends daily summary",
            cron_expression="0 9 * * *",
        )

    assert "## ✅ Cron Job Created" in out
    assert "Daily Report" in out
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_cron_job_db_error_message():
    from cron_job.server import create_cron_job

    session = MagicMock(name="AsyncSession")
    session.add = MagicMock()
    session.commit = AsyncMock(side_effect=SQLAlchemyError("insert failed"))
    session.refresh = AsyncMock()
    session.close = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        try:
            yield session
        except Exception:
            raise
        finally:
            await session.close()

    with patch("cron_job.server.get_session", _ctx):
        out = await create_cron_job(
            bot_id=BOT_ID,
            name="x",
            description="y",
            cron_expression="0 9 * * *",
        )

    assert "## Database Error" in out
    assert "insert failed" in out


# ──────────────────────────────────────────────
# update_cron_job
# ──────────────────────────────────────────────


JOB_ID = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_update_cron_job_invalid_job_uuid_returns_error():
    from cron_job.server import update_cron_job

    out = await update_cron_job(id="bad", bot_id=BOT_ID, name="x")
    assert "## Error" in out


@pytest.mark.asyncio
async def test_update_cron_job_invalid_bot_uuid_returns_error():
    from cron_job.server import update_cron_job

    out = await update_cron_job(id=JOB_ID, bot_id="bad", name="x")
    assert "## Error" in out


@pytest.mark.asyncio
async def test_update_cron_job_invalid_cron_returns_validation_error():
    from cron_job.server import update_cron_job

    # No DB session needed — validation fails first.
    out = await update_cron_job(id=JOB_ID, bot_id=BOT_ID, cron_expression="bad")
    assert "## Validation Error" in out


@pytest.mark.asyncio
async def test_update_cron_job_not_found_returns_markdown_message():
    from cron_job.server import update_cron_job

    session = make_fake_session(scalar_one_or_none=None)
    with _patch_session(session):
        out = await update_cron_job(id=JOB_ID, bot_id=BOT_ID, name="x")

    assert "## Not Found" in out
    assert JOB_ID in out
    assert BOT_ID in out


@pytest.mark.asyncio
async def test_update_cron_job_updates_and_returns_confirmation():
    from cron_job.server import update_cron_job

    job = SimpleNamespace(
        id=uuid.UUID(JOB_ID),
        bot_id=uuid.UUID(BOT_ID),
        name="Old Name",
        description="Old",
        cron_expression="0 9 * * *",
        next_run_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_active=True,
    )

    async def fake_refresh(obj):
        # Simulate SQLAlchemy refreshing the object with the new values
        # that the source just wrote via session.execute(update(...))
        obj.name = "New Name"
        obj.description = "New desc"

    # Two execute() calls: the initial select, and the update statement.
    session = make_fake_session(scalar_one_or_none=job)
    session.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=job)),
            MagicMock(),
        ]
    )
    session.refresh = AsyncMock(side_effect=fake_refresh)

    with _patch_session(session):
        out = await update_cron_job(
            id=JOB_ID, bot_id=BOT_ID, name="New Name", description="New desc"
        )

    assert "## ✅ Cron Job Updated" in out
    assert "New Name" in out
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_cron_job_db_error_message():
    from cron_job.server import update_cron_job

    session = MagicMock(name="AsyncSession")
    session.execute = AsyncMock(side_effect=SQLAlchemyError("update failed"))
    session.close = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        try:
            yield session
        finally:
            await session.close()

    with patch("cron_job.server.get_session", _ctx):
        out = await update_cron_job(id=JOB_ID, bot_id=BOT_ID, name="x")

    assert "## Database Error" in out


# ──────────────────────────────────────────────
# delete_cron_job
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_cron_job_invalid_job_uuid_returns_error():
    from cron_job.server import delete_cron_job

    out = await delete_cron_job(id="bad", bot_id=BOT_ID)
    assert "## Error" in out


@pytest.mark.asyncio
async def test_delete_cron_job_invalid_bot_uuid_returns_error():
    from cron_job.server import delete_cron_job

    out = await delete_cron_job(id=JOB_ID, bot_id="bad")
    assert "## Error" in out


@pytest.mark.asyncio
async def test_delete_cron_job_not_found_returns_markdown_message():
    from cron_job.server import delete_cron_job

    session = make_fake_session(scalar_one_or_none=None)
    with _patch_session(session):
        out = await delete_cron_job(id=JOB_ID, bot_id=BOT_ID)

    assert "## Not Found" in out
    session.delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_cron_job_deletes_and_returns_confirmation():
    from cron_job.server import delete_cron_job

    job = SimpleNamespace(
        id=uuid.UUID(JOB_ID),
        bot_id=uuid.UUID(BOT_ID),
        name="To Delete",
        description="bye",
        cron_expression="0 9 * * *",
    )
    session = make_fake_session(scalar_one_or_none=job)
    with _patch_session(session):
        out = await delete_cron_job(id=JOB_ID, bot_id=BOT_ID)

    assert "## ✅ Cron Job Deleted" in out
    assert "To Delete" in out
    session.delete.assert_awaited_once_with(job)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_cron_job_db_error_message():
    from cron_job.server import delete_cron_job

    session = MagicMock(name="AsyncSession")
    session.execute = AsyncMock(side_effect=SQLAlchemyError("delete failed"))
    session.close = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        try:
            yield session
        finally:
            await session.close()

    with patch("cron_job.server.get_session", _ctx):
        out = await delete_cron_job(id=JOB_ID, bot_id=BOT_ID)

    assert "## Database Error" in out
