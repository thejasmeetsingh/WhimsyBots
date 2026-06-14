"""Telegram client manager"""

from app.models import Bot


class TelegramClientManager:
    """Manages Telegram client instantiation"""

    @staticmethod
    def create_client(bot: Bot):
        """
        Create a TelegramClient for the given bot.

        Args:
            bot: Bot instance with telegram_bot_token and telegram_chat_id

        Returns:
            Configured TelegramClient instance
        """

        from clients import TelegramClient

        return TelegramClient(bot.telegram_bot_token, bot.telegram_chat_id)
