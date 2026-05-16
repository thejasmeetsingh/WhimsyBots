import logging
from typing import Optional

from django import forms

from app.models import MCPServer, Ollama, Bot
from app.utils import get_token_hash
from clients import OllamaClient
from strings import UNIQUE_TELEGRAM_TOKEN_ERROR


logger = logging.getLogger(__name__)

# Form field configuration constants
DEFAULT_MODEL_HELP_TEXT = "Please select a model which supports tool calling"
EMPTY_MODEL_CHOICE = (None, "Select a model")
EMPTY_MODEL_CHOICES = [EMPTY_MODEL_CHOICE]


def fetch_ollama_models(
    endpoint: Optional[str] = None, api_key: Optional[str] = None
) -> list[str]:
    """
    Fetch available models from Ollama client.

    Args:
        endpoint: Ollama API endpoint URL
        api_key: Ollama API key for authentication

    Returns:
        List of available model names, empty list on error

    Raises:
        Logs errors but returns empty list on any exception
    """

    try:
        client = OllamaClient(endpoint=endpoint, api_key=api_key)
        return client.list_models()
    except Exception as _:
        logger.error(
            "Failed to fetch Ollama models from endpoint %s", endpoint, exc_info=True
        )
        return []


def format_model_choices(
    models: list[str], preferred_model: Optional[str] = None
) -> list[tuple[str, str]]:
    """
    Convert model list to form choice tuples, optionally sorting with preferred model first.

    Args:
        models: List of model names
        preferred_model: Optional model to prioritize in the list

    Returns:
        List of tuples suitable for form ChoiceField
    """

    # Convert models to choice tuples
    choices = [(model, model) for model in models]

    # Sort with preferred model first if specified
    if preferred_model and any(choice[0] == preferred_model for choice in choices):
        preferred = [choice for choice in choices if choice[0] == preferred_model]
        others = [choice for choice in choices if choice[0] != preferred_model]
        choices = preferred + others

    return choices


def get_model_field() -> forms.ChoiceField:
    """
    Factory function to create a standardized model selection field.

    Returns:
        ChoiceField configured for model selection
    """

    return forms.ChoiceField(
        choices=EMPTY_MODEL_CHOICES, required=False, help_text=DEFAULT_MODEL_HELP_TEXT
    )


class OllamaForm(forms.ModelForm):
    """
    Form for Ollama configuration.
    Dynamically populates available models from the configured Ollama instance.
    """

    default_model = get_model_field()

    class Meta:
        model = Ollama
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._populate_model_choices()

    def _populate_model_choices(self) -> None:
        """
        Fetch and populate available Ollama models in the form field.
        Handles errors gracefully by leaving default empty choices if fetch fails.
        """

        instance = self.instance
        if not instance or not instance.pk:
            return

        endpoint = getattr(instance, "endpoint", None)
        api_key = getattr(instance, "api_key", None)

        models = fetch_ollama_models(endpoint=endpoint, api_key=api_key)
        if models:
            self.fields["default_model"].choices = format_model_choices(models)


class BotForm(forms.ModelForm):
    """
    Form for Bot configuration.
    Dynamically populates available models from the configured Ollama instance,
    with the default model prioritized in the list.
    """

    ollama_model = get_model_field()

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
        Prioritizes the default model from Ollama configuration if available.
        Handles errors gracefully by leaving default empty choices if fetch fails.
        """

        ollama_obj = Ollama.objects.first()
        if not ollama_obj:
            return

        endpoint = getattr(ollama_obj, "endpoint", None)
        api_key = getattr(ollama_obj, "api_key", None)
        default_model = getattr(ollama_obj, "default_model", None)

        models = fetch_ollama_models(endpoint=endpoint, api_key=api_key)
        if models:
            self.fields["ollama_model"].choices = format_model_choices(
                models, preferred_model=default_model
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
