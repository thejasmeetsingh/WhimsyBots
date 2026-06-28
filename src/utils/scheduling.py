"""Cron-related scheduling helpers."""

from __future__ import annotations

from croniter import croniter
from django.utils import timezone


def calculate_next_run_at(cron_expression: str) -> timezone.datetime:
    """Return the next run timestamp for a cron expression.

    Uses 'croniter.croniter' to compute the next occurrence of
    'cron_expression' after 'django.utils.timezone.now'. The
    result is timezone-aware (Django's default 'USE_TZ=True').

    Args:
        cron_expression: Standard cron expression (5 fields).

    Returns:
        The next run time as a timezone-aware 'datetime'.

    Example:
        >>> next_run = calculate_next_run_at("0 9 * * *")
    """
    current_dt = timezone.now()
    return croniter(cron_expression, current_dt).get_next(timezone.datetime)
