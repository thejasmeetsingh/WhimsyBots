"""Celery task definitions for async job processing"""

import logging
from typing import Optional

import redis
from django.utils import timezone
from django.conf import settings
from clients import OllamaClient

from app.models import Bot, Message
from app.utils import calculate_next_run_at
from whimsybots.celery import task as celery
from app.config import CeleryConfig
from app.managers import OllamaConfigManager, TelegramClientManager
from app.services import (
    BotMessageProcessor,
    ReportGeneratorService,
    TelegramUpdateHandler,
)


POLL_OFFSET_KEY = "telegram:poll_offset:{bot_id}"  # Redis key
POLL_LOCK_KEY   = "telegram:poll_lock:{bot_id}"    # Redis key for distributed lock

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=3)
def telegram_poller(self):
    """
    Polls Telegram for new messages and dispatches them.
      - Offset is persisted in Redis so it survives worker restarts.
      - A Redis lock ensures only one instance runs at a time across all workers.
    """

    try:
        r = redis.from_url(settings.CELERY_BROKER_URL)

        # Fetch active bots
        bots = Bot.objects.filter(is_active=True)

        for bot in bots:
            poll_lock_key = POLL_LOCK_KEY.format(bot_id=str(bot.id))
            poll_offset_key = POLL_OFFSET_KEY.format(bot_id=str(bot.id))

            # Distributed lock — 30s expiry (must exceed getUpdates timeout of 20s + buffer)
            # to auto-release if worker crashes
            lock = r.lock(poll_lock_key, timeout=30, blocking_timeout=0)
            if not lock.acquire(blocking=False):
                logger.debug("telegram_poller: another worker is already polling, skipping")
                return

            try:
                # Retreive offset from redis or 0
                offset = int(r.get(poll_offset_key) or 0)

                # Fetch messages for bot
                telegram_client = TelegramClientManager.create_client(bot)
                updates = telegram_client.get_updates(offset=offset + 1)

                logger.info(f"Received {len(updates)} updates for {bot.name} from telegram")

                for update in updates:
                    try:
                        TelegramUpdateHandler.handle_update(bot, update)
                        # Persist offset immediately after each successful dispatch
                        r.set(poll_offset_key, update["update_id"])
                    except Exception as e:
                        logger.exception(f"Error handling update {update.get('update_id')}: {e}")
                        # Don't update offset on failure — will retry this update next poll
                        break
            finally:
                if lock.owned():
                    lock.release()

    except Exception as e:
        logger.error("Telegram poller failed", exc_info=True)
        # Retry after 60 seconds
        raise self.retry(exc=e, countdown=60)


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

        # Queue each bot for processing on default worker
        for bot in due_bots:
            process_inbound_message.apply_async(
                queue="default",
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
            bot = Bot.objects.get(id=bot_id)
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
                generate_report.apply_async(
                    queue="default",
                    kwargs={"bot_id": str(bot.id)}
                )
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
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
