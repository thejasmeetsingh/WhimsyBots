"""Tests for `src/services/tool_calling_coordinator.py` (Tier 3).

`run_tool_calling_loop` is the chat orchestration glue between
Ollama and the MCP tool executor. We patch the Ollama client and the
sync `execute_tool_call_sync` so we can drive the loop deterministically.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.tool_calling_coordinator import run_tool_calling_loop


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


# ──────────────────────────────────────────────
# Termination: no tool calls
# ──────────────────────────────────────────────


def test_loop_terminates_when_no_tool_calls():
    history = [{"role": "user", "content": "hi"}]
    chat_response = {"message": "hello!", "tools": None, "ollama_ms": 12}

    client = MagicMock()
    client.chat.return_value = chat_response

    response, ms = run_tool_calling_loop(
        ollama_client=client,
        model="llama3",
        history=history,
        tools_config=[],
        ollama=_ollama(),
    )

    assert response == "hello!"
    assert ms == 12
    client.chat.assert_called_once()


def test_loop_passes_model_and_messages_to_chat():
    history = [{"role": "user", "content": "ping"}]
    client = MagicMock()
    client.chat.return_value = {"message": "pong", "tools": None, "ollama_ms": 1}

    run_tool_calling_loop(
        ollama_client=client,
        model="mistral",
        history=history,
        tools_config=[],
        ollama=_ollama(),
    )

    kwargs = client.chat.call_args.kwargs
    assert kwargs["model"] == "mistral"
    assert kwargs["messages"] == history


def test_loop_forwards_options_from_ollama_config():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    run_tool_calling_loop(
        ollama_client=client,
        model="m",
        history=[],
        tools_config=[],
        ollama=_ollama(temperature=0.3, num_ctx=8192, num_predict=256),
    )

    kwargs = client.chat.call_args.kwargs
    assert kwargs["options"] == {
        "temperature": 0.3,
        "num_ctx": 8192,
        "num_predict": 256,
    }


def test_loop_passes_empty_tools_when_no_configs():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    run_tool_calling_loop(
        ollama_client=client,
        model="m",
        history=[],
        tools_config=[],
        ollama=_ollama(),
    )

    assert client.chat.call_args.kwargs["tools"] == []


# ──────────────────────────────────────────────
# keep_alive wiring
# ──────────────────────────────────────────────


def test_loop_passes_keep_alive_when_add_keep_alive_true():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    run_tool_calling_loop(
        ollama_client=client,
        model="m",
        history=[],
        tools_config=[],
        ollama=_ollama(keep_alive="15m"),
        add_keep_alive=True,
    )

    assert client.chat.call_args.kwargs["keep_alive"] == "15m"


def test_loop_passes_none_keep_alive_when_add_keep_alive_false():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    run_tool_calling_loop(
        ollama_client=client,
        model="m",
        history=[],
        tools_config=[],
        ollama=_ollama(keep_alive="15m"),
        add_keep_alive=False,
    )

    # When the flag is off we explicitly pass None — never the ollama value.
    assert client.chat.call_args.kwargs["keep_alive"] is None


def test_loop_default_add_keep_alive_is_false():
    client = MagicMock()
    client.chat.return_value = {"message": "ok", "tools": None, "ollama_ms": None}

    run_tool_calling_loop(
        ollama_client=client,
        model="m",
        history=[],
        tools_config=[],
        ollama=_ollama(keep_alive="5m"),
    )

    # Default is False → keep_alive must be None.
    assert client.chat.call_args.kwargs["keep_alive"] is None


# ──────────────────────────────────────────────
# Tool-call loop body
# ──────────────────────────────────────────────


def test_loop_executes_each_tool_call_and_appends_history():
    """First turn: assistant asks for two tools.
    Second turn: assistant returns final message — loop terminates."""

    tool_call_a = SimpleNamespace(
        # `model_dump()` is called on the tool_call to produce a dict.
        model_dump=lambda: {"name": "tool_a", "arguments": {"x": 1}}
    )
    tool_call_b = SimpleNamespace(
        model_dump=lambda: {"name": "tool_b", "arguments": {"y": 2}}
    )

    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call_a, tool_call_b], "ollama_ms": 5},
        {"message": "all done", "tools": None, "ollama_ms": 7},
    ]

    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        side_effect=["result-a", "result-b"],
    ) as mock_exec:
        response, ms = run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[{"role": "user", "content": "go"}],
            tools_config=[_tool_cfg("tool_a"), _tool_cfg("tool_b")],
            ollama=_ollama(),
        )

    assert response == "all done"
    assert ms == 7
    assert mock_exec.call_count == 2

    # The history should have grown with one entry per tool result.
    # Original entry + two tool-role entries = 3.
    assert len(client.chat.call_args_list[1].kwargs["messages"]) == 3
    appended = client.chat.call_args_list[1].kwargs["messages"][-2:]
    assert appended[0]["role"] == "tool"
    assert appended[0]["content"] == "result-a"
    assert appended[1]["content"] == "result-b"


def test_loop_skips_tool_call_with_none_result():
    """If the tool executor returns None (e.g. error), we must NOT append
    a `{role: tool}` entry — that would confuse the LLM on the next turn."""

    tool_call = SimpleNamespace(model_dump=lambda: {"name": "broken_tool"})

    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 1},
        {"message": "ok", "tools": None, "ollama_ms": 1},
    ]

    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        return_value=None,
    ):
        run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[{"role": "user", "content": "x"}],
            tools_config=[_tool_cfg("broken_tool")],
            ollama=_ollama(),
        )

    # No tool-role entries were appended.
    final_history = client.chat.call_args_list[1].kwargs["messages"]
    assert all(m["role"] != "tool" for m in final_history)


def test_loop_records_tool_call_payload_in_appended_message():
    """Each appended `{role: tool}` message should echo the original
    tool_call dict under `tool_calls` for audit / debugging."""

    tool_call = SimpleNamespace(model_dump=lambda: {"name": "t", "arguments": {}})
    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 1},
        {"message": "done", "tools": None, "ollama_ms": 1},
    ]
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        return_value="r",
    ):
        run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[],
            tools_config=[_tool_cfg("t")],
            ollama=_ollama(),
        )

    final_history = client.chat.call_args_list[1].kwargs["messages"]
    tool_msg = next(m for m in final_history if m["role"] == "tool")
    assert tool_msg["tool_calls"] == [{"function": {"name": "t", "arguments": {}}}]


def test_loop_returns_ollama_ms_from_final_turn():
    """`ollama_ms` is captured from the LAST chat response, not the first."""

    tool_call = SimpleNamespace(model_dump=lambda: {"name": "t"})
    client = MagicMock()
    client.chat.side_effect = [
        {"message": "", "tools": [tool_call], "ollama_ms": 100},
        {"message": "done", "tools": None, "ollama_ms": 999},
    ]
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        return_value="ok",
    ):
        _, ms = run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[],
            tools_config=[_tool_cfg("t")],
            ollama=_ollama(),
        )
    assert ms == 999


# ──────────────────────────────────────────────
# Error propagation
# ──────────────────────────────────────────────


def test_loop_propagates_exceptions_from_chat():
    client = MagicMock()
    client.chat.side_effect = RuntimeError("ollama down")

    with pytest.raises(RuntimeError):
        run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[],
            tools_config=[],
            ollama=_ollama(),
        )


def test_loop_propagates_exceptions_from_tool_executor():
    tool_call = SimpleNamespace(model_dump=lambda: {"name": "t"})
    client = MagicMock()
    client.chat.return_value = {"message": "", "tools": [tool_call], "ollama_ms": 1}

    # When execute_tool_call_sync raises (not returns failure string),
    # the loop should let the exception bubble.
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        side_effect=RuntimeError("tool broken"),
    ):
        with pytest.raises(RuntimeError):
            run_tool_calling_loop(
                ollama_client=client,
                model="m",
                history=[],
                tools_config=[_tool_cfg("t")],
                ollama=_ollama(),
            )


def test_loop_returns_empty_message_when_chat_returns_no_message():
    client = MagicMock()
    client.chat.return_value = {"tools": None, "ollama_ms": None}
    response, ms = run_tool_calling_loop(
        ollama_client=client,
        model="m",
        history=[],
        tools_config=[],
        ollama=_ollama(),
    )
    assert response == ""
    assert ms is None
