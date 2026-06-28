"""Tests for 'src/services/tool_executor.py'.

Three units are tested:
  — 'MCPToolConfig.get_transport' — URL/command presence decides transport.
  — 'MCPToolsBuilder' — config & transport selection, async build path.
  — 'ToolExecutor' — find-by-name, execute (async + sync), error mapping.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.choices import MCPTransportType
from services.tool_executor import (
    MCPToolConfig,
    MCPToolsBuilder,
    ToolExecutor,
)
from strings import INVALID_TOOL, TOOL_EXECUTION_FAILED

# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _server(
    *,
    name: str = "srv",
    transport: str = MCPTransportType.LOCAL.value[0],
    command: str | None = "python",
    args: list | None = None,
    endpoint: str | None = None,
    secrets: dict | None = None,
):
    return SimpleNamespace(
        name=name,
        transport=transport,
        command=command,
        args=args or [],
        endpoint=endpoint,
        secrets=secrets or {},
    )


def _tool(name: str = "my_tool"):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "desc",
            "parameters": {"type": "object"},
        },
    }


# ──────────────────────────────────────────────
# MCPToolConfig.get_transport
# ──────────────────────────────────────────────


def test_get_transport_returns_remote_when_url_present():
    cfg = MCPToolConfig(tool=_tool(), config={"url": "https://x"}, transport_type="X")
    assert cfg.get_transport() == MCPTransportType.REMOTE.value[0]


def test_get_transport_returns_local_when_no_url():
    cfg = MCPToolConfig(tool=_tool(), config={"command": "python"}, transport_type="X")
    assert cfg.get_transport() == MCPTransportType.LOCAL.value[0]


def test_get_transport_returns_local_when_url_empty_string():
    # Empty string is falsy ⇒ treat as "no url" ⇒ local transport.
    cfg = MCPToolConfig(tool=_tool(), config={"url": ""}, transport_type="X")
    assert cfg.get_transport() == MCPTransportType.LOCAL.value[0]


# ──────────────────────────────────────────────
# MCPToolsBuilder._build_server_config
# ──────────────────────────────────────────────


def test_build_server_config_local_shape():
    server = _server(
        transport=MCPTransportType.LOCAL.value[0],
        command="python",
        args=["-m", "srv"],
        secrets={"TOKEN": "abc"},
    )
    cfg = MCPToolsBuilder._build_server_config(server)
    assert cfg == {
        "command": "python",
        "args": ["-m", "srv"],
        "env": {"TOKEN": "abc"},
    }


def test_build_server_config_remote_shape():
    server = _server(
        transport=MCPTransportType.REMOTE.value[0],
        command=None,
        endpoint="https://mcp.example.com/sse",
        secrets={"Authorization": "Bearer xyz"},
    )
    cfg = MCPToolsBuilder._build_server_config(server)
    assert cfg == {
        "url": "https://mcp.example.com/sse",
        "headers": {"Authorization": "Bearer xyz"},
    }


def test_build_server_config_local_drops_endpoint():
    # If a LOCAL server somehow carries an endpoint, the builder must
    # ignore it (only the LOCAL-shape fields make it into the config).
    server = _server(
        transport=MCPTransportType.LOCAL.value[0],
        command="python",
        endpoint="https://should-be-ignored",
    )
    cfg = MCPToolsBuilder._build_server_config(server)
    assert "endpoint" not in cfg
    assert "url" not in cfg


# ──────────────────────────────────────────────
# MCPToolsBuilder._get_transport_type
# ──────────────────────────────────────────────


def test_get_transport_type_returns_server_transport():
    for code in (MCPTransportType.LOCAL.value[0], MCPTransportType.REMOTE.value[0]):
        server = _server(transport=code)
        assert MCPToolsBuilder._get_transport_type(server) == code


# ──────────────────────────────────────────────
# MCPToolsBuilder.build_tools_from_servers (async)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_tools_from_servers_returns_configs():
    server = _server()
    tool_list = [_tool("alpha"), _tool("beta")]

    with patch("services.tool_executor.mcp_client", AsyncMock(return_value=tool_list)):
        configs = await MCPToolsBuilder.build_tools_from_servers([server])

    assert len(configs) == 2
    # Every config carries the same shared transport + config from the server.
    for cfg in configs:
        assert cfg.transport_type == MCPTransportType.LOCAL.value[0]
        assert cfg.config == MCPToolsBuilder._build_server_config(server)


@pytest.mark.asyncio
async def test_build_tools_from_servers_skips_failed_servers(caplog):
    import logging

    # Distinct commands so the fake_client can tell the servers apart.
    server_ok = _server(name="ok", command="ok-cmd")
    server_bad = _server(name="bad", command="bad-cmd")
    tool_list = [_tool("only")]

    async def fake_client(transport, config):
        if config["command"] == "bad-cmd":
            raise RuntimeError("connection refused")
        return tool_list

    with (
        patch("services.tool_executor.mcp_client", side_effect=fake_client),
        caplog.at_level(logging.ERROR, logger="services.tool_executor"),
    ):
        configs = await MCPToolsBuilder.build_tools_from_servers([server_ok, server_bad])

    # Only the working server contributed tools.
    assert len(configs) == 1
    # And we logged the failure for the bad server.
    assert any("bad" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_build_tools_from_servers_empty_input_returns_empty():
    configs = await MCPToolsBuilder.build_tools_from_servers([])
    assert configs == []


@pytest.mark.asyncio
async def test_build_tools_from_servers_concatenates_across_servers():
    server_a = _server(name="a", command="a-cmd")
    server_b = _server(name="b", command="b-cmd")

    async def fake_client(transport, config):
        return [_tool(f"{config['command']}_tool")]

    with patch("services.tool_executor.mcp_client", side_effect=fake_client):
        configs = await MCPToolsBuilder.build_tools_from_servers([server_a, server_b])

    tool_names = sorted(c.tool["function"]["name"] for c in configs)
    assert tool_names == ["a-cmd_tool", "b-cmd_tool"]


# ──────────────────────────────────────────────
# ToolExecutor.find_tool_by_call
# ──────────────────────────────────────────────


def test_find_tool_by_call_returns_matching_config():
    executor = ToolExecutor([MCPToolConfig(tool=_tool("foo"), config={}, transport_type="L")])
    found = executor.find_tool_by_call({"name": "foo"})
    assert found is not None
    assert found.tool["function"]["name"] == "foo"


def test_find_tool_by_call_returns_none_when_no_match():
    executor = ToolExecutor([MCPToolConfig(tool=_tool("foo"), config={}, transport_type="L")])
    assert executor.find_tool_by_call({"name": "bar"}) is None


def test_find_tool_by_call_handles_missing_name():
    executor = ToolExecutor([MCPToolConfig(tool=_tool("foo"), config={}, transport_type="L")])
    assert executor.find_tool_by_call({}) is None


def test_find_tool_by_call_returns_first_match_when_duplicates():
    # If two configs expose the same tool name, return the first — that's
    # an undefined behaviour boundary, but we pin the implementation.
    cfg1 = MCPToolConfig(tool=_tool("dup"), config={"a": 1}, transport_type="L")
    cfg2 = MCPToolConfig(tool=_tool("dup"), config={"a": 2}, transport_type="L")
    executor = ToolExecutor([cfg1, cfg2])
    found = executor.find_tool_by_call({"name": "dup"})
    assert found is cfg1


# ──────────────────────────────────────────────
# ToolExecutor.execute_tool (async)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_returns_aggregated_text():
    cfg = MCPToolConfig(
        tool=_tool("t"),
        config={"command": "x"},
        transport_type=MCPTransportType.LOCAL.value[0],
    )
    response = {
        "isError": False,
        "content": [{"text": "first"}, {"text": "second"}],
    }
    with patch("services.tool_executor.mcp_client", AsyncMock(return_value=response)):
        result = await ToolExecutor([cfg]).execute_tool(cfg)
    assert result == "first\n\nsecond"


@pytest.mark.asyncio
async def test_execute_tool_returns_failed_marker_on_is_error():
    cfg = MCPToolConfig(tool=_tool("t"), config={}, transport_type="L")
    response = {"isError": True, "content": []}
    with patch("services.tool_executor.mcp_client", AsyncMock(return_value=response)):
        result = await ToolExecutor([cfg]).execute_tool(cfg)
    assert result == TOOL_EXECUTION_FAILED


@pytest.mark.asyncio
async def test_execute_tool_returns_empty_string_when_no_content():
    cfg = MCPToolConfig(tool=_tool("t"), config={}, transport_type="L")
    response = {"isError": False, "content": []}
    with patch("services.tool_executor.mcp_client", AsyncMock(return_value=response)):
        result = await ToolExecutor([cfg]).execute_tool(cfg)
    assert result == ""


@pytest.mark.asyncio
async def test_execute_tool_skips_empty_text_entries():
    cfg = MCPToolConfig(tool=_tool("t"), config={}, transport_type="L")
    response = {
        "isError": False,
        "content": [{"text": "kept"}, {"text": ""}, {"text": "also-kept"}],
    }
    with patch("services.tool_executor.mcp_client", AsyncMock(return_value=response)):
        result = await ToolExecutor([cfg]).execute_tool(cfg)
    # Empty-text entries are dropped — we only join non-empty parts.
    assert "kept" in result
    assert "also-kept" in result


@pytest.mark.asyncio
async def test_execute_tool_propagates_exceptions():
    cfg = MCPToolConfig(tool=_tool("t"), config={}, transport_type="L")
    with patch(
        "services.tool_executor.mcp_client",
        AsyncMock(side_effect=RuntimeError("transport died")),
    ):
        with pytest.raises(RuntimeError):
            await ToolExecutor([cfg]).execute_tool(cfg)


# ──────────────────────────────────────────────
# ToolExecutor.execute_tool_call_sync
# ──────────────────────────────────────────────


def test_execute_tool_call_sync_returns_invalid_tool_when_not_found():
    executor = ToolExecutor([MCPToolConfig(tool=_tool("foo"), config={}, transport_type="L")])
    result = executor.execute_tool_call_sync({"name": "missing"})
    assert result == INVALID_TOOL.format(tool="missing")


def test_execute_tool_call_sync_returns_tool_result_on_success():
    cfg = MCPToolConfig(tool=_tool("foo"), config={}, transport_type="L")
    executor = ToolExecutor([cfg])

    # Patch 'asyncio.run' because 'execute_tool_call_sync' is sync but
    # calls an async method internally.
    with patch(
        "services.tool_executor.asyncio.run",
        return_value="hello",
    ):
        result = executor.execute_tool_call_sync({"name": "foo"})

    assert result == "hello"


def test_execute_tool_call_sync_returns_failed_marker_on_exception():
    cfg = MCPToolConfig(tool=_tool("foo"), config={}, transport_type="L")
    executor = ToolExecutor([cfg])

    # When the async call raises, we return the failure marker rather
    # than letting the exception bubble up — the caller treats it as a
    # tool result string and lets the LLM respond.
    with patch(
        "services.tool_executor.asyncio.run",
        side_effect=RuntimeError("boom"),
    ):
        result = executor.execute_tool_call_sync({"name": "foo"})

    assert result == TOOL_EXECUTION_FAILED


def test_execute_tool_call_sync_handles_missing_name_gracefully():
    executor = ToolExecutor([])
    # Tool name missing → can't find → return INVALID_TOOL with "None".
    result = executor.execute_tool_call_sync({})
    assert INVALID_TOOL.format(tool=None) in result or INVALID_TOOL.format(tool="None") in result


# ──────────────────────────────────────────────
# Construction
# ──────────────────────────────────────────────


def test_tool_executor_stores_tools():
    tools = [MCPToolConfig(tool=_tool("a"), config={}, transport_type="L")]
    executor = ToolExecutor(tools)
    assert executor.tools is tools


def test_tool_executor_with_empty_list_is_valid():
    # An executor with no tools must still be constructable and short-
    # circuit cleanly on any tool call.
    executor = ToolExecutor([])
    assert executor.find_tool_by_call({"name": "any"}) is None
    assert executor.execute_tool_call_sync({"name": "any"}) == INVALID_TOOL.format(tool="any")
