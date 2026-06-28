"""Bot message processor service."""

import asyncio
import logging
from typing import Optional

from pgvector.django import CosineDistance
from pydantic import ValidationError

from app.choices import MessageRole
from app.models import Bot, CronJob, MCPServer, Message, Ollama
from clients import OllamaClient
from managers import TelegramClientManager
from prompts import CRON_JOB_PROMPT
from services.context_assembler import ContextAssembler
from services.tool_calling_coordinator import run_tool_calling_loop
from services.tool_executor import MCPToolConfig, MCPToolsBuilder

logger = logging.getLogger(__name__)


class BotMessageProcessor:
    """Service for processing bot messages with Ollama and tools."""

    def __init__(self, bot: Bot, ollama: Ollama, ollama_client: OllamaClient):
        """Initialize the processor.

        Args:
            bot: Bot instance
            ollama: Ollama configuration
            ollama_client: Configured OllamaClient
        """
        self.bot = bot
        self.ollama = ollama
        self.ollama_client = ollama_client
        self.telegram_client = TelegramClientManager.create_client(bot)

    def _get_tools_config(
        self,
        query_vector: Optional[list[float]] = None,
        servers_to_exclude: Optional[set[str]] = None,
    ) -> list[MCPToolConfig]:
        """Build the list of MCP tool configurations available to the bot.

        Starts from MCP servers scoped to the bot, optionally re-ranks them
        by semantic similarity to 'query_vector' (closest descriptions first),
        then appends the global default MCP servers. Any servers whose names
        appear in 'servers_to_exclude' are filtered out before the tools are
        built.

        Args:
            query_vector: Optional embedding used to order bot-scoped MCP
                servers by cosine distance against their stored
                'tools_description_embedding' (most similar first). When
                'None', servers preserve the default queryset ordering.
            servers_to_exclude: Optional set of MCP server names to omit from
                the final list (e.g. "cron_job" for cron-driven runs).

        Returns:
            The list of 'MCPToolConfig' objects built from the selected
            servers, in the same order used for tool selection.
        """
        # Build tools from servers
        mcp_servers = MCPServer.objects.filter(bot_id=self.bot.id, is_active=True)

        if query_vector is not None:
            mcp_servers = mcp_servers.annotate(
                distance=CosineDistance("tools_description_embedding", query_vector)
            ).order_by("distance")

        mcp_server_list = list(mcp_servers)

        # Add default mcp server's to the 'mcp_servers' list
        default_servers = MCPServer.get_default_mcp_servers()
        mcp_server_list.extend(list(default_servers.values()))

        if servers_to_exclude:
            mcp_server_list = list(
                filter(
                    lambda mcp_server: mcp_server.name not in servers_to_exclude,
                    mcp_server_list,
                )
            )

        tools_config = asyncio.run(MCPToolsBuilder.build_tools_from_servers(mcp_server_list))

        return tools_config

    def process_message(self, message: Message) -> tuple[str, Optional[int]]:
        """Process message with tool calling loop.

        Args:
            message (Message): Latest user message object

        Returns:
            response: LLM Response
            ollama_ms: total duration taken by ollama
        """
        try:
            context_assembler_svc = ContextAssembler(
                bot=self.bot, ollama=self.ollama, current_message=message
            )

            tools_config = self._get_tools_config(query_vector=message.content_embedding)

            context = context_assembler_svc.assemble(
                tool_definitions=[tool_config.tool for tool_config in tools_config]
            )

            top_tools_config = tools_config[: context.budget.recommended_tool_count]

            # Send typing indicator (responsive UX)
            self.telegram_client.send_typing_action()

            # Run tool calling loop
            response, ollama_ms = run_tool_calling_loop(
                ollama_client=self.ollama_client,
                model=self.bot.ollama_model,
                history=context.history,
                tools_config=top_tools_config,
                ollama=self.ollama,
                add_keep_alive=True,
            )

            return response, ollama_ms

        except ValidationError as _:
            logger.error("Invalid response returned from the bot", exc_info=True)
            raise
        except Exception as _:
            logger.error("Failed to process message with tools", exc_info=True)
            raise

    def process_cron_job(self, cron_job: CronJob) -> tuple[str, Optional[int]]:
        """Process cron job with tool calling loop.

        Args:
            cron_job (CronJob): CronJob instance

        Returns:
            response: LLM Response
            ollama_ms: total duration taken by ollama
        """
        try:
            # Remove 'cron_job' MCP server
            tools_config = self._get_tools_config(
                query_vector=cron_job.schedule_embedding,
                servers_to_exclude={"cron_job"},
            )

            # Send typing indicator (responsive UX)
            self.telegram_client.send_typing_action()

            # Run tool calling loop
            response, ollama_ms = run_tool_calling_loop(
                ollama_client=self.ollama_client,
                model=self.bot.ollama_model,
                history=[
                    {
                        "role": "user",
                        "content": CRON_JOB_PROMPT.format(
                            name=cron_job.name,
                            description=cron_job.description,
                            bot_id=str(self.bot.id),
                        ),
                    }
                ],
                tools_config=tools_config,
                ollama=self.ollama,
            )

            return response, ollama_ms

        except ValidationError as _:
            logger.error("Invalid response returned from the bot", exc_info=True)
            raise
        except Exception as _:
            logger.error("Failed to process cron job activity", exc_info=True)
            raise

    def send_response(self, response: str) -> None:
        """Send response to user and save to database.

        Args:
            response: The response text to send
        """
        try:
            self.telegram_client.send_message(text=response)

            Message.objects.create(
                bot=self.bot, role=MessageRole.ASSISTANT.value[0], content=response
            )
        except Exception as _:
            logger.error("Failed to send response", exc_info=True)
            raise
