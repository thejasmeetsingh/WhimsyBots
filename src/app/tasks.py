"""Celery task definitions for async job processing"""

import logging
from typing import Optional

from django.conf import settings
from django.utils import timezone
from django.db.models import Prefetch

from app.choices import MessageRole
from app.models import Bot, CronJob, Log, MCPServer, Message, Ollama
from app.utils import calculate_next_run_at, get_token_hash
from clients import OllamaClient
from clients.telegram import TelegramRateLimitError
from managers import OllamaConfigManager, TelegramClientManager
from services.bot_processor import BotMessageProcessor
from services.conversation_summary import ConversationSummaryService
from services.embedding import EmbeddingService
from services.observed_patterns import ObservedPatternsService
from services.telegram_update_handler import TelegramUpdateHandler
from strings import (
    BOT_CRON_JOB_SUCCESS,
    BOT_MSG_SUCCESS,
    GENERAL_TASK_ERROR,
    GENERATE_EMBEDDING_SUCCESS,
    INVALID_BOT_TOKEN,
    NO_BOT_RESPONSE,
    NO_OLLAMA,
    OBJ_NOT_FOUND,
    SUMMARY_PROCESS_SUCCESS,
    TELEGRAM_RATE_LIMIT_ERROR,
    UPDATE_OBSERVED_PATTERNS_SUCCESS,
    WEBHOOK_SETUP_SUCCESS,
)
from whimsybots.celery import task as celery

logger = logging.getLogger(__name__)


"--------------------- HELPERS ----------------------------------"


def get_ollama_cfg() -> Optional[Ollama]:
    """Retrieve the Ollama configuration instance.

    Fetches the active Ollama configuration from the config manager and returns it if available.
    Logs a warning message if no Ollama configuration is found.

    Returns:
        The configured Ollama object, or None if not configured.

    Raises:
        No exception raised; logs warning instead when Ollama is unavailable.
    """

    ollama = OllamaConfigManager.get_ollama_config()
    if ollama:
        return ollama

    logger.warning(NO_OLLAMA)


def get_bot_obj(bot_id: str) -> Optional[Bot]:
    """Retrieve a Bot instance by its ID.

    Fetches the bot object from the database using the provided bot ID (UUID).
    Handles the case where no bot exists with the given ID and logs an error message.

    Args:
        bot_id: The UUID string of the bot to retrieve.

    Returns:
        The Bot instance if found, or None if not found.

    Raises:
        No exception raised; logs error instead when bot is not found.
    """

    try:
        bot = Bot.objects.get(id=bot_id)
        return bot
    except Bot.DoesNotExist:
        logger.error(OBJ_NOT_FOUND.format(obj_type="bot", obj_id=bot_id))


def get_cron_obj(cron_job_id: str) -> Optional[CronJob]:
    """Retrieve a CronJob instance by its ID.

    Fetches the cron job object from the database using the provided cron job ID (UUID).
    Handles the case where no cron job exists with the given ID and logs an error message.

    Args:
        cron_job_id: The UUID string of the cron job to retrieve.

    Returns:
        The CronJob instance if found, or None if not found.

    Raises:
        No exception raised; logs error instead when cron job is not found.
    """

    try:
        cron_job = CronJob.objects.get(id=cron_job_id)
        return cron_job
    except CronJob.DoesNotExist:
        logger.error(OBJ_NOT_FOUND.format(obj_type="cron job", obj_id=cron_job_id))


def get_msg_obj(msg_id: str) -> Optional[Message]:
    """Retrieve a Message instance by its ID.

    Fetches the message object from the database using the provided message ID (UUID).
    Handles the case where no message exists with the given ID and logs an error message.

    Args:
        msg_id: The UUID string of the message to retrieve.

    Returns:
        The Message instance if found, or None if not found.

    Raises:
        No exception raised; logs error instead when message is not found.
    """

    try:
        message = Message.objects.get(id=msg_id)
        return message
    except Message.DoesNotExist:
        logger.error(OBJ_NOT_FOUND.format(obj_type="message", obj_id=msg_id))


def create_log(bot: Bot, is_success: bool, desc: str) -> None:
    """Create a new Log entry for the given bot.

    Creates and saves a new log record associated with the specified bot, recording
    whether an operation succeeded or failed along with a description of what occurred.

    Args:
        bot: The Bot instance to associate this log entry with.
        is_success: Boolean indicating if the logged operation was successful (True) or failed (False).
        desc: A string describing the action that was performed and its outcome.

    Returns:
        None; creates a new Log record in the database.
    """

    Log.objects.create(bot=bot, is_success=is_success, description=desc)


