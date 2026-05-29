"""Celery task definitions for async job processing"""

import asyncio
import logging
from typing import Optional

from django.conf import settings
from django.utils import timezone

from app.choices import MessageRole
from app.models import Bot, CronJob, Log, MCPServer, Message
from app.utils import calculate_next_run_at, get_token_hash
from clients import OllamaClient
from clients.telegram import TelegramRateLimitError
from managers import OllamaConfigManager, TelegramClientManager
from services.bot_processor import BotMessageProcessor
from services.conversation_summary import ConversationSummaryService
from services.embedding import EmbeddingService
from services.log_formatter import LogFormatter
from services.observed_patterns import ObservedPatternsService
from services.report_generator import ReportGeneratorService
from services.telegram_update_handler import TelegramUpdateHandler
from services.tool_executor import MCPToolsBuilder
from strings import NO_OLLAMA, OBJ_NOT_FOUND
from whimsybots.celery import task as celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=3)
def telegram_msg_handler(self, bot_token: str, update: dict):
    """
    Fetch bot object from given bot token and processs the given update from telegram
    """

    try:
        # Fetch active bots
        bot_token_hash = get_token_hash(bot_token)
        bot = Bot.objects.get(telegram_bot_token_hash=bot_token_hash)
        TelegramUpdateHandler.handle_update(bot, update)
    except Bot.DoesNotExist:
        logger.error(f"Bot with token {bot_token} not found")
        return f"Bot with token {bot_token} not found"
    except Exception as e:
        logger.error("Telegram poller failed", exc_info=True)
        # Retry after 60 seconds
        raise self.retry(exc=e, countdown=60)


@celery.task(bind=True, max_retries=3)
def cron_job_poller(self):
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
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.warning(NO_OLLAMA)
            return NO_OLLAMA

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
            process_cron_job.apply_async(
                queue="default",
                kwargs={"job_id": str(job.id)},
                eta=job.next_run_at,
            )

        logger.info("Cron job poller completed successfully")
        return "Processed due cron jobs successfully"

    except Exception as e:
        logger.error("Cron job poller failed", exc_info=True)
        # Retry after 60 seconds
        raise self.retry(exc=e, countdown=60)


