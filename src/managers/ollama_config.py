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
