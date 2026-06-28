"""EmbeddingService — generates and queries vector embeddings for messages.

Flow:
  1. After a user message is saved and response is sent, generate_embedding
     task calls 'save_message_embedding()' to store the vector on the message.
  2. At the start of each 'process_inbound_message' cycle, 'get_relevant_memories()'
     queries the pgvector index for the 'top-k' most semantically similar past
     user messages and returns them ranked by similarity for 'ContextAssembler'.

Embedding model is configured per 'Ollama' instance via 'ollama_config.embedding_model'
(e.g. "nomic-embed-text", "mxbai-embed-large", "all-minilm").
Dimensions must match ollama_config.embedding_dimensions (e.g. 768, 1024, 384).

Note: Embedding generation delegates to OllamaClient.embed() — no direct HTTP calls here.
"""

import asyncio
import logging

from django.utils import timezone
from pgvector.django import CosineDistance

from app.choices import MessageRole
from app.models import Bot, CronJob, MCPServer, Message, Ollama
from clients.ollama import OllamaClient
from services.tool_executor import MCPToolsBuilder

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Generate and query vector embeddings for messages, cron jobs, and MCP servers."""

    def __init__(self, bot: Bot, ollama: Ollama):
        """Initialize the embedding service.

        Args:
            bot: Bot instance.
            ollama: Ollama configuration.
        """
        self.bot = bot
        self.ollama = ollama

    @staticmethod
    def _build_text_for_cron_job(cron_job: CronJob) -> str:
        """Build searchable text from cron job metadata."""
        return f"{cron_job.name}. {cron_job.description}"

    @staticmethod
    def _build_text_for_mcp_server(mcp_servers: list[MCPServer]) -> str | None:
        """Build tools description string for MCP server embedding.

        Returns None if no servers provided or no tools found.
        """
        if not mcp_servers:
            return None

        tools_detail: list[str] = []

        for mcp_server in mcp_servers:
            try:
                tools_config = asyncio.run(
                    MCPToolsBuilder.build_tools_from_servers(mcp_servers=[mcp_server])
                )
            except Exception:
                logger.exception("Failed to build tools from MCPServer %s", mcp_server.id)
                continue

            for tool_config in tools_config:
                try:
                    tool_name = tool_config.tool["function"]["name"]
                    tool_description = tool_config.tool["function"]["description"]
                    tools_detail.append(f"{tool_name}: {tool_description}")

                except (KeyError, TypeError) as exc:
                    logger.warning(
                        "Skipping malformed tool config for MCPServer %s: %s",
                        mcp_server.id,
                        exc,
                    )

        if not tools_detail:
            return None

        return ".\n".join(tools_detail)

    def _generate(self, text: str) -> list[float] | None:
        """Generate vector embedding for the given text."""
        client = OllamaClient(endpoint=self.ollama.endpoint, api_key=self.ollama.api_key)

        try:
            return client.generate_embeddings(
                model=self.bot.embedding_model,
                text=text,
                truncate=True,
                dimensions=self.bot.embedding_dimensions,
            )

        except Exception:
            logger.exception(
                "Embedding generation failed for text (truncated): %s...",
                text[:100] if len(text) > 100 else text,
            )
            return None

    def save_message_embedding(self, message: Message) -> bool:
        """Generate and store embedding for a user message.

        Returns True on success, False if skipped or failed.
        Called by the generate_embedding Celery task — always runs after
        response sent, never on critical path.
        """
        # Skip empty content early with debug logging
        if not message.content or not message.content.strip():
            logger.debug("Skipping embedding for empty message %s", message.id)
            return False

        # Check model configuration once at class level
        if not self.bot.embedding_model:
            logger.warning(
                "No embedding model configured — skipping embeddings. "
                "Set embedding_model in the admin panel.",
            )
            return False

        content_vector = self._generate(message.content)

        # Handle generation failure gracefully
        if content_vector is None:
            logger.error("Failed to generate embedding for message %s", message.id)
            return False

        try:
            message.content_embedding = content_vector
            message.save(update_fields=["content_embedding", "updated_at"])

            logger.info(
                "Embedding saved for message %s (dimensions=%d)",
                message.id,
                len(content_vector),
            )
            return True

        except Exception:
            logger.exception("Failed to save embedding for message %s", message.id)
            return False

    def save_cron_job_embedding(self, cron_job: CronJob) -> bool:
        """Generate and store embedding for a cron job schedule."""
        if not self.bot.embedding_model:
            logger.warning(
                "No embedding model configured — skipping embeddings. "
                "Set embedding_model in the admin panel.",
            )
            return False

        text_to_embed = self._build_text_for_cron_job(cron_job)

        cron_job_vector = self._generate(text_to_embed)

        if not cron_job_vector:
            logger.error("Failed to generate embedding for cron job %s", cron_job.id)
            return False

        try:
            cron_job.schedule_embedding = cron_job_vector
            cron_job.schedule_embedding_updated_at = timezone.now()
            cron_job.save(
                update_fields=[
                    "schedule_embedding",
                    "schedule_embedding_updated_at",
                ]
            )

            logger.info(
                "Embedding saved for cron job %s (dimensions=%d)",
                cron_job.id,
                len(cron_job_vector),
            )
            return True

        except Exception:
            logger.exception("Failed to save embedding for cron job %s", cron_job.id)
            return False

    def save_mcp_embedding(self, mcp_server: MCPServer) -> bool:
        """Generate and store tools description embedding for an MCP server."""
        if not self.bot.embedding_model:
            logger.warning(
                "No embedding model configured — skipping embeddings. "
                "Set embedding_model in the admin panel.",
            )
            return False

        text_to_embed = self._build_text_for_mcp_server([mcp_server])

        if not text_to_embed:
            logger.error("Failed to build tools description for MCPServer %s", mcp_server.id)
            return False

        mcp_server_vector = self._generate(text_to_embed)

        if not mcp_server_vector:
            logger.error("Failed to generate embedding for MCPServer %s", mcp_server.id)
            return False

        try:
            mcp_server.tools_description_embedding = mcp_server_vector
            mcp_server.save(update_fields=["tools_description_embedding"])

            logger.info(
                "Embedding saved for MCPServer %s (dimensions=%d)",
                mcp_server.id,
                len(mcp_server_vector),
            )
            return True

        except Exception:
            logger.exception("Failed to save embedding for MCPServer %s", mcp_server.id)
            return False

    def get_relevant_memories(
        self,
        query_vector: list[float],
        current_message_id: str | None = None,
        top_k: int = 10,
    ) -> list[tuple[Message, float]]:
        """Query for semantically similar user messages using cosine similarity.

        Args:
            query_vector: The embedding vector to search against
            current_message_id: ID of the message that triggered this (excluded from results)
            top_k: Maximum number of results to return

        Returns:
            List of tuples (Message, similarity_score) sorted by relevance.

        Note: Only USER messages with saved embeddings are considered.
              The query itself is excluded if current_message_id is provided.
        """
        # Validate inputs early
        if query_vector is None or (len(query_vector) != self.bot.embedding_dimensions):
            logger.warning(
                "Invalid query vector dimensions (%d vs expected %d)",
                len(query_vector),
                self.bot.embedding_dimensions,
            )
            return []

        try:
            results = (
                Message.objects.filter(
                    bot_id=self.bot.id,
                    role=MessageRole.USER.value[0],
                    content_embedding__isnull=False,
                ).exclude(id=current_message_id)
                if current_message_id
                else None
            ) or Message.objects.filter(
                bot_id=self.bot.id,
                role=MessageRole.USER.value[0],
                content_embedding__isnull=False,
            )

            results = results.annotate(
                distance=CosineDistance("content_embedding", query_vector)
            ).order_by("distance")[:top_k]

            # Convert distance to similarity: 1 - (distance / max_distance)
            # Cosine distance range is [0, 2], so we normalize by dividing by 2
            return [(msg, round(1 - (msg.distance / 2), 4)) for msg in results]

        except Exception:
            logger.exception("pgvector similarity query failed")
            return []
