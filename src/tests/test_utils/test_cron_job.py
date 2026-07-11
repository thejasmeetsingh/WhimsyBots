"""Tests for 'src/utils/cron_job.py'.

These helpers are pure utility functions: next-run calculation,
UUID parsing, and markdown formatting for CronJob rows. We exercise
them without hitting any database.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from utils.cron_job import (
    calc_next_run,
    fmt_job,
    fmt_jobs,
    parse_uuid,
)

# ──────────────────────────────────────────────
# calc_next_run
# ──────────────────────────────────────────────


def test_calc_next_run_returns_timezone_aware_datetime():
    result = calc_next_run("0 9 * * *")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


def test_calc_next_run_is_in_the_future():
    """For any valid cron, the next run must be after 'now'."""
    now = datetime.now(timezone.utc)
    result = calc_next_run("0 9 * * *")
    assert result > now


def test_calc_next_run_matches_cron_schedule():
    """For "* * * * *" (every minute), the result should be at most ~60s away."""
    now = datetime.now(timezone.utc)
    result = calc_next_run("* * * * *")
    delta = (result - now).total_seconds()
    # Allow generous slack for slow CI, but it must be positive and < ~120s.
    assert 0 < delta < 120


# ──────────────────────────────────────────────
# parse_uuid
# ──────────────────────────────────────────────


def test_parse_uuid_returns_uuid_object_on_valid_input():
    valid = "12345678-1234-5678-1234-567812345678"
    result = parse_uuid(valid, "bot_id")
    assert isinstance(result, uuid.UUID)
    assert str(result) == valid


def test_parse_uuid_returns_error_string_on_invalid_input():
    bad = "not-a-uuid"
    result = parse_uuid(bad, "bot_id")
    assert isinstance(result, str)
    assert "## Error" in result
    assert "bot_id" in result
    assert bad in result


def test_parse_uuid_error_string_is_markdown():
    bad = "abc"
    result = parse_uuid(bad, "id")
    assert isinstance(result, str)
    assert result.startswith("## Error")


def test_parse_uuid_error_uses_supplied_label():
    """The error string must reference the label that was passed in."""
    result = parse_uuid("nope", "my_special_field")
    assert "my_special_field" in result


# ──────────────────────────────────────────────
# fmt_job
# ──────────────────────────────────────────────


def _job_dict(**overrides):
    base = {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "bot_id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "name": "Daily Report",
        "description": "Sends daily summary",
        "cron_expression": "0 9 * * *",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_fmt_job_includes_all_required_fields():
    job = _job_dict()
    md = fmt_job(job)

    assert str(job.id) in md
    assert str(job.bot_id) in md
    assert job.name in md
    assert job.description in md
    assert job.cron_expression in md


def test_fmt_job_uses_markdown_list_syntax():
    job = _job_dict()
    md = fmt_job(job)
    assert md.startswith("- **ID**")
    # Each nested entry indented under the list item.
    assert "  - **Bot ID**" in md
    assert "  - **Name**" in md
    assert "  - **Description**" in md
    assert "  - **Cron Expression**" in md


# ──────────────────────────────────────────────
# fmt_jobs
# ──────────────────────────────────────────────


def test_fmt_jobs_empty_list_returns_empty_markdown():
    md = fmt_jobs([], "All Cron Jobs")
    assert "## All Cron Jobs" in md
    assert "No cron jobs found" in md
    assert "**Total**" not in md  # total only included when there are jobs


def test_fmt_jobs_heading_and_total_present():
    jobs = [_job_dict(), _job_dict(name="Second")]
    md = fmt_jobs(jobs, "All Cron Jobs")
    assert "## All Cron Jobs" in md
    assert "**Total**: 2" in md


def test_fmt_jobs_each_job_rendered():
    jobs = [_job_dict(name="A"), _job_dict(name="B")]
    md = fmt_jobs(jobs, "h")
    assert "A" in md
    assert "B" in md


def test_fmt_jobs_preserves_order():
    """'fmt_jobs' should render jobs in the order they're passed in."""
    jobs = [
        _job_dict(name="First"),
        _job_dict(name="Second"),
        _job_dict(name="Third"),
    ]
    md = fmt_jobs(jobs, "h")
    assert md.index("First") < md.index("Second") < md.index("Third")