"--------------------- TASKS ------------------------------------"


@celery.task(bind=True, max_retries=3)
def telegram_msg_handler(self, bot_token: str, update: dict) -> None:
    """
    Fetch bot object from given bot token and processs the given update from telegram
    """

    try:
        # Fetch active bots
        bot_token_hash = get_token_hash(bot_token)
        bot = Bot.objects.get(telegram_bot_token_hash=bot_token_hash)
        TelegramUpdateHandler.handle_update(bot, update)
    except Bot.DoesNotExist:
        logger.error(INVALID_BOT_TOKEN.format(bot_token=bot_token))
    except Exception as e:
        logger.error("Telegram poller failed", exc_info=True)
        # Retry after 60 seconds
        raise self.retry(exc=e, countdown=60)


@celery.task(bind=True, max_retries=3)
def cron_job_poller(self) -> None:
    """
    Poll cron jobs for all the active bots that are due for execution and queue them for processing.

    This task runs periodically to check if any scheduled cron job need to be executed
    and updates their next scheduled run time.

    Returns:
        Status message
    """

    try:
        current_dt = timezone.now()

        # Check if Ollama is configured
        ollama = get_ollama_cfg()
        if not ollama:
            return

        # Find cron jobs due for execution
        due_jobs = CronJob.objects.filter(
            is_active=True,
            bot__is_active=True,
            bot__telegram_chat_id__isnull=False,
            next_run_at__lte=current_dt,
        )

        logger.info(f"Found {due_jobs.count()} cron jobs due for execution")

        # Queue each bot for processing on default worker
        for job in due_jobs:
            # Regenerate the schedule embedding if it's missing or stale
            # (i.e. older than the cron job's last update).
            if job.schedule_embedding is None or (
                job.schedule_embedding_updated_at
                and job.schedule_embedding_updated_at < job.updated_at
            ):
                embedding_svc = EmbeddingService(bot=job.bot, ollama=ollama)
                embedding_svc.save_cron_job_embedding(cron_job=job)

            process_cron_job.apply_async(
                queue="default",
                kwargs={"job_id": str(job.id)},
                eta=job.next_run_at,
            )

    except Exception as e:
        logger.error("Cron job poller failed", exc_info=True)
        # Retry after 60 seconds
        raise self.retry(exc=e, countdown=60)


@celery.task(bind=True, max_retries=3)
def setup_bot_webhook(self, bot_id: str) -> None:
    """
    Set up Telegram webhook for a bot.

    Fetches the bot configuration, constructs the webhook URL,
    and registers it with Telegram API.

    Args:
        bot_id: UUID of the bot to configure webhook for

    Returns:
        Status message

    Raises:
        celery.exceptions.MaxRetriesExceededError: If webhook setup fails after all retries
    """

    try:
        bot = get_bot_obj(bot_id=bot_id)
        if not bot:
            return

        if not bot.is_active:
            logger.info(f"Skipping webhook setup for inactive bot: {bot.name}")
            return "Bot is inactive, webhook setup skipped"

        # Construct webhook URL
        webhook_url = (
            f"{settings.WEBHOOK_BASE_URL.strip('/')}/webhook/{bot.telegram_bot_token}/"
        )

        logger.info(f"Setting up webhook for bot '{bot.name}' at: {webhook_url}")

        # Create Telegram client and set webhook
        telegram_client = TelegramClientManager.create_client(bot)
        response = telegram_client.set_webhook(
            url=webhook_url, allowed_updates=["message"]
        )

        log = WEBHOOK_SETUP_SUCCESS.format(bot_name=bot.name)

        # Create Log
        create_log(bot=bot, is_success=True, desc=log)

        logger.info(f"{log}: {response}")

    except Exception as e:
        logger.error(f"Webhook setup failed for bot {bot_id}: {e}", exc_info=True)

        # Create an error log
        create_log(
            bot=bot,
            is_success=False,
            desc=GENERAL_TASK_ERROR.format(
                func_name="setup_bot_webhook",
                bot_name=bot.name,
                error=str(e),
            ),
        )

        raise self.retry(exc=e, countdown=60)