@celery.task(bind=True, max_retries=3)
def setup_bot_webhook(self, bot_id: str):
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
        # Fetch bot configuration
        bot = Bot.objects.get(id=bot_id)

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

        logger.info(f"Webhook setup successful for bot '{bot.name}': {response}")
        return f"Webhook setup successful for bot '{bot.name}'"

    except Bot.DoesNotExist:
        logger.error(f"Bot with ID {bot_id} not found")
        return f"Bot with ID {bot_id} not found"
    except Exception as e:
        logger.error(f"Webhook setup failed for bot {bot_id}: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=60)


@celery.task(bind=True, max_retries=3)
def process_cron_job(self, job_id: str):
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
        # Validate configuration
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.error(NO_OLLAMA)
            return NO_OLLAMA

        try:
            cron_job = CronJob.objects.get(id=job_id)
        except CronJob.DoesNotExist:
            logger.error(OBJ_NOT_FOUND.format(obj_type="cron job", obj_id=job_id))
            return OBJ_NOT_FOUND.format(obj_type="cron job", obj_id=job_id)

        # Initialize clients and processor
        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
        processor = BotMessageProcessor(cron_job.bot, ollama, ollama_client)

        # Process cron job
        result, ollama_ms = processor.process_cron_job(
            name=cron_job.name, description=cron_job.description
        )

        if result.is_report:
            # Kick-off report generation process
            generate_report.apply_async(queue="default", kwargs={"cron_job_id": job_id})
            logger.info("Report generation queued")

        # Send response
        processor.send_response(result)

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

        # Success Log
        desc = (
            LogFormatter("process_cron_job")
            .add("bot", cron_job.bot.name)
            .add("job", cron_job.name)
            .add(
                "ollama",
                f"{ollama_ms}ms" if ollama_ms else None,
            )
            .build()
        )
        Log.objects.create(bot=cron_job.bot, is_success=True, description=desc)

        logger.info(f"Cron job processing completed for bot: {cron_job.bot.name}")
        return "Processed cron job successfully"

    except TelegramRateLimitError as e:
        if cron_job:
            desc = (
                LogFormatter("process_cron_job")
                .add("bot", cron_job.bot.name)
                .add("error", "TelegramRateLimitError")
                .add("retry_after", f"{e.retry_after}s")
                .build()
            )
            Log.objects.create(bot=cron_job.bot, is_success=False, description=desc)

        logger.warning(
            f"Telegram rate limit hit in process_cron_job, retrying in {e.retry_after}s"
        )
        raise self.retry(exc=e, countdown=e.retry_after)

    except Exception as e:
        if cron_job:
            desc = (
                LogFormatter("process_cron_job")
                .add("bot", cron_job.bot.name)
                .add("error", type(e).__name__)
                .build()
            )
            Log.objects.create(bot=cron_job.bot, is_success=False, description=desc)

        logger.error("Failed to process cron job", exc_info=True)
        # Retry with exponential backoff: 60s, 300s, 900s
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@celery.task(bind=True, max_retries=3)
def process_inbound_message(self, bot_id: str, msg_id: str):
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
        # Validate configuration
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.error(NO_OLLAMA)
            return NO_OLLAMA

        # Get bot
        try:
            bot = Bot.objects.get(id=bot_id)
        except Bot.DoesNotExist:
            logger.error(OBJ_NOT_FOUND.format(obj_type="bot", obj_id=bot))
            return OBJ_NOT_FOUND.format(obj_type="bot", obj_id=bot)

        # Get message
        try:
            message = Message.objects.get(id=msg_id)
        except Message.DoesNotExist:
            logger.error(OBJ_NOT_FOUND.format(obj_type="message", obj_id=msg_id))
            return OBJ_NOT_FOUND.format(obj_type="message", obj_id=msg_id)

        # Initialize clients and processor
        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
        processor = BotMessageProcessor(bot, ollama, ollama_client)

        # Process message
        result, ollama_ms = processor.process_message(message=message)

        if result.response:
            # Send response
            processor.send_response(response=result)

            if result.is_report:
                # Kick-off report generation process
                generate_report.apply_async(queue="default", kwargs={"msg_id": msg_id})
                logger.info("Report generation queued")

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

        # Success Log
        desc = (
            LogFormatter("process_inbound_message")
            .add("bot", bot.name)
            .add("is_report", result.is_report)
            .add(
                "ollama",
                f"{ollama_ms}ms" if ollama_ms else None,
            )
            .build()
        )
        Log.objects.create(bot=bot, is_success=True, description=desc)

        logger.info(f"Message processing completed for bot: {bot.name}")
        return "Processed inbound message successfully"

    except TelegramRateLimitError as e:
        if bot:
            desc = (
                LogFormatter("process_inbound_message")
                .add("bot", bot.name)
                .add("error", "TelegramRateLimitError")
                .add("retry_after", f"{e.retry_after}s")
                .build()
            )
            Log.objects.create(bot=bot, is_success=False, description=desc)

        logger.warning(
            f"Telegram rate limit hit in process_inbound_message, "
            f"retrying in {e.retry_after}s"
        )
        raise self.retry(exc=e, countdown=e.retry_after)

    except Exception as e:
        if bot:
            desc = (
                LogFormatter("process_inbound_message")
                .add("bot", bot.name)
                .add("error", type(e).__name__)
                .build()
            )
            Log.objects.create(bot=bot, is_success=False, description=desc)

        logger.error("Failed to process inbound message", exc_info=True)
        # Retry with exponential backoff: 60s, 300s, 900s
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@celery.task(bind=True, max_retries=3)
def generate_report(self, msg_id: Optional[str], cron_job_id: Optional[str]):
    """
    Generate a report from bot conversation history and send to user.

    Args:
        msg_id (str): ID of the message to retreive the actual request
        cron_job_id: ID of cron job associated with report generation tasks

    Returns:
        Status message

    Raises:
        TelegramRateLimitError: If telegram sends a 429 error
        Retries on failure with exponential backoff
    """

    if not msg_id or not cron_job_id:
        return "No msg_id or cron_job_id provided"

    try:
        # Validate configuration
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.error(NO_OLLAMA)
            return NO_OLLAMA

        if msg_id:
            # Get message
            try:
                message = Message.objects.get(id=msg_id)
                bot = message.bot

                # Update message for report identifier
                message.is_report = True
                message.save(update_fields=["is_report"])
            except Message.DoesNotExist:
                logger.error(OBJ_NOT_FOUND.format(obj_type="message", obj_id=msg_id))
                return OBJ_NOT_FOUND.format(obj_type="message", obj_id=msg_id)
        else:
            # Get CronJob
            try:
                cron_job = CronJob.objects.get(id=cron_job_id)
                bot = cron_job.bot

                # Create a temporary message object
                message = Message(
                    bot=bot,
                    role=MessageRole.USER.value[0],
                    is_report=True,
                    content=f"Task Name: {cron_job.name}\nTask Description: {cron_job.description}",
                )
            except CronJob.DoesNotExist:
                logger.error(
                    OBJ_NOT_FOUND.format(obj_type="cron job", obj_id=cron_job_id)
                )
                return OBJ_NOT_FOUND.format(obj_type="cron job", obj_id=cron_job_id)

        # Initialize clients and service
        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
        generator = ReportGeneratorService(bot, ollama, ollama_client)

        # Generate and send report
        result, ollama_ms = generator.generate_and_send(message=message)

        # Success Log
        desc = (
            LogFormatter("generate_report")
            .add("bot", bot.name)
            .add(
                "ollama",
                f"{ollama_ms}ms" if ollama_ms else None,
            )
            .build()
        )
        Log.objects.create(bot=bot, is_success=True, description=desc)

        logger.info(f"Report generated for bot: {bot.name}")
        return result

    except TelegramRateLimitError as e:
        if bot:
            desc = (
                LogFormatter("generate_report")
                .add("bot", bot.name)
                .add("error", "TelegramRateLimitError")
                .add("retry_after", f"{e.retry_after}s")
                .build()
            )
            Log.objects.create(bot=bot, is_success=False, description=desc)

        logger.warning(
            f"Telegram rate limit hit in generate_report, retrying in {e.retry_after}s"
        )
        raise self.retry(exc=e, countdown=e.retry_after)

    except Exception as e:
        if bot:
            desc = (
                LogFormatter("generate_report")
                .add("bot", bot.name)
                .add("error", type(e).__name__)
                .build()
            )
            Log.objects.create(bot=bot, is_success=False, description=desc)

        logger.error("Failed to generate report", exc_info=True)
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@celery.task(bind=True, max_retries=3)
def manage_conversation_summary(self, bot_id: Optional[str] = None):
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
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.error(NO_OLLAMA)
            return NO_OLLAMA

        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)

        if bot_id:
            try:
                bots = [Bot.objects.get(id=bot_id)]
            except Bot.DoesNotExist:
                logger.error(OBJ_NOT_FOUND.format(obj_type="bot", obj_id=bot_id))
                return OBJ_NOT_FOUND.format(obj_type="bot", obj_id=bot_id)
        else:
            bots = list(Bot.objects.filter(is_active=True))

        summaries_to_update = []
        summaries_to_create = []

        # Build tools from servers
        mcp_servers = list(MCPServer.objects.filter(bot_id=bot_id, is_active=True))

        # Add default mcp server's to the 'mcp_servers' list
        default_servers = MCPServer.get_default_mcp_servers()
        mcp_servers.extend(list(default_servers.values()))

        tools_config = asyncio.run(
            MCPToolsBuilder.build_tools_from_servers(mcp_servers)
        )

        for bot in bots:
            service = ConversationSummaryService(
                bot,
                ollama,
                ollama_client,
                tool_definitions=[tool.tool for tool in tools_config],
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

        return f"Summary management completed for {len(bots)} bot(s)"

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
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.error(NO_OLLAMA)
            return NO_OLLAMA

        # Get message
        try:
            message = Message.objects.get(id=message_id)
        except Message.DoesNotExist:
            logger.error(OBJ_NOT_FOUND.format(obj_type="message", obj_id=message_id))
            return OBJ_NOT_FOUND.format(obj_type="message", obj_id=message_id)

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

    except Exception as exc:
        logger.exception(
            "generate_embedding failed for message %s: %s", message_id, exc
        )
        # Exponential backoff: 60s, 120s, 240s
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))


@celery.task(bind=True, max_retries=3)
def regenerate_observed_patterns(self, bot_id: str) -> None:
    """
    Analyzes recent conversation history and updates Bot.observed_patterns
    with a compact behavioral profile (always capped at 4096 chars).

    Triggered every PATTERN_REGEN_EVERY_N_MESSAGES user messages.
    """

    try:
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.error(NO_OLLAMA)
            return NO_OLLAMA

        # Get bot
        try:
            bot = Bot.objects.get(id=bot_id)
        except Bot.DoesNotExist:
            logger.error(OBJ_NOT_FOUND.format(obj_type="bot", obj_id=bot_id))
            return OBJ_NOT_FOUND.format(obj_type="bot", obj_id=bot_id)

        pattern_svc = ObservedPatternsService(bot=bot, ollama=ollama)
        pattern_svc.regenerate()

    except Exception as exc:
        logger.exception(
            "regenerate_observed_patterns failed for bot %s: %s", bot_id, exc
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
