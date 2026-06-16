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

import logging

from pgvector.django import CosineDistance
from django.utils import timezone

from app.choices import MessageRole
from app.models import Bot, Message, Ollama, CronJob
from clients.ollama import OllamaClient

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, bot: Bot, ollama: Ollama):
        """
        Initialize the embedding service.

        Args:
            bot: Bot instance
            ollama: Ollama configuration
        """

        self.bot = bot
        self.ollama = ollama

    def save_message_embedding(self, message: Message) -> None:
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

        if not self.bot.embedding_model:
            logger.warning(
                "No embedding model configured on Ollama config — skipping embedding "
                "for message %s. Set embedding_model in the admin panel.",
                message.id,
            )
            return

        content_vector = self._generate(message.content)
        if content_vector is None:
            return

        message.content_embedding = content_vector
        message.save(update_fields=["content_embedding", "updated_at"])

        logger.info(
            "Embedding saved for message %s (dimensions=%d)",
            message.id,
            len(content_vector),
        )

    def save_cron_job_embedding(self, cron_job: CronJob) -> None:
        """
        Generates a vector embedding for the cron job's name + description
        and saves it to 'schedule_embedding' (along with a fresh
        'schedule_embedding_updated_at' timestamp). Skips silently if the
        embedding model is not configured or vector generation fails.
        """

        if not self.bot.embedding_model:
            logger.warning(
                "No embedding model configured on Ollama config — skipping embedding "
                "for cron job %s. Set embedding_model in the admin panel.",
                cron_job.id,
            )
            return

        text_to_embed = f"{cron_job.name}. {cron_job.description}"
        cron_job_vector = self._generate(text_to_embed)
        if not cron_job_vector:
            return

        cron_job.schedule_embedding = cron_job_vector
        cron_job.schedule_embedding_updated_at = timezone.now()
        cron_job.save(
            update_fields=[
                "schedule_embedding",
                "schedule_embedding_updated_at",
            ]
        )

        logger.info(
            "Embedding saved for cron_job %s (dimensions=%d)",
            cron_job.id,
            len(cron_job_vector),
        )

    def get_relevant_memories(
        self,
        query_text: str,
        current_message_id: str,
        top_k: int,
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

        if not self.bot.embedding_model:
            logger.debug("No embedding model configured — skipping memory retrieval")
            return []

        query_vector = self._generate(query_text)
        if query_vector is None:
            return []

        return self._query_similar(
            query_vector=query_vector,
            current_message_id=current_message_id,
            top_k=top_k,
        )

    def _generate(
        self,
        text: str,
    ) -> list[float] | None:
        """
        Delegates to OllamaClient.embed(). Returns the vector or None on failure.
        """

        client = OllamaClient(
            endpoint=self.ollama.endpoint, api_key=self.ollama.api_key
        )

        try:
            return client.generate_embeddings(
                model=self.bot.embedding_model,
                text=text,
                truncate=True,
                dimensions=self.bot.embedding_dimensions,
            )
        except Exception as exc:
            logger.exception("Embedding generation failed: %s", exc)
            return None

    def _query_similar(
        self,
        query_vector: list[float],
        current_message_id: str,
        top_k: int,
    ) -> list[tuple[Message, float]]:
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
                    bot_id=self.bot.id,
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
