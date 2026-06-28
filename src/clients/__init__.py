"""External service clients.

Thin wrappers around the third-party SDKs that WhimsyBots talks to:
Ollama (local LLM), MCP (Model Context Protocol servers), and Telegram
(Bot API).
"""

from clients.mcp import mcp_client
from clients.ollama import OllamaClient
from clients.telegram import TelegramClient

__all__ = ("mcp_client", "OllamaClient", "TelegramClient")
