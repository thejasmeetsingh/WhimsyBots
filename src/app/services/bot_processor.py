"""Bot message processor service"""

import asyncio
import json
import logging
import re
from typing import Literal

from django.conf import settings
from pydantic import BaseModel, ValidationError

from clients import OllamaClient
from app.models import Bot, MCPServer, Message, Ollama
from app.choices import MCPTransportType, MessageRole
from app.config import CeleryConfig
from app.managers import OllamaConfigManager, TelegramClientManager
from app.services.tool_executor import MCPToolsBuilder
from app.services.tool_calling_coordinator import run_tool_calling_loop
from app.utils import convert_messages_to_ollama_format


logger = logging.getLogger(__name__)


class StructuredOutput(BaseModel):
    intent: Literal["J", "R", "Q", "CJ", "O"]
    response: str


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

    def _parse_llm_response(self, raw: str) -> StructuredOutput:
        """
        Validates LLM output against StructuredOutput schema.
        Falls back gracefully if model still misbehaves despite format param.

        Args:
            raw: Raw response returned by the LLM

        Returns:
            StructuredOutput pydantic model
        """

        # Step 1: Try clean pydantic validation (happy path)
        try:
            result = StructuredOutput.model_validate_json(raw)
            return result

        except ValidationError:
            pass

        # Step 2: Some models still wrap in markdown despite format param
        cleaned = raw.strip()
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"```", "", cleaned)
        cleaned = cleaned.strip()

        try:
            result = StructuredOutput.model_validate_json(cleaned)
            return result

        except ValidationError:
            pass

        # Step 3: Try extracting first JSON object from string
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            try:
                result = StructuredOutput.model_validate_json(json_match.group(0))
                return result.intent, result.response

            except ValidationError:
                # JSON found but fields don't match schema
                # attempt manual extraction with fallback values
                try:
                    data = json.loads(json_match.group(0))
                    intent = data.get("intent", "O").strip().upper()
                    response = data.get("response", raw.strip())

                    # ensure intent is valid before constructing
                    result = StructuredOutput(
                        intent=intent if intent in {"J", "R", "Q", "CJ", "O"} else "O",
                        response=response if response else raw.strip(),
                    )
                    return result

                except (json.JSONDecodeError, ValidationError):
                    pass

        result = StructuredOutput(intent="O", response=raw.strip())
        return result

    def get_default_mcp_servers(self) -> MCPServer:
        """
        Create MCPServer (temp) objects for default MCP servers

        Returns:
            list[MCPServer]: MCPServer objects
        """

        cron_job_mcp = MCPServer(
            name="cron_job",
            transport=MCPTransportType.LOCAL.value[0],
            command="python",
            args=["-m", "cron_job"],
            secrets={
                "DB_NAME": settings.DB_NAME,
                "DB_USER": settings.DB_USER,
                "DB_PASSWORD": settings.DB_PASSWORD,
                "DB_HOST": settings.DB_HOST,
            },
        )

        time_mcp = MCPServer(
            name="time",
            transport=MCPTransportType.LOCAL.value[0],
            command="python",
            args=["-m", "mcp_server_time"],
        )

        return [cron_job_mcp, time_mcp]

    def process_message(self) -> StructuredOutput:
        """
        Process message with tool calling loop.

        Returns:
            Final response from Ollama
        """

        try:
            # Build tools from servers
            mcp_servers = list(
                MCPServer.objects.filter(bot_id=self.bot.id, is_active=True)
            )

            # Add default mcp server's to the 'mcp_servers' list
            default_servers = self.get_default_mcp_servers()
            mcp_servers.extend(default_servers)

            tools_config = asyncio.run(
                MCPToolsBuilder.build_tools_from_servers(mcp_servers)
            )

            system_prompt = CeleryConfig.DEFAULT_SYSTEM_PROMPT.format(
                system_prompt=self.bot.system_prompt or "You are a helpful assistant",
                bot_id=str(self.bot.id),
                timezone=settings.TIME_ZONE,
            )

            # Fetch messages
            messages = Message.objects.filter(bot_id=self.bot.id).order_by("created_at")

            # Prepare message history
            history = convert_messages_to_ollama_format(
                messages,
                system_prompt=system_prompt,
            )

            # Run tool calling loop
            response = run_tool_calling_loop(
                ollama_client=self.ollama_client,
                model=self.model,
                history=history,
                tools_config=tools_config,
                ollama=self.ollama,
                format=StructuredOutput.model_json_schema(),
            )

            # Validate the response strucutre
            result = self._parse_llm_response(response)
            return result

        except ValidationError as _:
            logger.error("Invalid response returned from the bot", exc_info=True)
            raise
        except Exception as _:
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
        except Exception as _:
            logger.error("Failed to send response", exc_info=True)
            raise
