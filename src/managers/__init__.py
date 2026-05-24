"""Managers package"""

from managers.ollama_config import OllamaConfigManager
from managers.telegram_client import TelegramClientManager

__all__ = ("OllamaConfigManager", "TelegramClientManager")
