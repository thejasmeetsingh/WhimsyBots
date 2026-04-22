"""Message intent classification service"""

import logging
from typing import Optional

from clients import OllamaClient
from app.choices import MessageIntentType, MessageRole
from app.config import CeleryConfig


logger = logging.getLogger(__name__)


class MessageIntentClassifier:
    """Service for classifying message intent using Ollama"""

    def __init__(self, ollama_client: OllamaClient, model: str):
        """
        Initialize the classifier.

        Args:
            ollama_client: Configured OllamaClient instance
            model: Model name to use for classification
        """

        self.ollama_client = ollama_client
        self.model = model

    def classify(self, message_content: str) -> str:
        """
        Classify a message into an intent category.

        Args:
            message_content: The message text to classify

        Returns:
            Intent label (J, R, Q, or O)

        Raises:
            Exception: If classification fails
        """

        try:
            response = self.ollama_client.chat(
                model=self.model,
                messages=[{
                    "role": MessageRole.USER.value[1].lower(),
                    "content": CeleryConfig.INTENT_CLASSIFICATION_PROMPT.format(message=message_content)
                }],
                format={
                    "type": "string",
                    "properties": {"intent": {"type": "string"}},
                    "required": ["intent"]
                },
            )

            intent = response.get("message", "").strip()
            if not intent:
                logger.warning("Empty intent response from Ollama")
                return MessageIntentType.OTHER.value[0]

            return intent
        except Exception as e:
            logger.error(
                "Failed to classify message intent for content: %s",
                message_content[:100],
                exc_info=True
            )
            raise