@celery.task(bind=True, max_retries=3)
def process_cron_job(self, job_id: str) -> None:
    """
    Process the given cron job and update its metadata after processing

    Args:
        job_id: ID of the cron job object

    Returns:
        Status message

    Raises:
        TelegramRateLimitError: If telegram sends a 429 error
        Retries on failure with exponential backoff
    """
    try:
        ollama = get_ollama_cfg()
        if not ollama:
            return

        cron_job = get_cron_obj(cron_job_id=job_id)
        if not cron_job:
            return

        # Initialize clients and processor
        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
        processor = BotMessageProcessor(cron_job.bot, ollama, ollama_client)

        # Process cron job
        response, ollama_ms = processor.process_cron_job(cron_job=cron_job)

        if not response:
            logger.info(NO_BOT_RESPONSE.format(bot_name=cron_job.bot.name))
            return

        # Send response
        processor.send_response(response=response)

        # Trigger summary management after response is sent
        manage_conversation_summary.apply_async(
            queue="default",
            kwargs={"bot_id": str(cron_job.bot_id)},
        )
        logger.info("Conversation summary task queued")

        # Update cron job metadata
        cron_job.next_run_at = calculate_next_run_at(cron_job.cron_expression)
        cron_job.last_run_at = timezone.now()
        cron_job.save(update_fields=["next_run_at", "last_run_at"])

        # Create a success log
        create_log(
            bot=cron_job.bot,
            is_success=True,
            desc=BOT_CRON_JOB_SUCCESS.format(
                bot_name=cron_job.bot.name, duration=ollama_ms
            ),
        )

    except Exception as e:
        bot_name = cron_job.bot.name

        if isinstance(e, TelegramRateLimitError):
            desc = TELEGRAM_RATE_LIMIT_ERROR.format(
                func_name="process_cron_job", bot_name=bot_name, error=str(e)
            )
            retry = e.retry_after
        else:
            desc = GENERAL_TASK_ERROR.format(
                func_name="process_cron_job", bot_name=bot_name, error=str(e)
            )
            retry = 2**self.request.retries

        # Create an error log
        create_log(bot=cron_job.bot, is_success=False, desc=desc)

        logger.error("Failed to process cron job", exc_info=True)
        raise self.retry(exc=e, countdown=60 * retry)


@celery.task(bind=True, max_retries=3)
def process_inbound_message(self, bot_id: str, msg_id: str) -> None:
    """
    Process an inbound message through intent classification and tool execution.

    Args:
        bot_id: ID of the bot processing the message
        msg_id: ID of the message to process

    Returns:
        Status message

    Raises:
        TelegramRateLimitError: If telegram sends a 429 error
        Retries on failure with exponential backoff
    """

    try:
        ollama = get_ollama_cfg()
        if not ollama:
            return

        bot = get_bot_obj(bot_id=bot_id)
        if not bot:
            return

        message = get_msg_obj(msg_id=msg_id)
        if not message:
            return

        # Initialize clients and processor
        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
        processor = BotMessageProcessor(bot, ollama, ollama_client)

        # Process message
        response, ollama_ms = processor.process_message(message=message)

        if not response:
            logger.info(NO_BOT_RESPONSE.format(bot_name=bot.name))
            return

        # Send response
        processor.send_response(response=response)

        # Trigger summary management after response is sent
        manage_conversation_summary.apply_async(
            queue="default",
            kwargs={"bot_id": bot_id},
        )
        logger.info("Conversation summary task queued")

        # Conditionally regenerate observed patterns
        pattern_svc = ObservedPatternsService(bot=bot, ollama=ollama)
        if pattern_svc.should_regenerate():
            regenerate_observed_patterns.apply_async(
                queue="default",
                kwargs={"bot_id": bot_id},
            )
            logger.info("Pattern observation task queued")

        # Create a success log
        create_log(
            bot=bot,
            is_success=True,
            desc=BOT_MSG_SUCCESS.format(bot_name=bot.name, duration=ollama_ms),
        )

    except Exception as e:
        if isinstance(e, TelegramRateLimitError):
            desc = TELEGRAM_RATE_LIMIT_ERROR.format(
                func_name="process_inbound_message", bot_name=bot.name, error=str(e)
            )
            retry = e.retry_after
        else:
            desc = GENERAL_TASK_ERROR.format(
                func_name="process_inbound_message", bot_name=bot.name, error=str(e)
            )
            retry = 2**self.request.retries

        # Create an error log
        create_log(bot=bot, is_success=False, desc=desc)

        logger.error("Failed to process inbound message", exc_info=True)
        raise self.retry(exc=e, countdown=60 * retry)


