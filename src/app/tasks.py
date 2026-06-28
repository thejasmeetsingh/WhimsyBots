"""Celery task definitions for asynchronous job processing.

This module is the single entry point for all background work triggered by
the Django web layer (views, webhooks) or by scheduled beats. Tasks are
grouped into two categories:

— Embedding generation tasks: produce and persist vector embeddings
  for user messages, cron job schedules, and MCP server descriptions.
— Conversation / message processing tasks: Orchestrate the full
  bot response pipeline (intent classification, tool calling, summary
  management, pattern observation).

Conventions used throughout this module:

— All tasks are bound ('@celery.task(bind=True)') so they can call
  'self.retry(...)' with explicit backoff strategies.
— Every task fetches the active Ollama configuration via
  'utils.tasks.get_ollama_cfg' first; if no configuration is
  present the task exits silently with a warning instead of raising.
— Telegram rate-limit errors are caught explicitly and the task is
  retried after the server-supplied 'retry_after' interval.
— All other unexpected errors are retried with exponential backoff
  ('RETRY_BASE_SECONDS * 2**retries' seconds) and recorded in the
  Log table for operator visibility.

Shared helpers (object lookups, error formatting, embedding dispatch)
live in 'utils.tasks' so this module can stay focused on
orchestration logic.

The constants 'utils.tasks.MAX_RETRIES', 'utils.tasks.RETRY_BASE_SECONDS',
'utils.tasks.DEFAULT_QUEUE' and 'utils.tasks.TELEGRAM_ALLOWED_UPDATES'
are re-exported from 'utils.tasks' to keep a single source of truth.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from app.choices import MessageRole
from app.models import Bot, CronJob, MCPServer, Message, Ollama
from clients import OllamaClient
from managers import TelegramClientManager
from services.bot_processor import BotMessageProcessor
from services.conversation_summary import ConversationSummaryService
from services.embedding import EmbeddingService
from services.observed_patterns import ObservedPatternsService
from services.telegram_update_handler import TelegramUpdateHandler
from strings import (
    BOT_CRON_JOB_SUCCESS,
    BOT_MSG_SUCCESS,
    GENERAL_TASK_ERROR,
    GENERATE_EMBEDDING_FAILED,
    INVALID_BOT_TOKEN,
    NO_BOT_RESPONSE,
    UPDATE_OBSERVED_PATTERNS_SUCCESS,
    WEBHOOK_SETUP_SUCCESS,
)
from utils.crypto import get_token_hash
from utils.scheduling import calculate_next_run_at
from utils.tasks import (
    DEFAULT_QUEUE,
    MAX_RETRIES,
    RETRY_BASE_SECONDS,
    TELEGRAM_ALLOWED_UPDATES,
    build_error_description,
    create_log,
    generate_cron_job_embedding,
    generate_mcp_embedding,
    generate_message_embedding,
    get_bot_obj,
    get_cron_obj,
    get_msg_obj,
    get_ollama_cfg,
    log_task_failure,
)
from whimsybots.celery import task as celery

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Incoming-update tasks
# ---------------------------------------------------------------------------


@celery.task(bind=True, max_retries=MAX_RETRIES)
def telegram_msg_handler(self, bot_token: str, update: dict) -> None:
    """Dispatch a single Telegram update to the appropriate bot.

    The bot is resolved by hashing the incoming token (the hash is stored
    at 'Bot.telegram_bot_token_hash' so we never touch the encrypted
    plaintext during lookup). If the bot does not exist, the update is
    silently dropped with an error log — most likely a stale webhook
    pointing at a deleted bot. Any other exception is retried after 60s.

    Args:
        bot_token: The plaintext Telegram bot token extracted from the
            inbound webhook URL. Used only to compute the lookup hash.
        update: The full Telegram update payload as a dict.

    Returns:
        None. The work is performed by TelegramUpdateHandler
        which enqueues the follow-up tasks.
    """
    try:
        bot_token_hash = get_token_hash(bot_token)
        bot = Bot.objects.get(telegram_bot_token_hash=bot_token_hash)
    except Bot.DoesNotExist:
        logger.error(INVALID_BOT_TOKEN.format(bot_token=bot_token))
        return

    try:
        TelegramUpdateHandler.handle_update(bot, update)
    except Exception as exc:
        logger.error("Telegram update handler failed", exc_info=True)
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS)


# ---------------------------------------------------------------------------
# Cron job scheduling tasks
# ---------------------------------------------------------------------------


def _find_due_cron_jobs(current_dt: datetime) -> list[CronJob]:
    """Return active cron jobs whose 'next_run_at' has just elapsed.

    Args:
        current_dt: Reference timestamp used to decide which jobs are due.
            Typically 'django.utils.timezone.now'.

    Returns:
        A list of 'CronJob' instances ready to be dispatched.
    """
    return list(
        CronJob.objects.filter(
            is_active=True,
            bot__is_active=True,
            bot__telegram_chat_id__isnull=False,
            next_run_at__lte=current_dt,
        )
    )


def _refresh_cron_job_embedding_if_stale(job: CronJob, ollama: Ollama) -> None:
    """Regenerate a cron job's schedule embedding if it is missing or stale.

    A job's embedding is considered stale when 'schedule_embedding' is
    absent, or when it was last updated before the job itself (e.g. the
    operator edited the schedule since the last embedding run).

    Args:
        job: The CronJob to inspect / refresh.
        ollama: Active Ollama configuration.
    """
    embedding = job.schedule_embedding
    updated_at = job.schedule_embedding_updated_at

    if embedding is not None and not (updated_at and updated_at < job.updated_at):
        return

    EmbeddingService(bot=job.bot, ollama=ollama).save_cron_job_embedding(cron_job=job)


def _dispatch_cron_job(job: CronJob) -> None:
    """Enqueue 'process_cron_job' to run at the job's scheduled time.

    Args:
        job: The CronJob to enqueue. Its 'next_run_at' is used
            as the Celery 'eta' so the worker picks it up exactly on time.
    """
    process_cron_job.apply_async(
        queue=DEFAULT_QUEUE,
        kwargs={"job_id": str(job.id)},
        eta=job.next_run_at,
    )


@celery.task(bind=True, max_retries=MAX_RETRIES)
def cron_job_poller(self) -> None:
    """Enqueue every cron job whose 'next_run_at' has just elapsed.

    Runs periodically (driven by Celery beat / cron). For each due job it:

    1. Refreshes the schedule embedding if it is missing or out of date.
    2. Enqueues 'process_cron_job' to fire at 'next_run_at'.

    The task itself does not perform any work that can fail at the message
    level — if an unexpected error is raised, the whole poll is retried
    after 'RETRY_BASE_SECONDS' seconds.
    """
    try:
        ollama = get_ollama_cfg()
        if ollama is None:
            return

        due_jobs = _find_due_cron_jobs(current_dt=timezone.now())
        logger.info("Found %d cron jobs due for execution", len(due_jobs))

        for job in due_jobs:
            _refresh_cron_job_embedding_if_stale(job, ollama)
            _dispatch_cron_job(job)

    except Exception as exc:
        logger.error("Cron job poller failed", exc_info=True)
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS)


# ---------------------------------------------------------------------------
# Webhook setup
# ---------------------------------------------------------------------------


def _build_webhook_url(bot: Bot) -> str:
    """Construct the public URL Telegram should deliver updates to.

    The base URL is read from 'settings.WEBHOOK_BASE_URL'; trailing
    slashes are stripped before joining the bot's token. The trailing
    slash is significant — Telegram does not follow redirects for
    webhook deliveries.

    Args:
        bot: The Bot whose webhook URL is being constructed.

    Returns:
        The fully qualified webhook URL as a string.
    """
    base = settings.WEBHOOK_BASE_URL.strip("/")
    return f"{base}/webhook/{bot.telegram_bot_token}/"


@celery.task(bind=True, max_retries=MAX_RETRIES)
def setup_bot_webhook(self, bot_id: str) -> None:
    """Register the bot's webhook URL with the Telegram Bot API.

    Skipped for inactive bots (no error, no retry). On failure the task
    records the error in the Log table and retries with a fixed
    60s backoff.

    Args:
        bot_id: UUID (as 'str') of the bot to configure.

    Returns:
        None. Side effects: a 'Log' row and a Telegram API call.
    """
    bot: Optional[Bot] = None
    try:
        bot = get_bot_obj(bot_id=bot_id)
        if bot is None:
            return

        if not bot.is_active:
            logger.info("Skipping webhook setup for inactive bot: %s", bot.name)
            return

        webhook_url = _build_webhook_url(bot)
        logger.info("Setting up webhook for bot '%s' at: %s", bot.name, webhook_url)

        telegram_client = TelegramClientManager.create_client(bot)
        response = telegram_client.set_webhook(
            url=webhook_url, allowed_updates=TELEGRAM_ALLOWED_UPDATES
        )

        success_msg = WEBHOOK_SETUP_SUCCESS.format(bot_name=bot.name)
        create_log(bot=bot, is_success=True, desc=success_msg)
        logger.info("%s: %s", success_msg, response)

    except Exception as exc:
        description = GENERAL_TASK_ERROR.format(
            func_name="setup_bot_webhook",
            bot_name=bot.name if bot else "unknown",
            error=str(exc),
        )
        log_task_failure(
            bot,
            description=description,
            log_message=f"Webhook setup failed for bot {bot_id}",
        )
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS)


# ---------------------------------------------------------------------------
# Per-job / per-message processing tasks
# ---------------------------------------------------------------------------


def _build_processor(bot: Bot, ollama: Ollama) -> BotMessageProcessor:
    """Construct the BotMessageProcessor used by message / cron tasks.

    Centralises client wiring so both task bodies share the exact same
    configuration.

    Args:
        bot: The Bot the processor will operate on.
        ollama: Active Ollama configuration.

    Returns:
        A fully wired BotMessageProcessor instance.
    """
    ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
    return BotMessageProcessor(bot, ollama, ollama_client)


def _update_cron_job_schedule(cron_job: CronJob) -> None:
    """Persist the next and last run timestamps for a cron job.

    'next_run_at' is recomputed from the cron expression, and
    'last_run_at' is stamped with the current time. Only the two
    timestamp fields are written — keeping the update 'UPDATE' small
    avoids touching unrelated columns and minimises row-locking surface.

    Args:
        cron_job: The CronJob to update. Mutated in place.
    """
    cron_job.next_run_at = calculate_next_run_at(cron_job.cron_expression)
    cron_job.last_run_at = timezone.now()
    cron_job.save(update_fields=["next_run_at", "last_run_at"])


@celery.task(bind=True, max_retries=MAX_RETRIES)
def process_cron_job(self, job_id: str) -> None:
    """Execute a single scheduled cron job and deliver the bot's response.

    Pipeline:
        1. Resolve the Ollama config and CronJob row.
        2. Ask the bot to produce a response (with the 'cron_job' MCP
           server excluded from the available tool set).
        3. Send the response to the user via Telegram.
        4. Kick off 'manage_conversation_summary' on the default queue.
        5. Update the job's schedule metadata.
        6. Record a success or error log.

    Args:
        job_id: UUID (as 'str') of the cron job to run.

    Returns:
        None. Side effects: Telegram message, Log row, possibly a
        summary task.
    """
    cron_job: Optional[CronJob] = None
    try:
        ollama = get_ollama_cfg()
        if ollama is None:
            return

        cron_job = get_cron_obj(cron_job_id=job_id)
        if cron_job is None:
            return

        processor = _build_processor(cron_job.bot, ollama)
        response, ollama_ms = processor.process_cron_job(cron_job=cron_job)

        if not response:
            logger.info(NO_BOT_RESPONSE.format(bot_name=cron_job.bot.name))
            return

        processor.send_response(response=response)

        # Trigger summary management after response is sent.
        manage_conversation_summary.apply_async(
            queue=DEFAULT_QUEUE,
            kwargs={"bot_id": str(cron_job.bot_id)},
        )
        logger.info("Conversation summary task queued")

        _update_cron_job_schedule(cron_job)

        create_log(
            bot=cron_job.bot,
            is_success=True,
            desc=BOT_CRON_JOB_SUCCESS.format(bot_name=cron_job.bot.name, duration=ollama_ms),
        )

    except Exception as exc:
        bot = cron_job.bot if cron_job is not None else None
        description, retry_minutes = build_error_description(
            func_name="process_cron_job",
            bot=bot,
            error=exc,
        )
        log_task_failure(bot, description, "Failed to process cron job")
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS * retry_minutes)


def _queue_post_response_followups(bot: Bot, ollama: Ollama, bot_id: str) -> None:
    """Enqueue async work that should run after a user message is answered.

    Currently this includes:

    — 'manage_conversation_summary': always queued.
    — 'regenerate_observed_patterns': only when the pattern service
      signals that a refresh is due (every N user messages).

    Args:
        bot: The Bot that just produced a response.
        ollama: Active Ollama configuration (used to evaluate
            whether patterns should be regenerated).
        bot_id: The bot's UUID (as 'str') — passed as a Celery kwarg.
    """
    manage_conversation_summary.apply_async(
        queue=DEFAULT_QUEUE,
        kwargs={"bot_id": bot_id},
    )
    logger.info("Conversation summary task queued")

    pattern_svc = ObservedPatternsService(bot=bot, ollama=ollama)
    if pattern_svc.should_regenerate():
        regenerate_observed_patterns.apply_async(
            queue=DEFAULT_QUEUE,
            kwargs={"bot_id": bot_id},
        )
        logger.info("Pattern observation task queued")


@celery.task(bind=True, max_retries=MAX_RETRIES)
def process_inbound_message(self, bot_id: str, msg_id: str) -> None:
    """Generate and deliver a response to a single inbound user message.

    Pipeline:
        1. Resolve Ollama config, Bot, Message.
        2. Run the tool-calling loop via BotMessageProcessor.
        3. Send the response and persist it in the conversation history.
        4. Queue summary management and (conditionally) pattern refresh.
        5. Record a success or error log.

    Args:
        bot_id: UUID (as 'str') of the owning bot.
        msg_id: UUID (as 'str') of the user message to process.

    Returns:
        None. Side effects: Telegram message, Message row, Log
        row, possibly summary / pattern tasks.
    """
    bot: Optional[Bot] = None
    try:
        ollama = get_ollama_cfg()
        if ollama is None:
            return

        bot = get_bot_obj(bot_id=bot_id)
        if bot is None:
            return

        message = get_msg_obj(msg_id=msg_id)
        if message is None:
            return

        processor = _build_processor(bot, ollama)
        response, ollama_ms = processor.process_message(message=message)

        if not response:
            logger.info(NO_BOT_RESPONSE.format(bot_name=bot.name))
            return

        processor.send_response(response=response)

        _queue_post_response_followups(bot=bot, ollama=ollama, bot_id=bot_id)

        create_log(
            bot=bot,
            is_success=True,
            desc=BOT_MSG_SUCCESS.format(bot_name=bot.name, duration=ollama_ms),
        )

    except Exception as exc:
        description, retry_minutes = build_error_description(
            func_name="process_inbound_message",
            bot=bot,
            error=exc,
        )
        log_task_failure(bot, description, "Failed to process inbound message")
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS * retry_minutes)


# ---------------------------------------------------------------------------
# Summary management task
# ---------------------------------------------------------------------------


def _build_summary_bot_queryset(bot_id: Optional[str]):
    """Return the bot queryset used by 'manage_conversation_summary'.

    The queryset eagerly loads (via 'prefetch_related') every related
    collection that ConversationSummaryService needs to consult:

    — conversations: user / assistant messages.
    — system_messages: system messages (used as the existing summary seed when present).
    — mcp_servers_list: active MCP servers used to compute the token budget for the summary.

    Args:
        bot_id: When provided, scope the queryset to a single bot.
            Otherwise fetch every active bot.

    Returns:
        A Django queryset of Bot instances with the prefetches attached.
    """
    filters = {"id": bot_id} if bot_id else {"is_active": True}

    return Bot.objects.filter(**filters).prefetch_related(
        Prefetch(
            lookup="messages",
            queryset=Message.objects.filter(
                role__in=[
                    MessageRole.USER.value[0],
                    MessageRole.ASSISTANT.value[0],
                ]
            ),
            to_attr="conversations",
        ),
        Prefetch(
            lookup="messages",
            queryset=Message.objects.filter(role=MessageRole.SYSTEM.value[0]),
            to_attr="system_messages",
        ),
        Prefetch(
            lookup="mcp_servers",
            queryset=MCPServer.objects.filter(is_active=True),
            to_attr="mcp_servers_list",
        ),
    )


def _process_bot_summary(bot: Bot, ollama: Ollama, ollama_client: OllamaClient):
    """Run ConversationSummaryService for a single bot.

    Returns the service's 'process()' result untouched so the caller can
    classify the summary as "create" vs "update".

    Args:
        bot: The Bot to evaluate.
        ollama: Active Ollama configuration.
        ollama_client: A live OllamaClient used by the service.

    Returns:
        The result of 'ConversationSummaryService.process()' (typically a
        '(Message, created)' tuple) or None if the bot has no conversation to summarise.
    """
    if not getattr(bot, "conversations", None):
        return None
    return ConversationSummaryService(bot, ollama, ollama_client).process()


def _persist_summaries(
    summaries_to_create: list[Message],
    summaries_to_update: list[Message],
) -> None:
    """Bulk-write pending summary changes to the database.

    Using 'bulk_create' / 'bulk_update' keeps this task's query count
    constant regardless of how many bots are processed in a single run,
    which matters when the task is invoked without a 'bot_id' (all
    active bots at once).

    Args:
        summaries_to_create: New summary Message rows to insert.
        summaries_to_update: Existing summary Message rows whose
            'content' should be overwritten in-place.
    """
    if summaries_to_create:
        Message.objects.bulk_create(summaries_to_create)
        logger.info("Bulk created %d summaries", len(summaries_to_create))

    if summaries_to_update:
        Message.objects.bulk_update(summaries_to_update, ["content"])
        logger.info("Bulk updated %d summaries", len(summaries_to_update))


@celery.task(bind=True, max_retries=MAX_RETRIES)
def manage_conversation_summary(self, bot_id: Optional[str] = None) -> None:
    """Compact conversation history for one bot, or every active bot.

    The function is invoked in two ways:

    — Per bot ('bot_id' set): From 'process_inbound_message' and
      'process_cron_job' after a response is delivered.
    — Global ('bot_id' is None): From the admin panel's
      'save_model' override when the operator changes the LLM config
      and every bot's context window must be re-evaluated.

    For each bot the function calls 'ConversationSummaryService.process'.
    New summary messages are inserted via 'bulk_create'; existing ones are updated in-place via
    'bulk_update'.

    Args:
        bot_id: UUID (as 'str') of a single bot, or None to process
            every active bot.

    Returns:
        None. Side effects: potential Message inserts / updates.
    """
    try:
        ollama = get_ollama_cfg()
        if ollama is None:
            return

        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
        bots = _build_summary_bot_queryset(bot_id=bot_id)

        summaries_to_create: list[Message] = []
        summaries_to_update: list[Message] = []

        for bot in bots:
            result = _process_bot_summary(bot, ollama, ollama_client)
            if not result:
                continue

            summary_msg, created = result
            if created:
                summaries_to_create.append(summary_msg)
            else:
                summaries_to_update.append(summary_msg)

        _persist_summaries(summaries_to_create, summaries_to_update)
        logger.info("Summary management completed for %d bot(s)", bots.count())

    except Exception as exc:
        logger.error("Failed to manage conversation summaries", exc_info=True)
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS * (2**self.request.retries))


# ---------------------------------------------------------------------------
# Embedding generation task (fan-in entry point)
# ---------------------------------------------------------------------------


def _resolve_single_embedding_target(
    message_id: Optional[str],
    cron_job_id: Optional[str],
    mcp_server_id: Optional[str],
) -> Optional[tuple[str, str, Callable[[Ollama], str]]]:
    """Validate the embedding request and return the dispatch function.

    Exactly one of 'message_id', 'cron_job_id' or 'mcp_server_id'
    must be provided. When the validation passes, the returned triple
    contains:

    — obj_type: Human-readable label used in error logs.
    — obj_id: The actual ID (as 'str') being processed.
    — dispatch: A callable that performs the embedding work given
      an active Ollama instance.

    Args:
        message_id: Optional message UUID.
        cron_job_id: Optional cron job UUID.
        mcp_server_id: Optional MCP server UUID.

    Returns:
        '(obj_type, obj_id, dispatch)' on success, None when the
        request is invalid (no IDs, or more than one ID).
    """
    provided = {
        "Message": (message_id, generate_message_embedding),
        "Cron Job": (cron_job_id, generate_cron_job_embedding),
        "MCPServer": (mcp_server_id, generate_mcp_embedding),
    }
    provided = {key: (value, fn) for key, (value, fn) in provided.items() if value}

    if not provided:
        logger.error("Invalid embedding request received: no target ID supplied")
        return None

    if len(provided) > 1:
        logger.error(
            "Invalid embedding request received: expected exactly one ID, got %s",
            list(provided),
        )
        return None

    ((obj_type, (obj_id, dispatch)),) = provided.items()
    return obj_type, obj_id, dispatch


@celery.task(bind=True, max_retries=MAX_RETRIES)
def generate_embedding(
    self,
    message_id: Optional[str] = None,
    cron_job_id: Optional[str] = None,
    mcp_server_id: Optional[str] = None,
) -> None:
    """Generate and persist the embedding for exactly one target object.

    Acts as a fan-in entry point: callers dispatch a single Celery task
    with the appropriate ID and the function routes the work to the
    correct 'generate_*_embedding' helper. The task retries with
    exponential backoff ('60 * 2**retries' seconds) on failure and
    records an error log row attributed to the owning bot when possible.

    Args:
        message_id: UUID (as 'str') of the message to embed.
        cron_job_id: UUID (as 'str') of the cron job to embed.
        mcp_server_id: UUID (as 'str') of the MCP server to embed.

    Returns:
        None. Side effects: a row in 'content_embedding' /
        'schedule_embedding' / 'tools_description_embedding' (and
        possibly a follow-up 'process_inbound_message' task for
        message targets).
    """
    bot: Optional[Bot] = None
    obj_type: Optional[str] = None
    obj_id: Optional[str] = None

    try:
        target = _resolve_single_embedding_target(
            message_id=message_id,
            cron_job_id=cron_job_id,
            mcp_server_id=mcp_server_id,
        )
        if target is None:
            return
        obj_type, obj_id, dispatch = target

        ollama = get_ollama_cfg()
        if ollama is None:
            return

        bot = dispatch(ollama, obj_id)

    except Exception as exc:
        logger.error("Generate embedding failed", exc_info=True)
        if bot is not None and obj_type is not None and obj_id is not None:
            create_log(
                bot=bot,
                is_success=False,
                desc=GENERATE_EMBEDDING_FAILED.format(
                    obj_type=obj_type,
                    obj_id=obj_id,
                    error=str(exc),
                ),
            )
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS * (2**self.request.retries))


# ---------------------------------------------------------------------------
# Observed-pattern regeneration task
# ---------------------------------------------------------------------------


@celery.task(bind=True, max_retries=MAX_RETRIES)
def regenerate_observed_patterns(self, bot_id: str) -> None:
    """Re-derive a bot's behavioural profile from recent conversation history.

    Triggered every 'PATTERN_REGEN_EVERY_N_MESSAGES' user messages
    (see 'services.observed_patterns.ObservedPatternsService'). Runs
    out-of-band so the per-message processing path stays cheap.

    Args:
        bot_id: UUID (as 'str') of the bot whose patterns should be
            refreshed.

    Returns:
        None. Side effects: 'Bot.observed_patterns' updated,
        success or error row added to the Log table.
    """
    bot: Optional[Bot] = None
    try:
        ollama = get_ollama_cfg()
        if ollama is None:
            return

        bot = get_bot_obj(bot_id=bot_id)
        if bot is None:
            return

        ObservedPatternsService(bot=bot, ollama=ollama).regenerate()

        create_log(
            bot=bot,
            is_success=True,
            desc=UPDATE_OBSERVED_PATTERNS_SUCCESS.format(bot_name=bot.name),
        )

    except Exception as exc:
        bot_name = bot.name if bot is not None else "unknown"
        description = GENERAL_TASK_ERROR.format(
            func_name="regenerate_observed_patterns",
            bot_name=bot_name,
            error=str(exc),
        )
        log_task_failure(bot, description, "regenerate_observed_patterns failed")
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS * (2**self.request.retries))
