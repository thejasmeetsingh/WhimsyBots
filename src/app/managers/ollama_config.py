"""Ollama configuration manager"""

from typing import Optional

from app.models import Ollama


class OllamaConfigManager:
    """Manages Ollama configuration retrieval and caching"""

    _cached_ollama: Optional[Ollama] = None

    @classmethod
    def get_ollama_config(cls) -> Optional[Ollama]:
        """
        Get Ollama configuration from database with caching.

        Returns:
            Ollama instance or None if not configured
        """

        if cls._cached_ollama is None:
            cls._cached_ollama = Ollama.objects.first()
        return cls._cached_ollama

    @classmethod
    def get_model(cls, bot, ollama: Ollama) -> str:
        """
        Get the model to use, preferring bot's model over default.

        Args:
            bot: Bot instance
            ollama: Ollama configuration

        Returns:
            Model name to use
        """

        return bot.ollama_model if bot.ollama_model else ollama.default_model
