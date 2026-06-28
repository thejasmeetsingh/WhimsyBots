"""Tests for 'src/utils/scheduling.py'."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from croniter import croniter
from django.utils import timezone as django_timezone

from utils.scheduling import calculate_next_run_at

# ──────────────────────────────────────────────
# calculate_next_run_at
# ──────────────────────────────────────────────


def test_calculate_next_run_at_returns_future_datetime():
    # Daily at 09:00 — the next occurrence should be strictly in the future.
    before = django_timezone.now()
    next_run = calculate_next_run_at("0 9 * * *")
    after = django_timezone.now()

    assert before <= next_run
    assert next_run > after


def test_calculate_next_run_at_is_timezone_aware():
    # Django's USE_TZ=True is on, so the returned datetime must carry tzinfo.
    next_run = calculate_next_run_at("*/5 * * * *")
    assert next_run.tzinfo is not None
    assert next_run.tzinfo.utcoffset(next_run) is not None


def test_calculate_next_run_at_matches_croniter_reference():
    # Cross-check: the same expression computed via croniter directly
    # from 'now' should yield the exact same value (to the second).
    expression = "15 14 * * 1"  # every Monday at 14:15
    now = django_timezone.now()
    expected = croniter(expression, now).get_next(datetime)

    result = calculate_next_run_at(expression)

    # croniter operates on naive datetimes internally; normalize both
    # sides to UTC for comparison.
    assert result.astimezone(timezone.utc).replace(microsecond=0) == expected.replace(
        tzinfo=timezone.utc, microsecond=0
    )


def test_calculate_next_run_at_with_every_minute():
    # "* * * * *" ⇒ next run within ≤ 60s of now.
    next_run = calculate_next_run_at("* * * * *")
    delta_seconds = (next_run - django_timezone.now()).total_seconds()
    assert 0 < delta_seconds <= 60


def test_calculate_next_run_at_with_invalid_expression_raises():
    # croniter raises CroniterBadCronError (a subclass of KeyError)
    # for malformed expressions; assert it surfaces.
    from croniter import CroniterBadCronError

    with pytest.raises(CroniterBadCronError):
        calculate_next_run_at("not a cron")
