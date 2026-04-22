"""Managers package"""

from app.managers.ollama_config import OllamaConfigManager
from app.managers.telegram_client import TelegramClientManager

__all__ = ("OllamaConfigManager", "TelegramClientManager")
