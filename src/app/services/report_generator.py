"""Report generator service"""

import logging

from django.utils import timezone

from clients import OllamaClient
from app.models import Bot, Ollama
from app.config import CeleryConfig
from app.managers import OllamaConfigManager, TelegramClientManager
from app.utils import convert_messages_to_ollama_format, generate_pdf


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
        self.model = OllamaConfigManager.get_model(bot, ollama)
        self.telegram_client = TelegramClientManager.create_client(bot)

    def generate_and_send(self) -> str:
        """
        Generate a report from bot conversation history and send to user.

        Returns:
            Status message

        Raises:
            Exception: If report generation or sending fails
        """

        try:
            self.telegram_client.send_typing_action()

            # Get conversation history
            history = convert_messages_to_ollama_format(
                messages=self.bot.messages.order_by("created_at").all(),
                system_prompt=CeleryConfig.REPORT_GENERATION_PROMPT
            )

            # Generate report
            response = self.ollama_client.chat(
                model=self.model,
                messages=history,
                options={
                    "temperature": self.ollama.temperature,
                    "num_ctx": self.ollama.num_ctx,
                    "num_predict": self.ollama.num_predict
                }
            )

            report_html = response.get("message", "")

            # Convert to PDF and send
            pdf_bytes = generate_pdf(report_html)
            filename = f"report-{self.bot.name}-{timezone.now().isoformat()}.pdf"

            self.telegram_client.send_document(
                file_bytes=pdf_bytes,
                filename=filename,
                caption=CeleryConfig.TELEGRAM_MESSAGES["REPORT_READY"].format(
                    bot_name=self.bot.name
                )
            )

            logger.info(f"Report generated and sent for bot: {self.bot.name}")
            return f"Report generated successfully for bot: {self.bot.name}"

        except Exception as e:
            logger.error("Failed to generate report", exc_info=True)
            raise
