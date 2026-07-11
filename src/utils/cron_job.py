"""Helper utilities for cron job management."""

import uuid
from datetime import datetime

from croniter import croniter
from django.utils import timezone

from app.models import CronJob


def calc_next_run(expr: str) -> datetime:
    """Calculate the next execution time for a cron expression.

    Args:
        expr (str): Valid cron expression.

    Returns:
        datetime: The next occurrence of the schedule in UTC.
    """
    return croniter(expr, timezone.now()).get_next(datetime)


def parse_uuid(value: str, label: str) -> uuid.UUID | str:
    """Return a UUID object or an error string.

    Args:
        value (str): The string to parse as a UUID.
        label (str): The name of the field (for error reporting).

    Returns:
        uuid.UUID: Parsed UUID if successful.
        str: Markdown error message if parsing fails.
    """
    try:
        return uuid.UUID(value)
    except ValueError:
        return f"## Error\n\n`{label}` is not a valid UUID: `{value}`"


def fmt_job(job: CronJob) -> str:
    """Format a single CronJob object as a Markdown list item.

    Args:
        job (CronJob): The SQLAlchemy model instance to format.

    Returns:
        str: Markdown formatted string with job details.
    """
    return (
        f"- **ID**: `{str(job.id)}`\n"
        f"  - **Bot ID**: `{str(job.bot_id)}`\n"
        f"  - **Name**: {job.name}\n"
        f"  - **Description**: {job.description}\n"
        f"  - **Cron Expression**: `{job.cron_expression}`"
    )


def fmt_jobs(jobs: list[CronJob], heading: str) -> str:
    """Format a list of CronJob objects as a Markdown report.

    Args:
        jobs (list[CronJob]): List of jobs to format.
        heading (str): The title for the report.

    Returns:
        str: Markdown formatted string containing the total count and list of jobs.
    """
    if not jobs:
        return f"## {heading}\n\n_No cron jobs found._"
    lines = [f"## {heading}\n", f"**Total**: {len(jobs)}\n"]
    lines += [fmt_job(j) for j in jobs]
    return "\n".join(lines)
