"""Tool executor service and related data structures."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.choices import MCPTransportType
from app.models import MCPServer
from clients import mcp_client
from clients.telegram import TelegramClient
from mcp_tools.tools import (
    FETCH_AND_EXTRACT,
    FUNCTION_NAME_TO_CALLABLE_MAP,
    GENERATE_PDF,
    WEB_SEARCH,
)
from strings import INVALID_TOOL, TOOL_EXECUTION_FAILED

logger = logging.getLogger(__name__)


@dataclass
class MCPToolConfig:
    """Configuration for an MCP tool."""

    tool: dict[str, Any]
    config: dict[str, Any]
    transport_type: str

    def get_transport(self) -> str:
        """Determine transport type from config."""
        return (
            MCPTransportType.REMOTE.value[0]
            if self.config.get("url")
            else MCPTransportType.LOCAL.value[0]
        )


class MCPToolsBuilder:
    """Service for building and configuring MCP tools."""

    @staticmethod
    async def build_tools_from_servers(
        mcp_servers: list[MCPServer],
    ) -> list[MCPToolConfig]:
        """Build MCPToolConfig instances from bot MCP servers.

        Args:
            mcp_servers: QuerySet of active MCP servers

        Returns:
            List of MCPToolConfig instances
        """
        tools: list[MCPToolConfig] = []

        for server in mcp_servers:
            config = MCPToolsBuilder._build_server_config(server)
            transport_type = MCPToolsBuilder._get_transport_type(server)

            try:
                mcp_tools = await mcp_client(transport_type, config)
                for tool in mcp_tools:
                    tools.append(
                        MCPToolConfig(tool=tool, config=config, transport_type=transport_type)
                    )
            except Exception as _:
                logger.error(
                    "Failed to load tools from MCP server %s",
                    server.name,
                    exc_info=True,
                )

        return tools

    @staticmethod
    def _build_server_config(server: MCPServer) -> dict[str, Any]:
        """Build configuration dictionary from MCP server."""
        if server.transport == MCPTransportType.LOCAL.value[0]:
            return {
                "command": server.command,
                "args": server.args,
                "env": server.secrets,
            }
        else:
            return {"url": server.endpoint, "headers": server.secrets}

    @staticmethod
    def _get_transport_type(server: MCPServer) -> str:
        """Get transport type from server."""
        return server.transport


class ToolExecutor:
    """Service for executing MCP tools."""

    def __init__(self, tools: list[MCPToolConfig]):
        """Initialize the executor.

        Args:
            tools: List of MCPToolConfig instances
        """
        self.tools = tools

    def find_tool_by_call(self, tool_call: dict[str, Any]) -> Optional[MCPToolConfig]:
        """Find a tool matching the given tool call.

        Args:
            tool_call: The tool call from Ollama response

        Returns:
            MCPToolConfig if found, None otherwise
        """
        tool_name = tool_call.get("name")

        for tool in self.tools:
            tool_info = tool.tool.get("function", {})
            if tool_info.get("name") == tool_name:
                return tool

        return None

    async def execute_tool(
        self, tool_config: MCPToolConfig, payload: dict[str, Any] | None = None
    ) -> str:
        """Execute an MCP tool and return the result.

        Args:
            tool_config: The tool configuration to execute.
            payload: Optional arguments dict forwarded to the tool.

        Returns:
            String result from tool execution

        Raises:
            Exception: If tool execution fails
        """
        try:
            response = await mcp_client(tool_config.get_transport(), tool_config.config, payload)

            if response.get("isError"):
                logger.warning("Tool execution returned error: %s", response)
                return TOOL_EXECUTION_FAILED

            # Aggregate content from response
            result_parts = []
            for content in response.get("content", []):
                text = content.get("text", "")
                if text:
                    result_parts.append(text)

            return "\n\n".join(result_parts) if result_parts else ""
        except Exception as _:
            logger.error("Tool execution failed", exc_info=True)
            raise

    def execute_tool_call_sync(self, tool_call: dict[str, Any]) -> Optional[str]:
        """Execute a tool call synchronously.

        Args:
            tool_call: The tool call from Ollama response

        Returns:
            Tool result string, or None if tool not found
        """
        tool_config = self.find_tool_by_call(tool_call)
        if not tool_config:
            logger.warning("Tool not found for call: %s", tool_call.get("name"))
            return INVALID_TOOL.format(tool=tool_call.get("name"))

        try:
            result = asyncio.run(self.execute_tool(tool_config, tool_call))
            return result
        except Exception as _:
            logger.error("Failed to execute tool call", exc_info=True)
            return TOOL_EXECUTION_FAILED

    def execute_default_tool(
        self,
        tool_call: dict[str, Any],
        bot_id: Optional[str] = None,
        telegram_client: Optional[TelegramClient] = None,
    ) -> Optional[str]:
        """Execute a built-in default tool defined in 'mcp_tools'.

        Unlike 'execute_tool_call_sync', this path handles tools shipped with the
        project (cron job management, web search, PDF generation) that run as
        plain callables rather than remote/local MCP servers. It looks up the
        matching function in 'FUNCTION_NAME_TO_CALLABLE_MAP', injects the
        required runtime context ('bot_id' for cron tools, 'telegram_client'
        for PDF generation), and dispatches the call.

        Args:
            tool_call: The tool call from the Ollama response. Expected to
                contain a 'name' and an 'arguments' dict.
            bot_id: UUID of the bot invoking the tool. Forwarded to cron job
                tools. Ignored by 'web_search'/'fetch_and_extract'.
            telegram_client: Active 'TelegramClient' used by the PDF
                generator tool. Ignored by other tools.

        Returns:
            Optional[str]: The tool's string result on success, or a
            preformatted 'INVALID_TOOL' error message if the tool name has no
            registered callable. Returns None if the underlying callable
            returns None.
        """
        tool_name: str = tool_call.get("name", "")
        tool_args: dict[str, Any] = tool_call.get("arguments", {})

        logger.info("Calling '%s' default tool...", tool_name)

        if tool_name not in {WEB_SEARCH, FETCH_AND_EXTRACT}:
            if tool_name == GENERATE_PDF:
                tool_args.update({"client": telegram_client})
            else:
                tool_args.update({"bot_id": bot_id})

        _callable = FUNCTION_NAME_TO_CALLABLE_MAP.get(tool_name)
        if not _callable:
            return INVALID_TOOL.format(tool=tool_name)

        try:
            return _callable(**tool_args)
        except Exception as e:
            logger.error("Failed to execute tool call", exc_info=True)
            return TOOL_EXECUTION_FAILED + f": {str(e)}"
