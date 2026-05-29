"""Report generator service"""

import asyncio
import logging
from typing import Optional

from django.utils import timezone

from app.choices import MessageRole
from app.models import Bot, MCPServer, Message, Ollama
from app.utils import extract_html, generate_pdf
from clients import OllamaClient
from managers import TelegramClientManager
from services.context_assembler import ContextAssembler
from services.tool_calling_coordinator import run_tool_calling_loop
from services.tool_executor import MCPToolsBuilder
from strings import REPORT_READY

logger = logging.getLogger(__name__)


class ReportGeneratorService:
    """Service for generating and sending reports"""

    def __init__(self, bot: Bot, ollama: Ollama, ollama_client: OllamaClient):
        """
        Initialize the report generator.

        Args:
            bot: Bot instance
            ollama: Ollama configuration
            ollama_client: Configured OllamaClient
        """

        self.bot = bot
        self.ollama = ollama
        self.ollama_client = ollama_client
        self.telegram_client = TelegramClientManager.create_client(bot)

    def generate_and_send(self, message: Message) -> tuple[str, Optional[int]]:
        """
        Generate a report from bot conversation history and send to user.

        Args:
            message (Message): User's message.

        Returns:
            Status message and total duration taken by ollama

        Raises:
            Exception: If report generation or sending fails
        """

        try:
            # Build tools from servers
            mcp_servers = MCPServer.objects.filter(bot_id=self.bot.id, is_active=True)
            tools_config = asyncio.run(
                MCPToolsBuilder.build_tools_from_servers(list(mcp_servers))
            )

            context_assembler_svc = ContextAssembler(
                bot=self.bot, ollama=self.ollama, current_message=message
            )
            context = context_assembler_svc.assemble(
                tool_definitions=[tool.tool for tool in tools_config],
                active_mcp_server_names=[],
                is_report=True,
            )

            # Send typing indicator (responsive UX)
            self.telegram_client.send_typing_action()

            # Run tool calling loop to generate report
            report_response, ollama_ms = run_tool_calling_loop(
                ollama_client=self.ollama_client,
                model=self.bot.ollama_model,
                history=context.history,
                tools_config=tools_config,
                ollama=self.ollama,
            )

            Message.objects.create(
                bot=self.bot,
                role=MessageRole.ASSISTANT.value[0],
                content=report_response,
                is_report=True,
            )

            # Convert to PDF and send
            report_html = extract_html(report_response)
            pdf_bytes = generate_pdf(report_html)
            filename = f"report-{self.bot.name}-{timezone.now().isoformat()}.pdf"

            self.telegram_client.send_document(
                file_bytes=pdf_bytes,
                filename=filename,
                caption=REPORT_READY.format(bot_name=self.bot.name),
            )

            logger.info(f"Report generated and sent for bot: {self.bot.name}")
            return f"Report generated successfully for bot: {self.bot.name}", ollama_ms

        except Exception as _:
            logger.error("Failed to generate report", exc_info=True)
            raise
