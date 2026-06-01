"""Bot message processor service"""

import asyncio
import logging
from typing import Optional

from pydantic import ValidationError

from app.choices import MessageRole
from app.models import Bot, MCPServer, Message, Ollama
from clients import OllamaClient
from managers import TelegramClientManager
from prompts import CRON_JOB_PROMPT
from services.context_assembler import ContextAssembler
from services.tool_calling_coordinator import run_tool_calling_loop
from services.tool_executor import MCPToolsBuilder

logger = logging.getLogger(__name__)


class BotMessageProcessor:
    """Service for processing bot messages with Ollama and tools"""

    def __init__(self, bot: Bot, ollama: Ollama, ollama_client: OllamaClient):
        """
        Initialize the processor.

        Args:
            bot: Bot instance
            ollama: Ollama configuration
            ollama_client: Configured OllamaClient
        """

        self.bot = bot
        self.ollama = ollama
        self.ollama_client = ollama_client
        self.telegram_client = TelegramClientManager.create_client(bot)

    def process_message(self, message: Message) -> tuple[str, Optional[int]]:
        """
        Process message with tool calling loop.

        Args:
            message (Message): Latest user message object

        Returns:
            response: LLM Response
            ollama_ms: total duration taken by ollama
        """

        try:
            # Build tools from servers
            mcp_servers = list(
                MCPServer.objects.filter(bot_id=self.bot.id, is_active=True)
            )

            # Add default mcp server's to the 'mcp_servers' list
            default_servers = MCPServer.get_default_mcp_servers()
            mcp_servers.extend(list(default_servers.values()))

            tools_config = asyncio.run(
                MCPToolsBuilder.build_tools_from_servers(mcp_servers)
            )

            context_assembler_svc = ContextAssembler(
                bot=self.bot, ollama=self.ollama, current_message=message
            )
            context = context_assembler_svc.assemble(
                tool_definitions=[tool.tool for tool in tools_config],
                active_mcp_server_names=list(default_servers.keys()),
            )

            # Send typing indicator (responsive UX)
            self.telegram_client.send_typing_action()

            # Run tool calling loop
            response, ollama_ms = run_tool_calling_loop(
                ollama_client=self.ollama_client,
                model=self.bot.ollama_model,
                history=context.history,
                tools_config=tools_config,
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

    def process_cron_job(
        self, name: str, description: str
    ) -> tuple[str, Optional[int]]:
        """
        Process cron job with tool calling loop.

        Args:
            name (str): Name of the cron job
            description (str): cron job description

        Returns:
            response: LLM Response
            ollama_ms: total duration taken by ollama
        """

        try:
            # Build tools from servers
            mcp_servers = list(
                MCPServer.objects.filter(bot_id=self.bot.id, is_active=True)
            )

            # Add default time mcp server to the 'mcp_servers' list
            default_servers = MCPServer.get_default_mcp_servers()
            default_servers.pop("cron_job")  # Remove 'cron_job' MCP server
            mcp_servers.extend(list(default_servers.values()))

            tools_config = asyncio.run(
                MCPToolsBuilder.build_tools_from_servers(mcp_servers)
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
                            name=name, description=description, bot_id=str(self.bot.id)
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
        """
        Send response to user and save to database.

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