@celery.task(bind=True, max_retries=3)
def manage_conversation_summary(self, bot_id: Optional[str] = None) -> None:
    """
    Manage context window optimization for one or all active bots.

    Called after a message is sent to the user (process_inbound_message,
    process_cron_job) with a bot_id to process a single bot.

    Called without bot_id when triggered from the Ollama admin save_model
    override — in that case all active bots are processed.

    Args:
        bot_id: ID of the bot to process. If None, processes all active bots.

    Returns:
        Status message

    Raises:
        Retries on failure with exponential backoff
    """

    try:
        ollama = get_ollama_cfg()
        if not ollama:
            return

        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)

        # Adjust filter based on bot_id parameter
        filters = {"id": bot_id} if bot_id else {"is_active": True}

        # Fetch bots with messages
        bots = Bot.objects.filter(**filters).prefetch_related(
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

        summaries_to_update = []
        summaries_to_create = []

        for bot in bots:
            # Only create summary if there is conversation records
            if hasattr(bot, "conversations") and bot.conversations:
                service = ConversationSummaryService(
                    bot,
                    ollama,
                    ollama_client,
                )
                result = service.process()

                if result:
                    summary_msg, created = result
                    if created:
                        summaries_to_create.append(summary_msg)
                    else:
                        summaries_to_update.append(summary_msg)

        # Batch DB writes to minimize query count
        if summaries_to_create:
            Message.objects.bulk_create(summaries_to_create)
            logger.info(f"Bulk created {len(summaries_to_create)} summaries")

        if summaries_to_update:
            Message.objects.bulk_update(summaries_to_update, ["content"])
            logger.info(f"Bulk updated {len(summaries_to_update)} summaries")

        logger.info(SUMMARY_PROCESS_SUCCESS.format(bots_len=bots.count()))

    except Exception as e:
        logger.error("Failed to manage conversation summaries", exc_info=True)
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@celery.task(bind=True, max_retries=3)
def generate_embedding(self, message_id: str) -> None:
    """
    Generates and saves the content_embedding vector for a user message.

    - Always runs after the response is sent (non-blocking)
    - Skips silently if embedding already exists or role != USER
    - Retries with exponential backoff on transient failures
    """

    try:
        ollama = get_ollama_cfg()
        if not ollama:
            return

        message = get_msg_obj(msg_id=message_id)
        if not message:
            return

        if message.role != MessageRole.USER.value[0]:
            logger.debug("Skipping embedding for non-user message %s", message_id)
            return

        if message.content_embedding is not None:
            logger.debug(
                "Embedding already exists for message %s, skipping", message_id
            )
            return

        embedding_svc = EmbeddingService(bot=message.bot, ollama=ollama)
        embedding_svc.save_message_embedding(message=message)

        # Queue for processing on default worker
        process_inbound_message.apply_async(
            queue="default",
            kwargs={"bot_id": str(message.bot_id), "msg_id": str(message.id)},
        )

        # Create a success log
        create_log(
            bot=message.bot,
            is_success=True,
            desc=GENERATE_EMBEDDING_SUCCESS.format(bot_name=message.bot.name),
        )

    except Exception as e:
        logger.error(
            "generate_embedding failed for message %s", message_id, exc_info=True
        )

        # Create an error log
        create_log(
            bot=message.bot,
            is_success=False,
            desc=GENERAL_TASK_ERROR.format(
                func_name="generate_embedding", bot_name=message.bot.name, error=str(e)
            ),
        )

        # Exponential backoff: 60s, 120s, 240s
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@celery.task(bind=True, max_retries=3)
def regenerate_observed_patterns(self, bot_id: str) -> None:
    """
    Analyzes recent conversation history and updates Bot.observed_patterns
    with a compact behavioral profile (always capped at 4096 chars).

    Triggered every PATTERN_REGEN_EVERY_N_MESSAGES user messages.
    """

    try:
        ollama = get_ollama_cfg()
        if not ollama:
            return

        bot = get_bot_obj(bot_id=bot_id)
        if not bot:
            return

        pattern_svc = ObservedPatternsService(bot=bot, ollama=ollama)
        pattern_svc.regenerate()

        # Create a success log
        create_log(
            bot=bot,
            is_success=True,
            desc=UPDATE_OBSERVED_PATTERNS_SUCCESS.format(bot_name=bot.name),
        )

    except Exception as e:
        logger.exception(
            "regenerate_observed_patterns failed for bot %s", bot_id, exc_info=True
        )

        # Create an error log
        create_log(
            bot=bot,
            is_success=False,
            desc=GENERAL_TASK_ERROR.format(
                func_name="regenerate_observed_patterns",
                bot_name=bot.name,
                error=str(e),
            ),
        )

        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))
