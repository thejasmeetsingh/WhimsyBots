"""Manager classes.

Caches and factories used by the service layer: cached retrieval of the
Ollama configuration and factory methods for instantiating Telegram
clients bound to a specific bot.
"""

from managers.ollama_config import OllamaConfigManager
from managers.telegram_client import TelegramClientManager

__all__ = ("OllamaConfigManager", "TelegramClientManager")
