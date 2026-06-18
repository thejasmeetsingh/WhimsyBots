import logging
from typing import Optional

from django import forms
from django.core.cache import cache
from pydantic import BaseModel

from app.models import Bot, MCPServer, Ollama
from utils.crypto import get_token_hash
from clients import OllamaClient
from strings import UNIQUE_TELEGRAM_TOKEN_ERROR

logger = logging.getLogger(__name__)

# Form field configuration constants
EMPTY_MODEL_CHOICES = [(None, "Select a model")]

# Redis cache key prefix for model data
MODEL_DATA_CACHE_KEY = "ollama_models_data"
MODEL_DATA_CACHE_TIMEOUT = 3600  # 1 hour


class Model(BaseModel):
    model: str
    is_embedding: bool


def fetch_ollama_models_with_capabilities(
    endpoint: str, api_key: Optional[str]
) -> list[Model]:
    """
    Fetch available models from Ollama client with their capabilities.

    Args:
        endpoint: Ollama API endpoint URL
        api_key: Ollama API key for authentication

    Returns:
        List of dicts containing model name and an embedding model identifier

    Raises:
        Logs errors but returns empty list on any exception
    """

    try:
        result: list[Model] = []
        client = OllamaClient(endpoint=endpoint, api_key=api_key)
        models = client.list_models()

        for model in models:
            try:
                capabilities = client.fetch_model_capabilities(model)
                if "tools" in capabilities or "embedding" in capabilities:
                    result.append(
                        Model(model=model, is_embedding="embedding" in capabilities)
                    )
            except Exception as _:
                logger.info(
                    "Failed to fetch details for model %s",
                    model,
                )

        return result
    except Exception as _:
        logger.error(
            "Failed to fetch Ollama models from endpoint %s", endpoint, exc_info=True
        )
        return []


def get_models_from_cache() -> Optional[list[Model]]:
    """
    Retrieve models data from Redis cache.

    Returns:
        Cached models data or None if not found

    Raises:
        Logs errors but returns None on any exception
    """

    try:
        cached_data = cache.get(MODEL_DATA_CACHE_KEY)
        models = list(map(lambda _model: Model.model_validate(_model), cached_data))
        return models
    except Exception as _:
        logger.error("Failed to retrieve models from cache", exc_info=True)
        return None


def save_models_to_cache(
    models_data: list[Model],
) -> bool:
    """
    Save models data to Redis cache.

    Args:
        models_data: List of dicts

    Returns:
        True if saved successfully, False otherwise

    Raises:
        Logs errors but returns False on any exception
    """

    try:
        models = list(map(lambda _model: _model.model_dump(), models_data))
        cache.set(MODEL_DATA_CACHE_KEY, models, MODEL_DATA_CACHE_TIMEOUT)
        return True
    except Exception as _:
        logger.error("Failed to save models to cache", exc_info=True)
        return False


def get_filtered_models(
    models: list[Model], is_embedding: bool
) -> list[tuple[str, str]]:
    """
    Convert models data to choices format

    Args:
        models: Models data
        is_embedding: To tell what type of models to return

    Returns:
        list of tuples of model names
    """

    _models = [
        (model.model, model.model)
        for model in models
        if model.is_embedding == is_embedding
    ]

    return _models


class BotForm(forms.ModelForm):
    """
    Form for Bot configuration.
    Dynamically populates available models from the configured Ollama instance,
    with the default model prioritized in the list.
    """

    ollama_model = forms.ChoiceField(
        choices=EMPTY_MODEL_CHOICES,
        required=True,
        help_text="Support is limited to models with tool calling capabilities",
    )
    embedding_model = forms.ChoiceField(
        choices=EMPTY_MODEL_CHOICES,
        required=True,
        help_text="Model used for vector embeddings. Run: 'ollama pull nomic-embed-text' if no embedding model installed",
    )

    class Meta:
        model = Bot
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._populate_model_choices()

    def clean(self):
        """
        Validate form data with custom checks for unique telegram bot token.

        Ensures that the provided telegram_bot_token is unique across all Bot instances,
        excluding the current instance if it's being updated.

        Raises:
            ValidationError: If the telegram_bot_token already exists for another bot

        Returns:
            dict: The cleaned form data
        """

        cleaned_data = super().clean()
        telegram_bot_token = cleaned_data.get("telegram_bot_token")

        if telegram_bot_token:
            telegram_bot_token_hash = get_token_hash(telegram_bot_token)
            qs = Bot.objects.filter(
                telegram_bot_token_hash__exact=telegram_bot_token_hash
            )

            if self.instance:
                qs = qs.exclude(id=self.instance.id)

            if qs.exists():
                self.add_error("telegram_bot_token", UNIQUE_TELEGRAM_TOKEN_ERROR)

        return cleaned_data

    def _populate_model_choices(self) -> None:
        """
        Fetch and populate available Ollama models in the form field.
        Uses cache-first approach to avoid unnecessary API calls.
        Handles errors gracefully by leaving default empty choices if fetch fails.
        """

        ollama_obj = Ollama.objects.first()
        if not ollama_obj:
            return

        endpoint = getattr(ollama_obj, "endpoint", None)
        api_key = getattr(ollama_obj, "api_key", None)

        if not endpoint:
            return

        # Try to get models from cache first
        models = get_models_from_cache()
        if not models:
            models = fetch_ollama_models_with_capabilities(endpoint, api_key)
            if not models:
                return

            # Save models data into cache
            save_models_to_cache(models)

        self.fields["ollama_model"].choices = get_filtered_models(
            models, is_embedding=False
        )
        self.fields["embedding_model"].choices = get_filtered_models(
            models, is_embedding=True
        )


class MCPServerForm(forms.ModelForm):
    """
    Form for MCP Server configuration.
    Created to represent 'secrets' field as a valid JSONField.
    """

    secrets = forms.JSONField(required=False)

    class Meta:
        model = MCPServer
        fields = "__all__"
