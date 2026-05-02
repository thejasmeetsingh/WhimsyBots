"""
Model Context Protocol (MCP) client.

Provides async/await interface for connecting to MCP servers (both local and remote).
Handles tool discovery and execution with automatic cleanup.
"""

from typing import Any, Dict, Optional, List
from contextlib import AsyncExitStack

import httpx
from mcp.client.stdio import stdio_client
from mcp import ClientSession, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from app.choices import MCPTransportType


class MCPClient:
    """
    Asynchronous client for Model Context Protocol.

    Connects to MCP servers (local or remote) to discover available tools
    and execute them. Supports both stdio-based local servers and
    HTTP-based remote servers.

    Features:
    - Connection pooling and session caching
    - Local stdio server support (Python, Node.js, etc.)
    - Remote HTTPS server support
    - Automatic tool format conversion (MCP → Ollama)
    - Async/await based API
    - Proper resource cleanup

    Attributes:
        transport_type (str): 'L' for local, 'R' for remote
        config (dict): Transport-specific configuration
        exit_stack: AsyncExitStack for resource management
        _session: Cached ClientSession for connection reuse

    Example - Local MCP server:
        >>> import asyncio
        >>> client = MCPClient('L', {
        ...     'command': 'python',
        ...     'args': ['-m', 'mcp_server'],
        ...     'env': {}
        ... })
        >>> tools = await client.list_tools()
        >>> result = await client.execute_tool('get_weather', {'city': 'NYC'})
        >>> await client.cleanup()

    Example - Remote MCP server:
        >>> client = MCPClient('R', {
        ...     'url': 'https://api.example.com/mcp',
        ...     'headers': {'Authorization': 'Bearer token'}
        ... })
        >>> tools = await client.list_tools()
        >>> await client.cleanup()
    """

    def __init__(self, transport_type: str, config: Dict[str, Any]) -> None:
        """
        Initialize MCP client.

        Args:
            transport_type (str): Connection type
                'L' (LOCAL): Stdio-based server (command + args + env)
                'R' (REMOTE): HTTP-based server (url + headers)
            config (dict): Transport-specific configuration
                For LOCAL:
                    {
                        'command': 'python',  # or 'npx', 'uv', etc.
                        'args': ['-m', 'server_module'],  # optional
                        'env': {'VAR': 'value'}  # optional environment vars
                    }
                For REMOTE:
                    {
                        'url': 'https://api.example.com/mcp',
                        'headers': {'Authorization': 'Bearer token'}  # optional
                    }
        """

        self.transport_type = transport_type
        self.config = config
        self.exit_stack = AsyncExitStack()
        self._session: Optional[ClientSession] = None

    async def _connect(self) -> ClientSession:
        """
        Establish connection to MCP server.

        Creates either a stdio-based or HTTP-based connection based on transport_type.
        Caches the session for reuse on subsequent calls.

        Returns:
            ClientSession: Connected MCP session ready for tool operations

        Raises:
            ConnectionError: If unable to connect to server

        Internal method - not meant to be called directly.
        """

        # Return cached session if available
        if self._session is not None:
            return self._session

        if self.transport_type == MCPTransportType.LOCAL.value[0]:
            # LOCAL: Spawn subprocess with stdio
            params = StdioServerParameters(**self.config)
            (read, write) = await self.exit_stack.enter_async_context(
                stdio_client(params)
            )
        else:
            # REMOTE: HTTP connection
            http_client = httpx.AsyncClient(headers=self.config.get("headers", {}))
            (read, write, _) = await self.exit_stack.enter_async_context(
                streamable_http_client(url=self.config["url"], http_client=http_client)
            )

        # Initialize session and cache it
        session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        self._session = session
        return self._session

    async def list_tools(self) -> List[Dict[str, Any]]:
        """
        List all available tools from the MCP server.

        Queries the connected MCP server for available tools and converts
        them to Ollama-compatible function format for use in chat completions.

        Returns:
            list[dict]: Tools in Ollama function calling format
                Each tool:
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather for a location",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string"}
                            },
                            "required": ["location"]
                        }
                    }
                }

        Raises:
            ConnectionError: If not connected to server
            Exception: If server returns error

        Example:
            >>> tools = await client.list_tools()
            >>> for tool in tools:
            ...     print(f"{tool['function']['name']}: {tool['function']['description']}")
        """

        session = await self._connect()
        response = await session.list_tools()

        tools = []

        # Convert MCP tool format to Ollama-compatible format
        for tool in response.tools:
            input_schema = tool.inputSchema
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {
                            "type": input_schema.get("type", "object"),
                            "properties": input_schema.get("properties", {}),
                            "required": input_schema.get("required", []),
                        },
                    },
                }
            )

        return tools

    async def execute_tool(
        self, name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute a tool on the MCP server.

        Calls a tool by name with optional arguments and returns the result.

        Args:
            name (str): Name of the tool to execute
            arguments (dict | None): Arguments to pass to the tool
                Format depends on tool's parameter schema

        Returns:
            dict: Tool execution result containing:
                - isError (bool): Whether execution failed
                - content (list): Response content objects
                - Each content object has 'type' and 'text' fields

        Raises:
            ConnectionError: If not connected to server
            Exception: If tool execution fails on server

        Example - Tool with arguments:
            >>> result = await client.execute_tool('get_weather', {
            ...     'location': 'New York',
            ...     'units': 'celsius'
            ... })
            >>> if result.get('isError'):
            ...     print(f"Tool failed: {result['content']}")
            >>> else:
            ...     print(result['content'][0]['text'])  # Tool output

        Example - Tool without arguments:
            >>> result = await client.execute_tool('get_current_time')
        """

        session = await self._connect()
        response = await session.call_tool(name, arguments)

        return response.model_dump()

    async def cleanup(self) -> None:
        """
        Close server connection and release resources.

        Must be called when done with the client to properly close
        the subprocess or HTTP connection and clean up resources.

        Important:
            Always call this in a try/finally block or use with `async with`

        Example:
            >>> client = MCPClient(...)
            >>> try:
            ...     await client.list_tools()
            ... finally:
            ...     await client.cleanup()
        """

        await self.exit_stack.aclose()
        self._session = None


async def mcp_client(
    transport_type: str,
    config: Dict[str, Any],
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any] | List[Dict[str, Any]]:
    """
    Convenience async function for single MCP server operations.

    Creates an MCPClient, performs an operation, and cleans up.
    Useful for one-off tool calls without managing client lifecycle.

    Args:
        transport_type (str): 'L' for local, 'R' for remote
        config (dict): Transport configuration (see MCPClient.__init__)
        payload (dict | None): Tool execution options if provided
            If None: lists tools
            If provided: executes tool with format {"name": "...", "args": {...}}

    Returns:
        dict | list: Tool execution result or tool list

    Example - List tools:
        >>> tools = await mcp_client('L', {
        ...     'command': 'python',
        ...     'args': ['-m', 'memory_server'],
        ...     'env': {}
        ... })
        >>> print(tools)  # List of tools

    Example - Execute tool:
        >>> result = await mcp_client('L', config, {
        ...     'name': 'save_memory',
        ...     'args': {'key': 'my_var', 'value': 'data'}
        ... })
        >>> print(result)  # Execution result

    Note:
        This is a convenience wrapper. For multiple operations, create
        an MCPClient instance directly and manage its lifecycle.
    """

    client = MCPClient(transport_type, config)

    try:
        if payload:
            # Execute tool with arguments
            response = await client.execute_tool(**payload)
        else:
            # List available tools
            response = await client.list_tools()

        return response
    finally:
        # Always cleanup resources
        await client.cleanup()
