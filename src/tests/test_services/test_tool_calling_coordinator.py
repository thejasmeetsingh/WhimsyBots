"""Tests for 'src/services/tool_calling_coordinator.py'.

'run_tool_calling_loop' is the chat orchestration glue between
Ollama and the MCP tool executor. We patch the Ollama client and the
sync 'execute_tool_call_sync' so we can drive the loop deterministically.

The Ollama client returns each tool_call as a *dict* shaped like
'{"function": <Message.ToolCall>}' (mirroring the official ollama SDK).
The coordinator indexes 'tool_call["function"]' to obtain the inner
ToolCall object whose `model_dump()` is forwarded to the executor.

After the mcp_tools refactor, the loop accepts three additional
parameters:
  - 'bot_id' (UUID of the active bot, forwarded to default tools)
  - 'telegram_client' (forwarded to the PDF generator default tool)
  - 'exclude_crons' (drops the cron tool group to prevent recursion)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.choices import MessageRole
from mcp_tools.tools import (
    CRON_JOB_TOOLS,
    FUNCTION_NAME_TO_CALLABLE_MAP,
    PDF_GENERATOR_TOOLS,
    WEB_SEARCH_TOOLS,
)
from services.tool_calling_coordinator import run_tool_calling_loop

_ASSISTANT_ROLE = MessageRole.ASSISTANT.value[1].lower()


def _tool_call(name: str = "my_tool", arguments: dict | None = None):
    """Build a fake tool_call as the Ollama client would return it:
    a dict with a `function` key whose value exposes `model_dump()`.
    """
    inner = MagicMock(name=f"tool_call_{name}")
    inner.model_dump.return_value = {
        "name": name,
        "arguments": arguments or {},
    }
    return {"function": inner}


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _ollama(temperature=0.7, num_ctx=4096, num_predict=None, keep_alive="10m"):
    return SimpleNamespace(
        temperature=temperature,
        num_ctx=num_ctx,
        num_predict=num_predict,
        keep_alive=keep_alive,
    )


def _tool_cfg(name="my_tool"):
    return SimpleNamespace(
        tool={
            "type": "function",
            "function": {
                "name": name,
                "description": "d",
                "parameters": {"type": "object"},
            },
        },
        config={},
        transport_type="L",
    )


def _run_loop(
    client,
    history,
    tools_config,
    ollama,
    *,
    bot_id="bot-1",
    telegram_client=None,
    **kwargs,
):
    """Helper that runs the loop with the new mandatory parameters."""
    return run_tool_calling_loop(
        bot_id=bot_id,
        telegram_client=telegram_client or MagicMock(name="TelegramClient"),
        ollama_client=client,
        model="m",
        history=history,
        tools_config=tools_config,
        ollama=ollama,
        **kwargs,
    )


# ──────────────────────────────────────────────
# Termination: no tool calls
# ──────────────────────────────────────────────


def test_loop_terminates_when_no_tool_calls():
    history = [{"role": "user", "content": "hi"}]
    chat_response = {"message": "hello!", "tools": None, "ollama_ms": 12}

    client = MagicMock()
    client.chat.return_value = chat_response

    response, ms = _run_loop(client, history, [], _ollama())

    assert response == "hello!"
    assert ms == 12
    client.chat.assert_called_once()


def test_loop_passes_model_and_messages_to_chat():
    history = [{"role": "user", "content": "ping"}]
    client = MagicMock()
    client.chat.return_value = {"message": "pong", "tools": None, "ollama_ms": 1}

    _run_loop(client, history, [], _ollama())

    kwargs = client.chat.call_args.kwargs
    assert kwargs["model"] == "m"
    assert kwargs["messages"] == history


def test_loop_forwards_options_from_ollama_config():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    _run_loop(
        client,
        [],
        [],
        _ollama(temperature=0.3, num_ctx=8192, num_predict=256),
    )

    kwargs = client.chat.call_args.kwargs
    assert kwargs["options"] == {
        "temperature": 0.3,
        "num_ctx": 8192,
        "num_predict": 256,
    }


def test_loop_advertises_default_tools_when_no_configs():
    """Even with no MCP tool configs, the always-on web search, PDF,
    and cron tool groups are advertised to the model.
    """
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    _run_loop(client, [], [], _ollama())

    tools = client.chat.call_args.kwargs["tools"]
    # The flat list contains every individual tool definition.
    advertised_names = {t["function"]["name"] for t in tools}
    for group in (WEB_SEARCH_TOOLS, PDF_GENERATOR_TOOLS, CRON_JOB_TOOLS):
        for tool in group:
            assert tool["function"]["name"] in advertised_names


def test_loop_passes_keep_alive_when_add_keep_alive_true():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    _run_loop(client, [], [], _ollama(keep_alive="15m"), add_keep_alive=True)

    assert client.chat.call_args.kwargs["keep_alive"] == "15m"


def test_loop_passes_none_keep_alive_when_add_keep_alive_false():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    _run_loop(client, [], [], _ollama(keep_alive="15m"), add_keep_alive=False)

    # When the flag is off we explicitly pass None — never the ollama value.
    assert client.chat.call_args.kwargs["keep_alive"] is None


def test_loop_default_add_keep_alive_is_false():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    _run_loop(client, [], [], _ollama(keep_alive="5m"))

    # Default is False → keep_alive must be None.
    assert client.chat.call_args.kwargs["keep_alive"] is None


# ──────────────────────────────────────────────
# exclude_crons flag
# ──────────────────────────────────────────────


def test_loop_excludes_cron_tools_when_exclude_crons_true():
    """The cron job tool group is omitted when 'exclude_crons=True'."""
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    _run_loop(client, [], [], _ollama(), exclude_crons=True)

    tools = client.chat.call_args.kwargs["tools"]
    advertised_names = {t["function"]["name"] for t in tools}
    cron_names = {tool["function"]["name"] for tool in CRON_JOB_TOOLS}
    # No cron tool is advertised.
    assert advertised_names.isdisjoint(cron_names)
    # But the other default groups are still there.
    for tool in WEB_SEARCH_TOOLS + PDF_GENERATOR_TOOLS:
        assert tool["function"]["name"] in advertised_names


def test_loop_default_exclude_crons_is_false():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    _run_loop(client, [], [], _ollama())

    tools = client.chat.call_args.kwargs["tools"]
    advertised_names = {t["function"]["name"] for t in tools}
    cron_names = {tool["function"]["name"] for tool in CRON_JOB_TOOLS}
    assert not advertised_names.isdisjoint(cron_names)


# ──────────────────────────────────────────────
# Tool-call loop body — MCP tools
# ──────────────────────────────────────────────


def test_loop_executes_each_mcp_tool_call_and_appends_history():
    """First turn: assistant asks for two MCP tools.
    Second turn: assistant returns final message — loop terminates.
    """
    tool_call_a = _tool_call("tool_a", {"x": 1})
    tool_call_b = _tool_call("tool_b", {"y": 2})

    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call_a, tool_call_b], "ollama_ms": 5},
        {"message": "all done", "tools": None, "ollama_ms": 7},
    ]

    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        side_effect=["result-a", "result-b"],
    ) as mock_exec:
        response, ms = _run_loop(
            client,
            [{"role": "user", "content": "go"}],
            [_tool_cfg("tool_a"), _tool_cfg("tool_b")],
            _ollama(),
        )

    assert response == "all done"
    assert ms == 7
    assert mock_exec.call_count == 2

    # History growth: 1 user + 1 assistant(echo) + 2 result = 4 entries.
    assert len(client.chat.call_args_list[1].kwargs["messages"]) == 4


def test_loop_skips_tool_call_with_none_result():
    """If the tool executor returns None (e.g. error), we must NOT append
    a result-carrying message — otherwise the next chat turn would carry an
    empty result alongside the original tool_call echo.
    """
    tool_call = _tool_call("broken_tool")

    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 1},
        {"message": "ok", "tools": None, "ollama_ms": 1},
    ]

    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        return_value=None,
    ):
        _run_loop(
            client,
            [{"role": "user", "content": "x"}],
            [_tool_cfg("broken_tool")],
            _ollama(),
        )

    final_history = client.chat.call_args_list[1].kwargs["messages"]
    # Only the assistant's tool-call echo was added (no result entry).
    assert len(final_history) == 2


def test_loop_records_tool_call_payload_in_appended_messages():
    tool_call = _tool_call("t", {})
    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 1},
        {"message": "done", "tools": None, "ollama_ms": 1},
    ]
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        return_value="r",
    ):
        _run_loop(client, [], [_tool_cfg("t")], _ollama())

    final_history = client.chat.call_args_list[1].kwargs["messages"]

    # The assistant tool-calls echo carries the raw (un-flattened) tool_call.
    echo_msg = next(
        m for m in final_history if m.get("role") == _ASSISTANT_ROLE and m.get("content") == ""
    )
    assert echo_msg["tool_calls"] == [tool_call]

    # The result message carries the same raw tool_call under `tool_calls`.
    result_msg = next(
        m for m in final_history if m.get("role") == _ASSISTANT_ROLE and m.get("content") == "r"
    )
    assert result_msg["tool_calls"] == [tool_call]


def test_loop_passes_tool_call_function_payload_to_executor():
    """`execute_tool_call_sync` must receive the result of
    `tool_call['function'].model_dump()` — i.e. the inner function dict,
    not the outer {function: ...} wrapper.
    """
    inner_payload = {"name": "t", "arguments": {"k": "v"}}
    inner = MagicMock()
    inner.model_dump.return_value = inner_payload
    tool_call = {"function": inner}

    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 1},
        {"message": "done", "tools": None, "ollama_ms": 1},
    ]
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        return_value="ok",
    ) as mock_exec:
        _run_loop(client, [], [_tool_cfg("t")], _ollama())

    # Exactly one call; arg is the dumped inner function payload.
    mock_exec.assert_called_once_with(inner_payload)
    inner.model_dump.assert_called_once_with()


def test_loop_returns_ollama_ms_from_final_turn():
    """'ollama_ms' is captured from the LAST chat response, not the first."""
    tool_call = _tool_call("t")
    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 100},
        {"message": "done", "tools": None, "ollama_ms": 999},
    ]
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        return_value="ok",
    ):
        _, ms = _run_loop(client, [], [_tool_cfg("t")], _ollama())
    assert ms == 999


def test_loop_does_not_mutate_or_flatten_returned_tool_calls():
    """Whatever the Ollama client yields under `tools` must appear, identical,
    inside the assistant echo's `tool_calls` field — the coordinator must not
    strip the `function` wrapper, nor re-order the list.
    """
    outer = {"function": MagicMock(model_dump=lambda: {"name": "t", "arguments": {}})}
    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [outer], "ollama_ms": 1},
        {"message": "done", "tools": None, "ollama_ms": 1},
    ]
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        return_value="ok",
    ):
        _run_loop(client, [], [_tool_cfg("t")], _ollama())

    final_history = client.chat.call_args_list[1].kwargs["messages"]
    echo = next(m for m in final_history if m["role"] == _ASSISTANT_ROLE and m.get("tool_calls"))
    # The echo's `tool_calls` references the same outer dict the client produced.
    assert outer in echo["tool_calls"] and len(echo["tool_calls"]) == 1


# ──────────────────────────────────────────────
# Default tool dispatch
# ──────────────────────────────────────────────


def test_loop_dispatches_default_tool_via_execute_default_tool():
    """When the tool name appears in FUNCTION_NAME_TO_CALLABLE_MAP the
    loop routes through 'execute_default_tool', not 'execute_tool_call_sync'.
    """
    # Pick a name that's known to be in the default map.
    default_name = next(iter(FUNCTION_NAME_TO_CALLABLE_MAP))
    tool_call = _tool_call(default_name, {})
    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 1},
        {"message": "done", "tools": None, "ollama_ms": 1},
    ]
    with (
        patch(
            "services.tool_calling_coordinator.ToolExecutor.execute_default_tool",
            return_value="default-result",
        ) as default_exec,
        patch(
            "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        ) as mcp_exec,
    ):
        _run_loop(client, [], [_tool_cfg(default_name)], _ollama())

    default_exec.assert_called_once()
    # mcp_exec is NOT used for default tools.
    mcp_exec.assert_not_called()


def test_loop_routes_unknown_tool_through_mcp_executor():
    """Tool names NOT in FUNCTION_NAME_TO_CALLABLE_MAP go through the
    legacy 'execute_tool_call_sync' path.
    """
    tool_call = _tool_call("custom_mcp_tool", {})
    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 1},
        {"message": "done", "tools": None, "ollama_ms": 1},
    ]
    with (
        patch(
            "services.tool_calling_coordinator.ToolExecutor.execute_default_tool",
        ) as default_exec,
        patch(
            "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
            return_value="mcp-result",
        ) as mcp_exec,
    ):
        _run_loop(client, [], [_tool_cfg("custom_mcp_tool")], _ollama())

    mcp_exec.assert_called_once()
    default_exec.assert_not_called()


def test_loop_passes_bot_id_and_telegram_client_to_default_tool():
    """Both bot_id and telegram_client are forwarded to execute_default_tool."""
    default_name = next(iter(FUNCTION_NAME_TO_CALLABLE_MAP))
    tool_call = _tool_call(default_name, {})
    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 1},
        {"message": "done", "tools": None, "ollama_ms": 1},
    ]
    tg = MagicMock(name="TelegramClient")
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_default_tool",
        return_value="ok",
    ) as default_exec:
        _run_loop(
            client, [], [_tool_cfg(default_name)], _ollama(), bot_id="bot-xyz", telegram_client=tg
        )

    # bot_id and telegram_client are passed to the executor.
    assert default_exec.call_args.args[1] == "bot-xyz"
    assert default_exec.call_args.args[2] is tg


# ──────────────────────────────────────────────
# Error propagation
# ──────────────────────────────────────────────


def test_loop_propagates_exceptions_from_chat():
    client = MagicMock()
    client.chat.side_effect = RuntimeError("ollama down")

    with pytest.raises(RuntimeError):
        _run_loop(client, [], [], _ollama())


def test_loop_propagates_exceptions_from_tool_executor():
    tool_call = _tool_call("t")
    client = MagicMock()
    client.chat.return_value = {"message": "", "tools": [tool_call], "ollama_ms": 1}

    # When execute_tool_call_sync raises (not returns failure string),
    # the loop should let the exception bubble.
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        side_effect=RuntimeError("tool broken"),
    ):
        with pytest.raises(RuntimeError):
            _run_loop(client, [], [_tool_cfg("t")], _ollama())


def test_loop_returns_empty_message_when_chat_returns_no_message():
    client = MagicMock()
    client.chat.return_value = {"tools": None, "ollama_ms": None}
    response, ms = _run_loop(client, [], [], _ollama())
    assert response == ""
    assert ms is None
