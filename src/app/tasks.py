"""Celery task definitions for async job processing"""

import logging

from django.utils import timezone
from django.conf import settings
from app.choices import MessageIntentType
from clients import OllamaClient

from app.models import Bot, CronJob, Message
from app.utils import calculate_next_run_at
from whimsybots.celery import task as celery
from app.config import CeleryConfig
from app.managers import OllamaConfigManager, TelegramClientManager
from app.services import (
    BotMessageProcessor,
    ReportGeneratorService,
    TelegramUpdateHandler,
)

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=3)
def telegram_msg_handler(self, bot_token: str, update: dict):
    """
    Fetch bot object from given bot token and processs the given update from telegram
    """

    try:
        # Fetch active bots
        bot = Bot.objects.get(telegram_bot_token=bot_token)
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
            logger.warning(CeleryConfig.ERROR_MESSAGES["NO_OLLAMA"])
            return CeleryConfig.ERROR_MESSAGES["NO_OLLAMA"]

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
            process_inbound_message.apply_async(
                queue="default",
                kwargs={"bot_id": str(job.bot_id), "msg_id": None},
                eta=job.next_run_at,
            )

            # Update next run time
            job.next_run_at = calculate_next_run_at(job.cron_expression)
            job.last_run_at = current_dt

        # Bulk update
        if due_jobs:
            CronJob.objects.bulk_update(due_jobs, fields=["next_run_at", "last_run_at"])

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
        webhook_url = f"{settings.WEBHOOK_BASE_URL}/webhook/{bot.telegram_bot_token}/"

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
def process_inbound_message(self, bot_id: str, msg_id: str):
    """
    Process an inbound message through intent classification and tool execution.

    Args:
        bot_id: ID of the bot processing the message
        msg_id: ID of the message to process

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
            bot = Bot.objects.get(id=bot_id)
        except Bot.DoesNotExist:
            logger.error(
                CeleryConfig.ERROR_MESSAGES["BOT_NOT_FOUND"].format(bot_id=bot_id)
            )
            return CeleryConfig.ERROR_MESSAGES["BOT_NOT_FOUND"].format(bot_id=bot_id)

        # Initialize clients and processor
        ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
        processor = BotMessageProcessor(bot, ollama, ollama_client)

        # Process message
        result = processor.process_message()

        if result:
            # Kick-off intent classification process
            classify_intent.apply_async(
                queue="default",
                kwargs={
                    "bot_id": bot_id,
                    "msg_id": msg_id,
                    "intent": result.intent,
                },
            )
            logger.info("Intent classify process queued")

            # Send response
            processor.send_response(result.response)

        logger.info(f"Message processing completed for bot: {bot.name}")
        return "Processed inbound message successfully"

    except Exception as e:
        logger.error("Failed to process inbound message", exc_info=True)
        # Retry with exponential backoff: 60s, 300s, 900s
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@celery.task(bind=True, max_retries=3)
def classify_intent(self, bot_id: str, msg_id: str, intent: str):
    """
    Classify a message's intent and trigger report generation if needed.

    Updates the message record with the classified intent and queues
    report generation if the intent is REPORT.

    Args:
        bot_id (str): ID of the bot processing the message
        msg_id (str): ID of the message to classify
        intent (str): Classified intent label (e.g., 'R' for REPORT)

    Returns:
        str: Success or error message

    Raises:
        celery.exceptions.MaxRetriesExceededError: If classification fails after all retries
    """

    try:
        try:
            message = Message.objects.get(id=msg_id)
            message.intent = intent
            message.save(update_fields=["intent"])

        except Message.DoesNotExist:
            logger.error(
                CeleryConfig.ERROR_MESSAGES["MESSAGE_NOT_FOUND"].format(msg_id=msg_id)
            )
            return CeleryConfig.ERROR_MESSAGES["MESSAGE_NOT_FOUND"].format(
                msg_id=msg_id
            )

        if intent == MessageIntentType.REPORT.value[0]:
            generate_report.apply_async(queue="default", kwargs={"bot_id": bot_id})
            logger.info("Report generation queued")

        return "Message classified successfully"

    except Exception as e:
        logger.error("Failed to classify message", exc_info=True)
        # Retry with exponential backoff: 60s, 300s, 900s
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


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
            bot = Bot.objects.get(id=bot_id)
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
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))
