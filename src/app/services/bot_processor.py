"""Bot message processor service"""

import asyncio
import logging

from clients import OllamaClient
from app.models import Bot, Message, Ollama
from app.choices import MessageRole
from app.config import CeleryConfig
from app.managers import OllamaConfigManager, TelegramClientManager
from app.services.tool_executor import MCPToolsBuilder
from app.services.tool_calling_coordinator import run_tool_calling_loop
from app.utils import convert_messages_to_ollama_format


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
        self.model = OllamaConfigManager.get_model(bot, ollama)
        self.telegram_client = TelegramClientManager.create_client(bot)

    def process_message(self) -> str:
        """
        Process message with tool calling loop.

        Returns:
            Final response from Ollama
        """

        try:
            # Build tools from servers
            tools_config = asyncio.run(
                MCPToolsBuilder.build_tools_from_servers(self.bot.mcp_servers)
            )

            system_prompt = CeleryConfig.DEFAULT_SYSTEM_PROMPT.format(
                system_prompt=self.bot.system_prompt or "You are a helpful assistant"
            )

            # Fetch messages
            messages = Message.objects.filter(bot_id=self.bot.id).order_by("created_at")

            # Prepare message history
            history = convert_messages_to_ollama_format(
                messages,
                system_prompt=system_prompt,
            )

            # Run tool calling loop
            return run_tool_calling_loop(
                ollama_client=self.ollama_client,
                model=self.model,
                history=history,
                tools_config=tools_config,
                ollama=self.ollama,
            )
        except Exception as e:
            logger.error("Failed to process message with tools", exc_info=True)
            raise

    def send_response(self, response: str) -> None:
        """
        Send response to user and save to database.

        Args:
            response: The response text to send
        """

        try:
            self.telegram_client.send_message(response)

            Message.objects.create(
                bot=self.bot, role=MessageRole.ASSISTANT.value[0], content=response
            )
        except Exception as e:
            logger.error("Failed to send response", exc_info=True)
            raise
