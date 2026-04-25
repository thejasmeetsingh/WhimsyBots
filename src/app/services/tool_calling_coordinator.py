"""Tool calling coordinator for Ollama with MCP tools"""

import logging
from typing import Any

from clients import OllamaClient
from app.models import Ollama
from app.services.tool_executor import ToolExecutor


logger = logging.getLogger(__name__)


def run_tool_calling_loop(
    ollama_client: OllamaClient,
    model: str,
    history: list[dict[str, Any]],
    tools_config: list,
    ollama: Ollama,
) -> str:
    """
    Execute the tool calling loop with Ollama and MCP tools.

    Args:
        ollama_client: Configured OllamaClient instance
        model: Model name to use for chat
        history: Message history list with role and content
        tools_config: List of tool configurations from MCPToolsBuilder
        ollama: Ollama configuration with temperature, num_ctx, num_predict

    Returns:
        Final response message from Ollama

    Raises:
        Exception: If the tool calling loop fails
    """

    try:
        tool_executor = ToolExecutor(tools_config)

        # Tool calling loop
        while True:
            response = ollama_client.chat(
                model=model,
                messages=history,
                tools=[tool.tool for tool in tools_config],
                options={
                    "temperature": ollama.temperature,
                    "num_ctx": ollama.num_ctx,
                    "num_predict": ollama.num_predict,
                },
            )

            # Break if no tools were called
            if not response.get("tools"):
                break

            # Execute each tool call
            for tool_call in response.get("tools", []):
                tool_call_dict = tool_call.model_dump()
                result = tool_executor.execute_tool_call_sync(tool_call_dict)

                if result is not None:
                    history.append(
                        {
                            "role": "tool",
                            "content": result,
                            "tool_calls": [{"function": tool_call_dict}],
                        }
                    )

        return response.get("message", "")
    except Exception as e:
        logger.error("Failed to run tool calling loop", exc_info=True)
        raise
