"""Ollama configuration manager."""

from typing import Optional

from app.models import Ollama


class OllamaConfigManager:
    """Manages Ollama configuration retrieval and caching."""

    _cached_ollama: Optional[Ollama] = None

    @classmethod
    def get_ollama_config(cls) -> Optional[Ollama]:
        """Get Ollama configuration from database with caching.

        Returns:
            Ollama instance or None if not configured
        """
        if cls._cached_ollama is None:
            cls._cached_ollama = Ollama.objects.first()
        # Refresh the cached model from the database so we always return the latest
        # persisted Ollama settings even when the cached object already exists.
        cls._cached_ollama.refresh_from_db()
        return cls._cached_ollama
