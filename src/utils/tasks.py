"""Shared helpers for the Celery tasks in 'app.tasks'.

This module hosts the small, task-layer-specific helpers that are
extracted out of 'app.tasks' to keep the task definitions focused
on the orchestration steps (fetch inputs -> call services -> log ->
retry). Helpers here are pure or close to pure: they take inputs,
return outputs, and never call 'self.retry' — that stays in the
task bodies.

What lives here
---------------

— Module-level configuration constants: retry base interval, max
  retries, default Celery queue, allowed Telegram update types.
— Object lookups: 'get_ollama_cfg', 'get_bot_obj',
  'get_cron_obj', 'get_msg_obj', plus the underlying
  '_get_by_id_or_log' generic.
— Embedding-dispatch helpers: 'generate_message_embedding',
  'generate_cron_job_embedding', 'generate_mcp_embedding',
  '_should_skip_message_embedding'.
— Error handling: 'build_error_description' and
  'log_task_failure' for the common "log + retry" pattern.
— Persistence: 'create_log' thin wrapper around the 'app.models.Log' ORM.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.choices import MessageRole
from app.models import Bot, CronJob, Log, MCPServer, Message, Ollama
from clients.telegram import TelegramRateLimitError
from managers import OllamaConfigManager
from services.embedding import EmbeddingService
from strings import (
    GENERAL_TASK_ERROR,
    NO_OLLAMA,
    OBJ_NOT_FOUND,
    TELEGRAM_RATE_LIMIT_ERROR,
)

# 'process_inbound_message' lives in 'app.tasks' which itself
# imports from this module, so we can't import it eagerly. Instead we
# look up the configured Celery app and dispatch by task name — this
# also makes the helper easier to unit-test (you can monkey-patch the
# 'celery_app' attribute in tests).
from whimsybots.celery import task as celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level configuration constants
# ---------------------------------------------------------------------------

# Number of seconds used as the base unit for exponential backoff
# between task retries on unexpected failures.
RETRY_BASE_SECONDS: int = 60

# Maximum number of retry attempts configured for every task in
# 'app.tasks'.
MAX_RETRIES: int = 3

# Identifier of the Celery queue used for non-webhook follow-up work
# (embedding generation, conversation summary, pattern regeneration).
DEFAULT_QUEUE: str = "default"

# Telegram update types we want to receive on the webhook.
TELEGRAM_ALLOWED_UPDATES: list[str] = ["message"]


# ---------------------------------------------------------------------------
# Generic object lookups
# ---------------------------------------------------------------------------


def get_ollama_cfg() -> Optional[Ollama]:
    """Return the active 'Ollama' configuration, or None.

    The configuration is cached inside 'OllamaConfigManager' to
    avoid hitting the database on every task invocation. When no
    configuration exists yet (e.g. the operator has not finished the
    first-time setup) a warning is logged and None is returned so
    callers can short-circuit cleanly.

    Returns:
        The configured 'Ollama' instance, or None if missing.
    """
    ollama = OllamaConfigManager.get_ollama_config()
    if ollama is not None:
        return ollama

    logger.warning(NO_OLLAMA)
    return None


def _get_by_id_or_log(
    model: type,
    obj_id: str,
    obj_label: str,
) -> Optional[Any]:
    """Fetch a model instance by primary key, logging if it is missing.

    Centralises the "look up an object, return None if not found, log
    otherwise" pattern repeated for 'Bot', 'CronJob' and
    'Message' lookups. The returned object is untyped to keep
    the helper generic — callers narrow the type at the call site.

    Args:
        model: The Django model class to query.
        obj_id: UUID (as 'str') of the object to look up.
        obj_label: Human-readable object kind (e.g. 'bot') used
            in the "not found" log message.

    Returns:
        The fetched model instance, or None if it does not exist.
    """
    try:
        return model.objects.get(id=obj_id)
    except model.DoesNotExist:
        logger.error(OBJ_NOT_FOUND.format(obj_type=obj_label, obj_id=obj_id))
        return None


def get_bot_obj(bot_id: str) -> Optional[Bot]:
    """Return the 'Bot' identified by 'bot_id', or None.

    Args:
        bot_id: UUID (as 'str') of the bot to fetch.

    Returns:
        The 'Bot' instance, or None if it does not exist.
    """
    return _get_by_id_or_log(Bot, bot_id, obj_label="bot")


def get_cron_obj(cron_job_id: str) -> Optional[CronJob]:
    """Return the 'CronJob' identified by 'cron_job_id', or None.

    Args:
        cron_job_id: UUID (as 'str') of the cron job to fetch.

    Returns:
        The 'CronJob' instance, or None if it does not exist.
    """
    return _get_by_id_or_log(CronJob, cron_job_id, obj_label="cron job")


def get_msg_obj(msg_id: str) -> Optional[Message]:
    """Return the 'Message' identified by 'msg_id', or None.

    Args:
        msg_id: UUID (as 'str') of the message to fetch.

    Returns:
        The 'Message' instance, or None if it does not exist.
    """
    return _get_by_id_or_log(Message, msg_id, obj_label="message")


def create_log(bot: Bot, is_success: bool, desc: str) -> None:
    """Persist a 'Log' entry describing the outcome of a task step.

    Args:
        bot: Bot the log entry should be associated with.
        is_success: True for successful runs, False for failures.
        desc: Human-readable description of what happened. Pre-formatted
            via the 'strings.*' templates by the caller.
    """
    Log.objects.create(bot=bot, is_success=is_success, description=desc)


# ---------------------------------------------------------------------------
# Embedding generation helpers
# ---------------------------------------------------------------------------


def _should_skip_message_embedding(message: Message) -> bool:
    """Decide whether 'message' is eligible for embedding generation.

    A message is skipped if it is not a user message or already has a
    stored embedding vector. Both conditions are normal — the caller
    will simply move on.

    Args:
        message: The 'Message' to inspect.

    Returns:
        True if the message should be skipped, False otherwise.
    """
    if message.role != MessageRole.USER.value[0]:
        logger.debug("Skipping embedding for non-user message %s", message.id)
        return True

    if message.content_embedding is not None:
        logger.debug("Embedding already exists for message %s, skipping", message.id)
        return True

    return False


def generate_message_embedding(ollama: Ollama, msg_id: str) -> Optional[Bot]:
    """Generate and persist the embedding for a single user message.

    The function is a no-op (returning None) for non-user messages
    or when the message already carries an embedding. On success, the
    inbound message processing task is re-queued so downstream context
    assembly can leverage the freshly stored vector.

    Args:
        ollama: Active 'Ollama' configuration (used to build the 'EmbeddingService').
        msg_id: UUID (as 'str') of the message to embed.

    Returns:
        The owning 'Bot' on success (handy for the caller when
        it needs to log a failure), otherwise None.
    """
    message = get_msg_obj(msg_id=msg_id)
    if message is None:
        return None

    if _should_skip_message_embedding(message):
        return None

    EmbeddingService(bot=message.bot, ollama=ollama).save_message_embedding(message=message)

    # Queue for processing on default worker — context assembly will
    # use the embedding we just persisted. Dispatched by name to avoid
    # a circular import between this module and 'app.tasks'.
    celery_app.send_task(
        "app.tasks.process_inbound_message",
        queue=DEFAULT_QUEUE,
        kwargs={
            "bot_id": str(message.bot_id),
            "msg_id": str(message.id),
        },
    )

    return message.bot


def generate_cron_job_embedding(ollama: Ollama, cron_job_id: str) -> Optional[Bot]:
    """Generate and persist the embedding for a cron job's schedule.

    Args:
        ollama: Active 'Ollama' configuration.
        cron_job_id: UUID (as 'str') of the cron job to embed.

    Returns:
        The owning 'Bot' on success, otherwise None.
    """
    cron_job = get_cron_obj(cron_job_id=cron_job_id)
    if cron_job is None:
        return None

    EmbeddingService(bot=cron_job.bot, ollama=ollama).save_cron_job_embedding(cron_job=cron_job)

    return cron_job.bot


def generate_mcp_embedding(ollama: Ollama, mcp_server_id: str) -> Optional[Bot]:
    """Generate and persist the embedding for a single MCP server.

    Unlike the message / cron helpers this function does not log an
    error when the server is missing — MCP server entries are
    sometimes deleted between the dispatch and the actual run, and
    we want to silently skip them rather than spam the error log.

    Args:
        ollama: Active 'Ollama' configuration.
        mcp_server_id: UUID (as 'str') of the MCP server to embed.

    Returns:
        The owning 'Bot' on success, otherwise None.
    """
    try:
        mcp_server = MCPServer.objects.get(id=mcp_server_id)
    except MCPServer.DoesNotExist:
        return None

    EmbeddingService(bot=mcp_server.bot, ollama=ollama).save_mcp_embedding(mcp_server=mcp_server)

    return mcp_server.bot


# ---------------------------------------------------------------------------
# Shared error-handling utilities for tasks
# ---------------------------------------------------------------------------


def build_error_description(
    *,
    func_name: str,
    bot: Optional[Bot],
    error: BaseException,
) -> tuple[str, int]:
    """Build the log description + retry delay for a failed task invocation.

    Telegram rate-limit errors get a dedicated message and use the
    server-supplied 'retry_after' value. Every other exception falls
    back to the generic template and a deterministic exponential
    backoff ('2**retries' minutes).

    Args:
        func_name: Name of the task that failed (used in the log template).
        bot: The bot being processed when the error occurred. May be
            None for tasks that have not yet resolved a bot.
        error: The exception that was raised.

    Returns:
        A 2-tuple '(description, retry_minutes)' ready to be passed to
        'create_log' and 'self.retry'.
    """
    bot_name = bot.name if bot is not None else "unknown"

    if isinstance(error, TelegramRateLimitError):
        description = TELEGRAM_RATE_LIMIT_ERROR.format(
            func_name=func_name, bot_name=bot_name, error=str(error)
        )
        return description, error.retry_after

    description = GENERAL_TASK_ERROR.format(
        func_name=func_name, bot_name=bot_name, error=str(error)
    )
    # Caller multiplies this by RETRY_BASE_SECONDS — keep it in minutes.
    return description, 1


def log_task_failure(
    bot: Optional[Bot],
    description: str,
    log_message: str,
) -> None:
    """Persist an error log entry and emit the matching logger call.

    Args:
        bot: The bot being processed when the error occurred. May be
            None for tasks that have not yet resolved a bot — in
            that case the log entry is skipped but the logger call
            still runs.
        description: Pre-formatted description string for the log row.
        log_message: Short message forwarded to 'logger.error' (the
            full traceback is attached via 'exc_info=True' by callers).
    """
    if bot is not None:
        create_log(bot=bot, is_success=False, desc=description)
    logger.error(log_message, exc_info=True)
