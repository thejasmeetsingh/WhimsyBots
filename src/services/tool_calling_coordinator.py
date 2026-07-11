"""Tool calling coordinator for Ollama with MCP tools."""

import logging
from typing import Any, Optional

from app.choices import MessageRole
from app.models import Ollama
from clients import OllamaClient
from clients.telegram import TelegramClient
from mcp_tools.tools import (
    CRON_JOB_TOOLS,
    FUNCTION_NAME_TO_CALLABLE_MAP,
    PDF_GENERATOR_TOOLS,
    WEB_SEARCH_TOOLS,
)
from services.tool_executor import MCPToolConfig, ToolExecutor

logger = logging.getLogger(__name__)


def run_tool_calling_loop(
    bot_id: str,
    ollama_client: OllamaClient,
    model: str,
    history: list[dict[str, Any]],
    tools_config: list[MCPToolConfig],
    ollama: Ollama,
    telegram_client: TelegramClient,
    add_keep_alive: bool = False,
    exclude_crons: bool = False,
) -> tuple[str, Optional[int]]:
    """Run a multi-turn Ollama chat loop that resolves tool calls.

    Drives the model in a 'while True' loop: each iteration sends the
    current 'history' (with the configured 'tools' schema) to Ollama and
    exits when the response no longer contains a 'tools' field. While the
    model keeps requesting tools, each tool call is dispatched either to
    a built-in 'mcp_tools' callable (cron job, web search, PDF generator)
    or to a remote/local MCP server via 'ToolExecutor'. Results are
    appended back into 'history' so the next iteration sees them.

    The 'tools' list sent to Ollama is composed of:
    1. The bot's MCP server tools ('tools_config').
    2. The always-on 'WEB_SEARCH_TOOLS' and 'PDF_GENERATOR_TOOLS'.
    3. 'CRON_JOB_TOOLS', unless 'exclude_crons=True' (e.g. when the
       loop is itself triggered by a cron job, to prevent recursion).

    Args:
        bot_id: UUID of the bot driving the conversation. Forwarded to
            default tools that need it (cron job management).
        ollama_client: Configured 'OllamaClient' used to call Ollama.
        model: Name of the Ollama model to chat with.
        history: Mutable message history in Ollama/OpenAI format
            ('role', 'content'). Updated in place with assistant tool-call
            messages and tool results as the loop progresses.
        tools_config: Bot-specific MCP tool configurations produced by
            'MCPToolsBuilder.build_tools_from_servers'.
        ollama: 'Ollama' model row providing 'temperature', 'num_ctx',
            'num_predict', and 'keep_alive' settings.
        telegram_client: Active 'TelegramClient' required by the
            'generate_and_send_report' default tool.
        add_keep_alive: When True, pass 'ollama.keep_alive' to
            'ollama_client.chat' so the model stays loaded between
            requests. When False, omit it (model is unloaded
            immediately after the call).
        exclude_crons: When True, do not advertise the cron job
            tools to the model. Used when this loop is itself executed
            from within a cron job to avoid recursive scheduling.

    Returns:
        tuple[str, Optional[int]]: A 2-tuple of:
            - The final assistant 'message' text from Ollama.
            - The 'ollama_ms' duration of the last call in milliseconds,
              or None if the upstream client did not report one.

    Raises:
        Exception: Re-raises any error from 'ollama_client.chat',
            'ToolExecutor.execute_default_tool', or
            'ToolExecutor.execute_tool_call_sync' after logging it.
            Callers are expected to handle the failure and report a
            user-facing error.
    """
    try:
        tool_executor = ToolExecutor(tools_config)
        tools = [tool.tool for tool in tools_config] + WEB_SEARCH_TOOLS + PDF_GENERATOR_TOOLS

        if not exclude_crons:
            tools += CRON_JOB_TOOLS

        # Tool calling loop
        while True:
            response = ollama_client.chat(
                model=model,
                messages=history,
                keep_alive=ollama.keep_alive if add_keep_alive else None,
                tools=tools,
                options={
                    "temperature": ollama.temperature,
                    "num_ctx": ollama.num_ctx,
                    "num_predict": ollama.num_predict,
                },
            )

            # Break if no tools were called
            if not response.get("tools"):
                break

            # Append the assistant's tool-call message BEFORE the tool results
            history.append(
                {
                    "role": MessageRole.ASSISTANT.value[1].lower(),
                    "content": response.get("message", ""),
                    "tool_calls": response.get("tools"),
                }
            )

            # Execute each tool call
            for tool_call in response.get("tools", []):
                tool_call_dict = tool_call["function"].model_dump()

                if tool_call_dict.get("name", "") in FUNCTION_NAME_TO_CALLABLE_MAP:
                    result = tool_executor.execute_default_tool(
                        tool_call_dict, bot_id, telegram_client
                    )
                else:
                    result = tool_executor.execute_tool_call_sync(tool_call_dict)

                if result is not None:
                    history.append(
                        {
                            "role": MessageRole.ASSISTANT.value[1].lower(),
                            "content": result,
                            "tool_calls": [tool_call],
                        }
                    )

        return response.get("message", ""), response.get("ollama_ms")
    except Exception as _:
        logger.error("Failed to run tool calling loop", exc_info=True)
        raise
