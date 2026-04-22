"""Celery task definitions for async job processing"""

import logging
from typing import Optional

from django.utils import timezone
from clients import OllamaClient

from app.models import Bot, Ollama, Message
from app.utils import calculate_next_run_at, parse_telegram_update
from whimsybots.celery import task as celery
from app.config import CeleryConfig
from app.managers import OllamaConfigManager, TelegramClientManager
from app.services import (
    BotMessageProcessor,
    ReportGeneratorService,
)


logger = logging.getLogger(__name__)


def handle_inbound_update(bot_id: str, update: dict) -> None:
    """
    Handle incoming Telegram updates and queue for processing.

    Args:
        bot_id: ID of the bot receiving the update
        update: Telegram update dictionary

    Raises:
        Bot.DoesNotExist: If bot not found
    """

    try:
        parsed = parse_telegram_update(update)
        if not parsed:
            logger.warning("Failed to parse Telegram update")
            return

        chat_id = parsed["chat_id"]
        text = parsed["text"]

        bot = Bot.objects.get(id=bot_id)

        # Update chat_id if not already set
        if not bot.telegram_chat_id:
            bot.telegram_chat_id = str(chat_id)
            bot.save(update_fields=["telegram_chat_id"])

        # Create message
        message = Message.objects.create(
            bot=bot,
            role="user",
            content=text,
        )

        # Send typing indicator
        telegram_client = TelegramClientManager.create_client(bot)
        telegram_client.send_typing_action()

        # Queue for processing
        process_inbound_message.apply_async(
            kwargs={"bot_id": str(bot.id), "msg_id": str(message.id)}
        )

        logger.info(f"Queued message {message.id} for processing")

    except Bot.DoesNotExist:
        logger.error(
            CeleryConfig.ERROR_MESSAGES["BOT_NOT_FOUND"].format(bot_id=bot_id)
        )
    except Exception as e:
        logger.error("Failed to handle inbound update", exc_info=True)


@celery.task(bind=True, max_retries=3)
def master_poller(self):
    """
    Poll for bots that are due for execution and queue them for processing.

    This task runs periodically to check if any scheduled bots need to be executed
    and updates their next scheduled run time.

    Returns:
        Status message
    """

    try:
        current_dt = timezone.now()

        # Check if Ollama is configured
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.warning(CeleryConfig.ERROR_MESSAGES["NO_OLLAMA"])
            return CeleryConfig.ERROR_MESSAGES["NO_OLLAMA"]

        # Find bots due for execution
        due_bots = Bot.objects.filter(
            is_active=True,
            telegram_chat_id__isnull=False,
            next_run_at__lte=current_dt
        )

        logger.info(f"Found {due_bots.count()} bots due for execution")

        # Queue each bot for processing
        for bot in due_bots:
            process_inbound_message.apply_async(
                kwargs={"bot_id": str(bot.id), "msg_id": None},
                eta=bot.next_run_at
            )

            # Update next run time
            bot.next_run_at = calculate_next_run_at(bot.interval_mins, bot.cron_expression)
            bot.last_run_at = current_dt

        # Bulk update
        if due_bots:
            Bot.objects.bulk_update(due_bots, fields=["next_run_at", "last_run_at"])

        logger.info("Master poller completed successfully")
        return "Processed due bots successfully"

    except Exception as e:
        logger.error("Master poller failed", exc_info=True)
        # Retry after 60 seconds
        raise self.retry(exc=e, countdown=60)


@celery.task(bind=True, max_retries=3)
def process_inbound_message(self, bot_id: str, msg_id: Optional[str] = None):
    """
    Process an inbound message through intent classification and tool execution.

    Args:
        bot_id: ID of the bot processing the message
        msg_id: ID of the message to process (optional for scheduled runs)

    Returns:
        Status message

    Raises:
        Retries on failure with exponential backoff
    """

    try:
        # Validate configuration
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.error(CeleryConfig.ERROR_MESSAGES["NO_OLLAMA"])
            return CeleryConfig.ERROR_MESSAGES["NO_OLLAMA"]

        # Get bot
        try:
            bot = Bot.objects.prefetch_related("messages", "mcp_servers").get(id=bot_id)
        except Bot.DoesNotExist:
            logger.error(
                CeleryConfig.ERROR_MESSAGES["BOT_NOT_FOUND"].format(bot_id=bot_id)
            )
            return CeleryConfig.ERROR_MESSAGES["BOT_NOT_FOUND"].format(bot_id=bot_id)

        # Initialize clients and processor
        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
        processor = BotMessageProcessor(bot, ollama, ollama_client)

        # If message ID provided, process specific message
        if msg_id:
            try:
                message = Message.objects.get(id=msg_id)
            except Message.DoesNotExist:
                logger.error(
                    CeleryConfig.ERROR_MESSAGES["MESSAGE_NOT_FOUND"].format(msg_id=msg_id)
                )
                return CeleryConfig.ERROR_MESSAGES["MESSAGE_NOT_FOUND"].format(msg_id=msg_id)

            # Process message
            result = processor.process_message(message)

            # Check if report was requested
            if result == "report_requested":
                generate_report.apply_async(kwargs={"bot_id": str(bot.id)})
                return "Report generation queued"

            # Send response
            processor.send_response(result)
        else:
            # Scheduled run without specific message
            logger.info(f"Scheduled run triggered for bot: {bot.name}")

        logger.info(f"Message processing completed for bot: {bot.name}")
        return "Processed inbound message successfully"

    except Exception as e:
        logger.error("Failed to process inbound message", exc_info=True)
        # Retry with exponential backoff: 60s, 300s, 900s
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


@celery.task(bind=True, max_retries=3)
def generate_report(self, bot_id: str):
    """
    Generate a report from bot conversation history and send to user.

    Args:
        bot_id: ID of the bot to generate report for

    Returns:
        Status message

    Raises:
        Retries on failure with exponential backoff
    """

    try:
        # Validate configuration
        ollama = OllamaConfigManager.get_ollama_config()
        if not ollama:
            logger.error(CeleryConfig.ERROR_MESSAGES["NO_OLLAMA"])
            return CeleryConfig.ERROR_MESSAGES["NO_OLLAMA"]

        # Get bot
        try:
            bot = Bot.objects.prefetch_related("messages").get(id=bot_id)
        except Bot.DoesNotExist:
            logger.error(
                CeleryConfig.ERROR_MESSAGES["BOT_NOT_FOUND"].format(bot_id=bot_id)
            )
            return CeleryConfig.ERROR_MESSAGES["BOT_NOT_FOUND"].format(bot_id=bot_id)

        # Initialize clients and service
        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
        generator = ReportGeneratorService(bot, ollama, ollama_client)

        # Generate and send report
        result = generator.generate_and_send()

        logger.info(f"Report generated for bot: {bot.name}")
        return result

    except Exception as e:
        logger.error("Failed to generate report", exc_info=True)
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))

