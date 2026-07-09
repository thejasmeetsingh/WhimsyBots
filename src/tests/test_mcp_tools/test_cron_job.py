"""Tests for 'src/mcp_tools/cron_job.py'.

'CronJob' is a Django model so we exercise the in-process tools by
patching 'CronJob.objects' — the source code talks to the ORM only
through that QuerySet, so we never need a live database.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mcp_tools import cron_job

# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _make_job(**overrides) -> SimpleNamespace:
    """Build a fake CronJob SimpleNamespace — id/bot are UUIDs as strings
    (matching what the real ORM returns), everything else defaults to
    something reasonable.
    """
    base = {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "bot_id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "name": "Daily Report",
        "description": "Sends daily summary",
        "cron_expression": "0 9 * * *",
        "next_run_at": "2099-01-01T00:00:00Z",
        "is_active": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _QuerySet:
    """Minimal QuerySet stand-in supporting filter/update/delete chains.

    Records every call so tests can assert on the kwargs that were
    passed to the ORM. The actual returned data is controlled by
    'data' and 'update_count' constructor parameters.
    """

    def __init__(self, data=None, update_count=1, delete_count=1):
        self._data = list(data or [])
        self._update_count = update_count
        self._delete_count = delete_count
        self.calls = []

    def filter(self, **kwargs):
        self.calls.append(("filter", kwargs))
        return self

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return self._update_count

    def delete(self):
        self.calls.append(("delete",))
        return self._delete_count, {}

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


# ──────────────────────────────────────────────
# list
# ──────────────────────────────────────────────


def test_list_returns_empty_markdown_when_no_jobs():
    qs = _QuerySet(data=[])
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        out = cron_job.list(bot_id="bot-1")
    assert "No cron jobs found" in out
    assert "All" in out  # default status label


def test_list_shows_active_label_when_is_active_true():
    qs = _QuerySet(data=[])
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        out = cron_job.list(bot_id="bot-1", is_active=True)
    assert "Active" in out


def test_list_shows_inactive_label_when_is_active_false():
    qs = _QuerySet(data=[])
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        out = cron_job.list(bot_id="bot-1", is_active=False)
    assert "Inactive" in out


def test_list_renders_jobs_with_field_details():
    job = _make_job(name="My Job")
    qs = _QuerySet(data=[job])
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        out = cron_job.list(bot_id="bot-1")
    assert "My Job" in out
    assert "0 9 * * *" in out
    assert "Total" in out  # total included when there are jobs


def test_list_filters_by_active_status_when_is_active_provided():
    """When 'is_active' is provided, the QuerySet must be filtered by it."""
    qs = _QuerySet(data=[])
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        cron_job.list(bot_id="bot-1", is_active=True)
    # The filter chain must include is_active=True.
    assert any(call[0] == "filter" and call[1].get("is_active") is True for call in qs.calls)


def test_list_propagates_db_exceptions():
    """The 'list' tool does not catch DB exceptions — callers handle
    the failure upstream. (The 'create' tool DOES catch and return
    an error string; that path is covered separately.)
    """
    with patch(
        "mcp_tools.cron_job.CronJob.objects.filter",
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(RuntimeError):
            cron_job.list(bot_id="bot-1")


# ──────────────────────────────────────────────
# create
# ──────────────────────────────────────────────


def test_create_persists_job_with_computed_next_run_at():
    captured = {}
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.create.side_effect = lambda **kwargs: captured.update(kwargs)
        out = cron_job.create(
            bot_id="bot-1",
            name="daily",
            description="runs every day",
            cron_expression="0 9 * * *",
        )

    assert "Cron Job Created" in out
    assert captured["bot_id"] == "bot-1"
    assert captured["name"] == "daily"
    assert captured["description"] == "runs every day"
    assert captured["cron_expression"] == "0 9 * * *"
    assert captured["is_active"] is True
    # next_run_at is computed from the cron expression and is not None.
    assert captured["next_run_at"] is not None


def test_create_returns_db_error_message_on_exception():
    with patch(
        "mcp_tools.cron_job.CronJob.objects.create",
        side_effect=RuntimeError("db down"),
    ):
        out = cron_job.create(
            bot_id="bot-1",
            name="x",
            description="y",
            cron_expression="0 9 * * *",
        )
    assert "## Database Error" in out
    assert "db down" in out


# ──────────────────────────────────────────────
# update
# ──────────────────────────────────────────────


def test_update_partially_updates_job_name():
    qs = _QuerySet(update_count=1)
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        out = cron_job.update(id=str(uuid.uuid4()), bot_id="bot-1", name="new")

    assert "Cron Job Updated" in out
    # The update() call only carries the changed field.
    update_calls = [c for c in qs.calls if c[0] == "update"]
    assert update_calls[0][1] == {"name": "new"}


def test_update_cron_expression_recomputes_next_run_at():
    qs = _QuerySet(update_count=1)
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        cron_job.update(
            id=str(uuid.uuid4()),
            bot_id="bot-1",
            cron_expression="0 10 * * *",
        )

    update_calls = [c for c in qs.calls if c[0] == "update"]
    # Both cron_expression and next_run_at are written.
    assert update_calls[0][1]["cron_expression"] == "0 10 * * *"
    assert "next_run_at" in update_calls[0][1]


def test_update_returns_error_on_invalid_uuid():
    out = cron_job.update(id="not-a-uuid", bot_id="bot-1", name="x")
    assert "## Error" in out


def test_update_returns_not_found_for_missing_job():
    qs = _QuerySet(update_count=0)
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        out = cron_job.update(id=str(uuid.uuid4()), bot_id="bot-1", name="x")
    # 'CRON_DOES_NOT_EXISTS' template uses 'No Cron Job exists ...'
    assert "No Cron Job exists" in out


def test_update_propagates_db_exceptions():
    """The 'update' tool does not catch DB exceptions — the exception
    bubbles up to the caller. (Cron UUID parsing errors are caught
    separately by 'parse_uuid'.)
    """
    with patch(
        "mcp_tools.cron_job.CronJob.objects.filter",
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(RuntimeError):
            cron_job.update(id=str(uuid.uuid4()), bot_id="bot-1", name="x")


# ──────────────────────────────────────────────
# delete
# ──────────────────────────────────────────────


def test_delete_removes_job():
    qs = _QuerySet(delete_count=1)
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        out = cron_job.delete(id=str(uuid.uuid4()), bot_id="bot-1")
    assert "Cron Job Deleted" in out


def test_delete_returns_error_on_invalid_uuid():
    out = cron_job.delete(id="not-a-uuid", bot_id="bot-1")
    assert "## Error" in out


def test_delete_returns_not_found_for_missing_job():
    qs = _QuerySet(delete_count=0)
    with patch("mcp_tools.cron_job.CronJob.objects") as objs:
        objs.filter.return_value = qs
        out = cron_job.delete(id=str(uuid.uuid4()), bot_id="bot-1")
    assert "No Cron Job exists" in out


def test_delete_propagates_db_exceptions():
    """The 'delete' tool does not catch DB exceptions."""
    with patch(
        "mcp_tools.cron_job.CronJob.objects.filter",
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(RuntimeError):
            cron_job.delete(id=str(uuid.uuid4()), bot_id="bot-1")
