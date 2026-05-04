"""
Cron job management tools for MCP integration.

Features:
- List cron jobs for a bot with optional filtering by active status
- Create new cron jobs with cron expressions
- Partially update existing cron jobs (name, expression, active status)
- Delete cron jobs
- Cron expression validation
- Automatic next run time calculation

MCP Integration:
These tools are registered with the MCP server via @mcp.tool() decorators.
The LLM can call these tools to help users manage their bot scheduling.

Example MCP Usage:
    # User asks: "Set up a daily report at 9 AM"
    # LLM calls: await list_cron_jobs(bot_id="...")
    # LLM calls: await create_cron_job(bot_id="...", name="Daily Report", cron_expression="0 9 * * *")
"""

import uuid
from typing import Optional
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from cron_job.db import get_session
from cron_job.helpers import (
    _calc_next_run,
    _fmt_job,
    _fmt_jobs,
    _parse_uuid,
    _validate_cron,
)
from cron_job.models import CronJob
from cron_job.server import server as mcp


@mcp.tool()
async def list_cron_jobs(bot_id: str, is_active: Optional[bool] = None) -> str:
    """
    List cron jobs for a specific bot.

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
            | xxx-xxx-xxx | xxx-xxx-xxx | Daily Report | 0 9 * * * | 2026-05-05 09:00:00+00:00 | True |

    Raises:
        ValueError: If bot_id is not a valid UUID
        SQLAlchemyError: If database query fails
    """

    bot_uuid = _parse_uuid(bot_id, "bot_id")
    if isinstance(bot_uuid, str):
        return bot_uuid

    try:
        async with get_session() as session:
            stmt = select(CronJob).where(CronJob.bot_id == bot_uuid)
            if is_active is not None:
                stmt = stmt.where(CronJob.is_active == is_active)
            stmt = stmt.order_by(CronJob.created_at.desc())

            result = await session.execute(stmt)
            jobs = result.scalars().all()

        status_label = {True: "Active", False: "Inactive", None: "All"}[is_active]
        return _fmt_jobs(jobs, f"Cron Jobs — Bot `{bot_id}` ({status_label})")
    except SQLAlchemyError as e:
        return f"## Database Error\n\n```\n{e}\n```"


@mcp.tool()
async def create_cron_job(bot_id: str, name: str, cron_expression: str) -> str:
    """
    Create a new cron job for a bot.

    Creates a new scheduled job that will be picked up by the cron_job_poller
    Celery task. The job uses standard cron expressions for scheduling.

    Args:
        bot_id (str): UUID of the bot this job belongs to
        name (str): Human-readable label for the job (max 100 chars)
            Examples: "Daily Report", "Weekly Newsletter", "Hourly Check"
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
            | xxx-xxx-xxx | xxx-xxx-xxx | Daily Report | 0 9 * * * | 2026-05-05 09:00:00+00:00 | True |

    Raises:
        ValueError: If cron_expression is invalid
        SQLAlchemyError: If database operation fails
    """

    bot_uuid = _parse_uuid(bot_id, "bot_id")
    if isinstance(bot_uuid, str):
        return bot_uuid

    try:
        _validate_cron(cron_expression)
    except ValueError as e:
        return f"## Validation Error\n\n{e}"

    try:
        async with get_session() as session:
            job = CronJob(
                id=uuid.uuid4(),
                bot_id=bot_uuid,
                name=name,
                cron_expression=cron_expression,
                next_run_at=_calc_next_run(cron_expression),
                is_active=True,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

        return "## ✅ Cron Job Created\n\n" + _fmt_job(job)
    except SQLAlchemyError as e:
        return f"## Database Error\n\n```\n{e}\n```"


@mcp.tool()
async def update_cron_job(
    id: str,
    bot_id: str,
    name: Optional[str] = None,
    cron_expression: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> str:
    """
    Partially update an existing cron job.

    Updates one or more fields of an existing cron job. Only the provided
    fields will be updated; other fields remain unchanged.

    Args:
        id (str): UUID of the cron job to update (required)
        bot_id (str): UUID of the owning bot — used to scope the lookup (required)
        name (str | None): New name for the job (optional)
            Examples: "Daily Report", "Weekly Newsletter"
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
            | xxx-xxx-xxx | xxx-xxx-xxx | Daily Report | 0 9 * * * | 2026-05-05 09:00:00+00:00 | True |

            Or error message if job not found or validation failed:
            ## Not Found

            No cron job with ID `xxx-xxx-xxx` found for bot `xxx-xxx-xxx`.

    Raises:
        ValueError: If cron_expression is invalid
        SQLAlchemyError: If database operation fails
    """

    job_uuid = _parse_uuid(id, "id")
    if isinstance(job_uuid, str):
        return job_uuid

    bot_uuid = _parse_uuid(bot_id, "bot_id")
    if isinstance(bot_uuid, str):
        return bot_uuid

    if cron_expression is not None:
        try:
            _validate_cron(cron_expression)
        except ValueError as e:
            return f"## Validation Error\n\n{e}"

    # Build only the fields that were actually provided
    changes: dict = {"updated_at": datetime.now(timezone.utc)}
    if name is not None:
        changes["name"] = name
    if is_active is not None:
        changes["is_active"] = is_active
    if cron_expression is not None:
        changes["cron_expression"] = cron_expression
        changes["next_run_at"] = _calc_next_run(cron_expression)

    try:
        async with get_session() as session:
            # Fetch first so we can return the updated row and detect not-found
            result = await session.execute(
                select(CronJob).where(
                    CronJob.id == job_uuid,
                    CronJob.bot_id == bot_uuid,
                )
            )
            job = result.scalar_one_or_none()

            if job is None:
                return f"## Not Found\n\nNo cron job with ID `{id}` found for bot `{bot_id}`."

            await session.execute(
                update(CronJob)
                .where(CronJob.id == job_uuid, CronJob.bot_id == bot_uuid)
                .values(**changes)
            )
            await session.commit()
            await session.refresh(job)

        return "## ✅ Cron Job Updated\n\n" + _fmt_job(job)
    except SQLAlchemyError as e:
        return f"## Database Error\n\n```\n{e}\n```"


@mcp.tool()
async def delete_cron_job(id: str, bot_id: str) -> str:
    """
    Delete a cron job.

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
            | xxx-xxx-xxx | xxx-xxx-xxx | Daily Report | 0 9 * * * | 2026-05-05 09:00:00+00:00 | True |

            Or error message if job not found:
            ## Not Found

            No cron job with ID `xxx-xxx-xxx` found for bot `xxx-xxx-xxx`.

    Raises:
        SQLAlchemyError: If database operation fails
    """

    job_uuid = _parse_uuid(id, "id")
    if isinstance(job_uuid, str):
        return job_uuid

    bot_uuid = _parse_uuid(bot_id, "bot_id")
    if isinstance(bot_uuid, str):
        return bot_uuid

    try:
        async with get_session() as session:
            result = await session.execute(
                select(CronJob).where(
                    CronJob.id == job_uuid,
                    CronJob.bot_id == bot_uuid,
                )
            )
            job = result.scalar_one_or_none()

            if job is None:
                return f"## Not Found\n\nNo cron job with ID `{id}` found for bot `{bot_id}`."

            snapshot_md = _fmt_job(job)  # capture before delete
            await session.delete(job)
            await session.commit()

        return "## ✅ Cron Job Deleted\n\n" + snapshot_md
    except SQLAlchemyError as e:
        return f"## Database Error\n\n```\n{e}\n```"
