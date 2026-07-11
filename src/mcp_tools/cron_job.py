"""Cron job management tools for MCP integration.

Features:
- List cron jobs for a bot with optional filtering by active status
- Create new cron jobs with cron expressions
- Partially update existing cron jobs (name, expression, active status)
- Delete cron jobs
- Cron expression validation
- Automatic next run time calculation

Example Usage:
    # User asks: "Set up a daily report at 9 AM"
    # LLM calls: list_cron_jobs(bot_id="...")
    # LLM calls: create_cron_job(bot_id="...", name="Daily Report",
    #     cron_expression="0 9 * * *")
"""

from typing import Any, Optional

from app.models import CronJob
from strings import CRON_ACTION_SUCCESS, CRON_DOES_NOT_EXISTS
from utils.cron_job import (
    calc_next_run,
    fmt_jobs,
    parse_uuid,
)


def list(bot_id: str, is_active: Optional[bool] = None) -> str:
    """List cron jobs for a specific bot.

    Retrieves all cron jobs associated with a bot, optionally filtered by
    active status. Returns a formatted markdown table showing job details.

    Args:
        bot_id (str): UUID of the bot whose cron jobs to retrieve
        is_active (bool | None): Filter by active status
            - True: Only active jobs
            - False: Only inactive jobs
            - None: All jobs regardless of status

    Returns:
        str: Markdown-formatted list of cron jobs with columns:
            - id: Job UUID
            - bot_id: Owning bot UUID
            - name: Job name
            - cron_expression: Cron schedule
            - next_run_at: Next scheduled execution
            - is_active: Active status

            Format:
            ## Cron Jobs — Bot `xxx-xxx-xxx` (Active)

            | id | bot_id | name | cron_expression | next_run_at | is_active |
            |---|---|---|---|---|---|
            | xxx-... | xxx-... | Daily Report | 0 9 * * * | 2026-05-05 09:00:00+00:00 | True |
    """
    jobs = CronJob.objects.filter(bot_id=bot_id)
    if is_active is not None:
        jobs = jobs.filter(is_active=is_active)

    status_label = {True: "Active", False: "Inactive", None: "All"}[is_active]
    return fmt_jobs(jobs, f"Cron Jobs — Bot `{bot_id}` ({status_label})")


def create(bot_id: str, name: str, description: str, cron_expression: str) -> str:
    """Create a new cron job for a bot.

    Creates a new scheduled job that will be picked up by the cron_job_poller
    Celery task. The job uses standard cron expressions for scheduling.

    Args:
        bot_id (str): UUID of the bot this job belongs to
        name (str): Human-readable label for the job (max 100 chars)
            Examples: "Daily Report", "Weekly Newsletter", "Hourly Check"
        description (str): Detailed description of what the job does (max 5000 chars)
            Examples: "Generates and sends daily performance report", "Fetches latest data from API"
        cron_expression (str): Standard 5-field cron expression
            Format: minute hour day month weekday
            Examples:
                - "0 9 * * *" → Every day at 9:00 AM
                - "0 10 * * MON-FRI" → Every weekday at 10:00 AM
                - "*/15 * * * *" → Every 15 minutes
                - "0 0 * * *" → Every day at midnight

    Returns:
        str: Markdown confirmation with the new cron job's details:
            ## ✅ Cron Job Created

            | id | bot_id | name | cron_expression | next_run_at | is_active |
            |---|---|---|---|---|---|
            | xxx-... | xxx-... | Daily Report | 0 9 * * * | 2026-05-05 09:00:00+00:00 | True |
    """
    try:
        CronJob.objects.create(
            bot_id=bot_id,
            name=name,
            description=description,
            cron_expression=cron_expression,
            next_run_at=calc_next_run(cron_expression),
            is_active=True,
        )

        return CRON_ACTION_SUCCESS.format(action="Created")
    except Exception as e:
        return f"## Database Error\n\n```\n{e}\n```"


def update(
    id: str,
    bot_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    cron_expression: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> str:
    """Partially update an existing cron job.

    Updates one or more fields of an existing cron job. Only the provided
    fields will be updated; other fields remain unchanged.

    Args:
        id (str): UUID of the cron job to update (required)
        bot_id (str): UUID of the owning bot — used to scope the lookup (required)
        name (str | None): New name for the job (optional)
            Examples: "Daily Report", "Weekly Newsletter"
        description (str | None): New description for the job (optional)
            Examples: "Generates and sends daily performance report", "Fetches latest data from API"
        cron_expression (str | None): New cron expression (optional)
            Must be valid cron syntax if provided
            Examples: "0 9 * * *", "*/15 * * * *"
        is_active (bool | None): Enable or disable the job (optional)
            - True: Enable the job
            - False: Disable the job
            - None: Keep current status

    Returns:
        str: Markdown confirmation with the updated cron job's details:
            ## ✅ Cron Job Updated

            | id | bot_id | name | cron_expression | next_run_at | is_active |
            |---|---|---|---|---|---|
            | xxx-... | xxx-... | Daily Report | 0 9 * * * | 2026-05-05 09:00:00+00:00 | True |

            Or error message if job not found or validation failed:
            ## Not Found

            No cron job with ID `xxx-xxx-xxx` found for bot `xxx-xxx-xxx`.
    """
    job_uuid = parse_uuid(id, "id")
    if isinstance(job_uuid, str):
        return job_uuid

    # Build only the fields that were actually provided
    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = name
    if description is not None:
        changes["description"] = description
    if is_active is not None:
        changes["is_active"] = is_active
    if cron_expression is not None:
        changes["cron_expression"] = cron_expression
        changes["next_run_at"] = calc_next_run(cron_expression)

    count = CronJob.objects.filter(id=job_uuid, bot_id=bot_id).update(**changes)
    if not count:
        return CRON_DOES_NOT_EXISTS.format(id=id)

    return CRON_ACTION_SUCCESS.format(action="Updated")


def delete(id: str, bot_id: str) -> str:
    """Delete a cron job.

    Permanently removes a cron job from the database. The job will no longer
    be scheduled by the cron_job_poller task.

    Args:
        id (str): UUID of the cron job to delete
        bot_id (str): UUID of the owning bot — used to scope the deletion

    Returns:
        str: Markdown confirmation of deletion:
            ## ✅ Cron Job Deleted

            | id | bot_id | name | cron_expression | next_run_at | is_active |
            |---|---|---|---|---|---|
            | xxx-... | xxx-... | Daily Report | 0 9 * * * | 2026-05-05 09:00:00+00:00 | True |

            Or error message if job not found:
            ## Not Found

            No cron job with ID `xxx-xxx-xxx` found for bot `xxx-xxx-xxx`.
    """
    job_uuid = parse_uuid(id, "id")
    if isinstance(job_uuid, str):
        return job_uuid

    count, _ = CronJob.objects.filter(id=job_uuid, bot_id=bot_id).delete()
    if not count:
        return CRON_DOES_NOT_EXISTS.format(id=id)

    return CRON_ACTION_SUCCESS.format(action="Deleted")
