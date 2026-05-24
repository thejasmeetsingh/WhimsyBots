"""
EmbeddingService — generates and queries vector embeddings for messages.

Flow:
  1. After a user message is saved and response is sent, generate_embedding
     task calls save_message_embedding() to store the vector on the message.
  2. At the start of each process_inbound_message cycle, get_relevant_memories()
     queries the pgvector index for the top-k most semantically similar past
     user messages and returns them ranked by similarity for ContextAssembler.

Embedding model is configured per Ollama instance via ollama_config.embedding_model
(e.g. "nomic-embed-text", "mxbai-embed-large", "all-minilm").
Dimensions must match ollama_config.embedding_dimensions (e.g. 768, 1024, 384).

Note: Embedding generation delegates to OllamaClient.embed() — no direct
HTTP calls here.
"""

from __future__ import annotations
import logging

from pgvector.django import CosineDistance

from app.models import Bot, Message, Ollama
from app.choices import MessageRole
from clients.ollama import OllamaClient


logger = logging.getLogger(__name__)


class EmbeddingService:
    @classmethod
    def save_message_embedding(
        cls, message: "Message", ollama_config: "Ollama"
    ) -> None:
        """
        Generates a vector embedding for message.content and saves it to
        message.content_embedding. Skips silently if the embedding model
        is not configured or the content is empty.

        Called by the generate_embedding Celery task — always runs after
        the response has been sent, never on the critical path.
        """

        if not message.content or not message.content.strip():
            logger.debug("Skipping embedding for empty message %s", message.id)
            return

        if not ollama_config.embedding_model:
            logger.warning(
                "No embedding model configured on Ollama config — skipping embedding "
                "for message %s. Set embedding_model in the admin panel.",
                message.id,
            )
            return

        vector = cls._generate(message.content, ollama_config)
        if vector is None:
            return

        message.content_embedding = vector
        message.save(update_fields=["content_embedding", "updated_at"])

        logger.info(
            "Embedding saved for message %s (dimensions=%d)",
            message.id,
            len(vector),
        )

    @classmethod
    def get_relevant_memories(
        cls,
        query_text: str,
        bot: "Bot",
        current_message_id: str,
        top_k: int,
        ollama_config: "Ollama",
    ) -> list[tuple["Message", float]]:
        """
        Finds the top-k past USER messages most semantically similar to
        query_text, using pgvector cosine distance on content_embedding.

        Returns a list of (Message, similarity_score) tuples sorted by
        similarity descending (most relevant first). similarity_score is
        in range [0, 1] where 1.0 = identical.

        Returns [] if:
          - Embedding model is not configured
          - Query embedding generation fails
          - No embedded messages exist for this bot yet
        """

        if not bot.embedding_model:
            logger.debug("No embedding model configured — skipping memory retrieval")
            return []

        query_vector = cls._generate(query_text, bot, ollama_config)
        if query_vector is None:
            return []

        return cls._query_similar(
            query_vector=query_vector,
            bot_id=bot.id,
            current_message_id=current_message_id,
            top_k=top_k,
        )

    @classmethod
    def _generate(
        cls, text: str, bot: "Bot", ollama_config: "Ollama"
    ) -> list[float] | None:
        """
        Delegates to OllamaClient.embed(). Returns the vector or None on failure.
        """

        try:
            client = OllamaClient(ollama_config.endpoint, api_key=ollama_config.api_key)

            return client.generate_embeddings(
                model=bot.embedding_model,
                text=text,
                truncate=True,
                dimensions=bot.embedding_dimensions,
            )
        except Exception as exc:
            logger.exception("Embedding generation failed: %s", exc)
            return None

    @staticmethod
    def _query_similar(
        query_vector: list[float],
        bot_id: str,
        current_message_id: str,
        top_k: int,
    ) -> list[tuple["Message", float]]:
        """
        Queries the Message table using pgvector's cosine distance operator (<=>).

        Cosine distance is in range [0, 2] where 0 = identical direction.
        We convert to similarity score [0, 1] as: similarity = 1 - (distance / 2)

        Only USER messages with a saved embedding are considered.
        The current message is excluded (it was just saved, hasn't been
        embedded yet, and is already the query itself).
        """

        try:
            results = (
                Message.objects.filter(
                    bot_id=bot_id,
                    role=MessageRole.USER.value[0],
                    content_embedding__isnull=False,
                )
                .exclude(id=current_message_id)
                .annotate(distance=CosineDistance("content_embedding", query_vector))
                .order_by("distance")[:top_k]
            )

            return [(msg, round(1 - (msg.distance / 2), 4)) for msg in results]

        except Exception as exc:
            logger.exception("pgvector similarity query failed: %s", exc)
            return []
