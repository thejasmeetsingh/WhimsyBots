"""Telegram update handler service for processing incoming messages.

Handles parsing Telegram updates, creating message records, and queuing
for processing. Separated from celery.py to maintain clean separation
of concerns between task definitions and business logic.
"""

import logging
from typing import Any, Dict

from app.choices import MessageRole
from app.models import Bot, Message
from managers import TelegramClientManager
from utils.telegram import parse_telegram_update

logger = logging.getLogger(__name__)


class TelegramUpdateHandler:
    """Service for handling incoming Telegram updates.

    Processes Telegram webhook/polling updates by:
    1. Parsing the update JSON
    2. Looking up the bot
    3. Creating message records
    4. Queuing for async processing
    5. Sending typing indicator to user

    This keeps the bot responsive while async workers handle the heavy
    intent classification and tool calling.

    Example:
        >>> from app.celery import process_inbound_message
        >>> TelegramUpdateHandler.handle_update(
        ...     bot=Bot(...),
        ...     update={'update_id': 123, 'message': {...}}
        ... )
        >>> # Message is now queued for processing
    """

    @staticmethod
    def handle_update(bot: Bot, update: Dict[str, Any]) -> None:
        """Handle a single incoming Telegram update.

        Parses the update, saves the message to database, sends a typing
        indicator to the user, and queues the message for processing
        via the process_inbound_message Celery task.

        This is a blocking operation that should complete quickly. The
        actual message processing (intent classification, tool execution)
        happens asynchronously in the Celery task.

        Args:
            bot (Bot): Bot model instance
            update (dict): Telegram update object from webhook/polling
                Format: {
                    'update_id': int,
                    'message': {
                        'message_id': int,
                        'chat': {'id': int, ...},
                        'text': str,
                        ...
                    }
                }

        Returns:
            None

        Raises:
            Exception: Any database or Telegram API errors (logged)

        Side Effects:
            - Creates Message record in database
            - Updates Bot.telegram_chat_id if not set
            - Sends typing indicator to Telegram
            - Queues process_inbound_message task

        Example:
            >>> # From telegram_poller task
            >>> for update in updates:
            ...     try:
            ...         TelegramUpdateHandler.handle_update(bot, update)
            ...         r.set(poll_offset_key, update['update_id'])
            ...     except Exception as _:
            ...         logger.exception(f"Error handling update: {e}")
            ...         break
        """
        # Import here to avoid circular dependencies (tasks.py imports this service)
        from app.tasks import generate_embedding

        try:
            # Parse Telegram update
            parsed = parse_telegram_update(update)
            if not parsed:
                logger.warning("Failed to parse Telegram update")
                return

            chat_id = parsed["chat_id"]
            text = parsed["text"]

            # Update chat_id if not already set (first time receiving message)
            if not bot.telegram_chat_id:
                bot.telegram_chat_id = str(chat_id)
                bot.save(update_fields=["telegram_chat_id"])
                logger.debug(f"Updated bot {bot.id} with chat_id {chat_id}")

            # Create message record
            message = Message.objects.create(
                bot=bot,
                role=MessageRole.USER.value[0],
                content=text,
            )
            logger.debug(f"Created message {message.id} for bot {bot.id}")

            # Send typing indicator (responsive UX)
            telegram_client = TelegramClientManager.create_client(bot)
            telegram_client.send_typing_action()

            # Generate embedding for user's message
            generate_embedding.apply_async(
                queue="default",
                kwargs={"message_id": str(message.id)},
            )

            logger.info(f"Queued message {message.id} for processing")

        except Exception as _:
            logger.error("Failed to handle inbound update", exc_info=True)
