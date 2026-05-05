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
import logging
from typing import Optional
from datetime import datetime, timezone

from sqlalchemy import select, update
from mcp.server.fastmcp import FastMCP
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


logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="cron-job-manager",
    instructions="""
You are connected to a Cron Job Manager MCP server that allows you to manage scheduled cron jobs stored in a PostgreSQL database. Each cron job belongs to a bot and defines when that bot should execute using standard cron expressions.

## Available Tools

### 1. `list_cron_jobs`
Retrieves cron jobs for a given bot. Use this when the user wants to see, browse, or check existing cron jobs.
- `bot_id` (required): The UUID of the bot.
- `is_active` (optional): Pass `true` for active jobs only, `false` for inactive jobs only. Omit to return all.

### 2. `create_cron_job`
Creates a new cron job for a bot. Use this when the user wants to schedule a new job.
- `bot_id` (required): The UUID of the bot.
- `name` (required): A short, human-readable label (max 100 characters).
- `cron_expression` (required): A valid standard 5-field cron expression (e.g. `0 9 * * 1` for every Monday at 9 AM).

### 3. `update_cron_job`
Partially updates an existing cron job. Only the fields you provide will be changed — unspecified fields remain untouched.
- `id` (required): UUID of the cron job to update.
- `bot_id` (required): UUID of the owning bot, used to scope the lookup.
- `name` (optional): New label for the job.
- `cron_expression` (optional): New cron schedule. Will be validated and `next_run_at` will be recalculated automatically.
- `is_active` (optional): Pass `true` to enable or `false` to disable the job.

### 4. `delete_cron_job`
Permanently deletes a cron job. This action is irreversible.
- `id` (required): UUID of the cron job to delete.
- `bot_id` (required): UUID of the owning bot, used to scope the deletion.

## Response Format
All tools return a Markdown-formatted response containing:
- **ID** — UUID of the cron job
- **Bot ID** — UUID of the owning bot
- **Name** — Label of the job
- **Cron Expression** — The schedule in cron format

Errors (invalid UUIDs, invalid cron expressions, not found, or database failures) are also returned as Markdown with a clear heading indicating the error type.

## Important Rules
- Never guess or fabricate UUIDs. Always use exact values provided by the user.
- When creating or updating a job, validate that the cron expression follows the standard 5-field format: `minute hour day-of-month month day-of-week`.
- Prefer `update_cron_job` with `is_active: false` over deletion when the user wants to temporarily pause a job.
- If the user asks to "disable" or "pause" a job, use `update_cron_job` with `is_active: false` — do not delete it.
""",
)


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

    logger.info(
        {
            "tool": "list_cron_jobs",
            "params": {"bot_id": bot_id, "is_active": None},
        }
    )

    bot_uuid = _parse_uuid(bot_id, "bot_id")
    if isinstance(bot_uuid, str):
        logger.error("Invalid 'bot_uuid' format")
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

    logger.info(
        {
            "tool": "create_cron_job",
            "params": {
                "bot_id": bot_id,
                "name": name,
                "cron_expression": cron_expression,
            },
        }
    )

    bot_uuid = _parse_uuid(bot_id, "bot_id")
    if isinstance(bot_uuid, str):
        logger.error("Invalid 'bot_uuid' format")
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

    logger.info(
        {
            "tool": "update_cron_job",
            "params": {
                "id": id,
                "bot_id": bot_id,
                "name": name,
                "cron_expression": cron_expression,
                "is_active": is_active,
            },
        }
    )

    job_uuid = _parse_uuid(id, "id")
    if isinstance(job_uuid, str):
        logger.error("Invalid 'job_uuid' format")
        return job_uuid

    bot_uuid = _parse_uuid(bot_id, "bot_id")
    if isinstance(bot_uuid, str):
        logger.error("Invalid 'bot_uuid' format")
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

    logger.info(
        {
            "tool": "update_cron_job",
            "params": {
                "id": id,
                "bot_id": bot_id,
            },
        }
    )

    job_uuid = _parse_uuid(id, "id")
    if isinstance(job_uuid, str):
        logger.error("Invalid 'job_uuid' format")
        return job_uuid

    bot_uuid = _parse_uuid(bot_id, "bot_id")
    if isinstance(bot_uuid, str):
        logger.error("Invalid 'bot_uuid' format")
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
