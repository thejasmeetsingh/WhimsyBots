from typing import Any
from contextlib import AsyncExitStack

import httpx
from mcp.client.stdio import stdio_client
from mcp import ClientSession, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from app.choices import MCPTransportType


class MCPClient:
    def __init__(self, transport_type: str, config: dict[str, Any]) -> None:
        self.transport_type = transport_type
        self.config = config
        self.exit_stack = AsyncExitStack()
    
    async def get_session(self) -> ClientSession:
        if self.transport_type == MCPTransportType.LOCAL.value[0]:
            params = StdioServerParameters(**self.config)
            (read, write) = await self.exit_stack.enter_async_context(stdio_client(params))
        else:
            http_client = httpx.AsyncClient(headers=self.config.get("headers", {}))
            (read, write, _) = await self.exit_stack.enter_async_context(
                streamable_http_client(url=self.config["url"], http_client=http_client))
        
        session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        return session
    
    async def list_tools(self) -> list[dict[str, Any]]:
        session = await self.get_session()

        await session.initialize()
        response = await session.list_tools()

        tools = []

        # Convert MCP list_tools response to ollama tools format
        for tool in response.tools:
            input_schema = tool.inputSchema
            tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": input_schema.get("type", "object"),
                        "properties": input_schema.get("properties", {}),
                        "required": input_schema.get("required", []),
                    }
                }
            })
        
        return tools

    async def execute_tool(self, tool_name: str, tool_args: dict[str, Any] | None = None) -> dict[str, Any]:
        session = await self.get_session()

        await session.initialize()
        response = await session.call_tool(tool_name, tool_args)

        return response.model_dump()

    async def cleanup(self):
        await self.exit_stack.aclose()
