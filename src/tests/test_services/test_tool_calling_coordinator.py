"""Tests for 'src/services/tool_calling_coordinator.py'.

'run_tool_calling_loop' is the chat orchestration glue between
Ollama and the MCP tool executor. We patch the Ollama client and the
sync 'execute_tool_call_sync' so we can drive the loop deterministically.

The Ollama client now returns each tool_call as a *dict* shaped like
'{"function": <Message.ToolCall>}' (mirroring the official ollama SDK).
The coordinator indexes 'tool_call["function"]' to obtain the inner
ToolCall object whose `model_dump()` is forwarded to the executor.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.choices import MessageRole
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
    Second turn: assistant returns final message — loop terminates.

    History shape on the second chat call:
        1. original user message
        2. assistant message echoing the tool_calls (prepended before
           tool execution so the LLM sees what it asked for)
        3. assistant message holding result-a
        4. assistant message holding result-b
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

    # History growth: 1 user + 1 assistant(echo) + 2 result = 4 entries.
    assert len(client.chat.call_args_list[1].kwargs["messages"]) == 4

    msgs = client.chat.call_args_list[1].kwargs["messages"]
    # Entry [1] is the echo of the assistant's tool-call request itself.
    echo = msgs[1]
    assert echo["role"] == _ASSISTANT_ROLE
    assert echo["content"] == ""
    assert echo["tool_calls"] == [tool_call_a, tool_call_b]

    # Entries [2] and [3] carry the tool results.
    assert msgs[2]["role"] == _ASSISTANT_ROLE
    assert msgs[2]["content"] == "result-a"
    assert msgs[3]["content"] == "result-b"


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
        run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[{"role": "user", "content": "x"}],
            tools_config=[_tool_cfg("broken_tool")],
            ollama=_ollama(),
        )

    final_history = client.chat.call_args_list[1].kwargs["messages"]
    # Only the assistant's tool-call echo was added (no result entry).
    assert len(final_history) == 2
    assert final_history[0] == {"role": "user", "content": "x"}
    assert final_history[1]["role"] == _ASSISTANT_ROLE
    # The echo carries the original tool_call — but no result content.
    assert "content" not in final_history[1] or final_history[1].get("content") == ""


def test_loop_records_tool_call_payload_in_appended_messages():
    """Two messages are appended for each successful tool call:

    1. An assistant `tool_calls` echo (content=''), carrying the raw
       tool_call dict as returned by Ollama (still wrapped as
       {function: <Message.ToolCall>}).
    2. An assistant result message with the tool's return value and
       the same `tool_calls` echo so the LLM can correlate.
    """
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
        run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[],
            tools_config=[_tool_cfg("t")],
            ollama=_ollama(),
        )

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
        run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[],
            tools_config=[_tool_cfg("t")],
            ollama=_ollama(),
        )

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
        _, ms = run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[],
            tools_config=[_tool_cfg("t")],
            ollama=_ollama(),
        )
    assert ms == 999


def test_loop_appends_assistant_echo_with_message_and_tool_calls():
    """The pre-tool assistant message must combine (a) the assistant's
    textual message from Ollama, (b) the tool_calls list, exactly as the
    Ollama client returned them — no flattening.
    """
    inner_a = MagicMock()
    inner_a.model_dump.return_value = {"name": "a", "arguments": {}}
    inner_b = MagicMock()
    inner_b.model_dump.return_value = {"name": "b", "arguments": {}}
    tool_call_a = {"function": inner_a}
    tool_call_b = {"function": inner_b}

    client = MagicMock()
    client.chat.side_effect = [
        {
            "message": "I'll need to call both tools.",
            "tools": [tool_call_a, tool_call_b],
            "ollama_ms": 1,
        },
        {"message": "done", "tools": None, "ollama_ms": 1},
    ]
    with patch(
        "services.tool_calling_coordinator.ToolExecutor.execute_tool_call_sync",
        side_effect=["ra", "rb"],
    ):
        run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[],
            tools_config=[_tool_cfg("a"), _tool_cfg("b")],
            ollama=_ollama(),
        )

    final_history = client.chat.call_args_list[1].kwargs["messages"]

    # Locate the echo: role=assistant, content is the textual message.
    echo = next(
        m
        for m in final_history
        if m["role"] == _ASSISTANT_ROLE and m["content"] == "I'll need to call both tools."
    )
    # tool_calls payload is the original list, not flattened.
    assert echo["tool_calls"] == [tool_call_a, tool_call_b]


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
        run_tool_calling_loop(
            ollama_client=client,
            model="m",
            history=[],
            tools_config=[_tool_cfg("t")],
            ollama=_ollama(),
        )

    final_history = client.chat.call_args_list[1].kwargs["messages"]
    echo = next(m for m in final_history if m["role"] == _ASSISTANT_ROLE and m.get("tool_calls"))
    # The echo's `tool_calls` references the same outer dict the client produced.
    assert outer in echo["tool_calls"] and len(echo["tool_calls"]) == 1


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
