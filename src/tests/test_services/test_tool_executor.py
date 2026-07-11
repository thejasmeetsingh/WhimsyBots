"""Tests for 'src/services/tool_executor.py'.

Four units are tested:
  — 'MCPToolConfig.get_transport' — URL/command presence decides transport.
  — 'MCPToolsBuilder' — config & transport selection, async build path,
    Redis-backed per-server cache.
  — 'ToolExecutor.find_tool_by_call' / 'execute_tool' / 'execute_tool_call_sync'
    — find-by-name, async execute, sync wrapper, error mapping.
  — 'ToolExecutor.execute_default_tool' — in-process mcp_tools dispatch.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.core.cache import cache

from app.choices import MCPTransportType
from mcp_tools.tools import (
    FETCH_AND_EXTRACT,
    FUNCTION_NAME_TO_CALLABLE_MAP,
    GENERATE_PDF,
    WEB_SEARCH,
)
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
    id: str = "srv-1",
    transport: str = MCPTransportType.LOCAL.value[0],
    command: str | None = "python",
    args: list | None = None,
    endpoint: str | None = None,
    secrets: dict | None = None,
):
    return SimpleNamespace(
        id=id,
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


@pytest.fixture(autouse=True)
def _clear_cache():
    """The Redis-backed cache (LocMemCache in tests) must be cleared
    between tests so cached tool lists from one test do not leak.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.mark.asyncio
async def test_build_tools_from_servers_returns_configs():
    server = _server()
    tool_list = [_tool("alpha"), _tool("beta")]

    with patch("services.tool_executor.mcp_client", AsyncMock(return_value=tool_list)):
        configs = await MCPToolsBuilder.build_tools_from_servers(
            bot_id="bot-1", mcp_servers=[server]
        )

    assert len(configs) == 2
    for cfg in configs:
        assert cfg.transport_type == MCPTransportType.LOCAL.value[0]
        assert cfg.config == MCPToolsBuilder._build_server_config(server)


@pytest.mark.asyncio
async def test_build_tools_from_servers_skips_failed_servers(caplog):
    import logging

    # Distinct commands so the fake_client can tell the servers apart.
    server_ok = _server(name="ok", id="ok", command="ok-cmd")
    server_bad = _server(name="bad", id="bad", command="bad-cmd")
    tool_list = [_tool("only")]

    async def fake_client(transport, config):
        if config["command"] == "bad-cmd":
            raise RuntimeError("connection refused")
        return tool_list

    with (
        patch("services.tool_executor.mcp_client", side_effect=fake_client),
        caplog.at_level(logging.ERROR, logger="services.tool_executor"),
    ):
        configs = await MCPToolsBuilder.build_tools_from_servers(
            bot_id="bot-1", mcp_servers=[server_ok, server_bad]
        )

    # Only the working server contributed tools.
    assert len(configs) == 1
    # And we logged the failure for the bad server.
    assert any("bad" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_build_tools_from_servers_empty_input_returns_empty():
    configs = await MCPToolsBuilder.build_tools_from_servers(bot_id="bot-1", mcp_servers=[])
    assert configs == []


@pytest.mark.asyncio
async def test_build_tools_from_servers_concatenates_across_servers():
    server_a = _server(name="a", id="a", command="a-cmd")
    server_b = _server(name="b", id="b", command="b-cmd")

    async def fake_client(transport, config):
        return [_tool(f"{config['command']}_tool")]

    with patch("services.tool_executor.mcp_client", side_effect=fake_client):
        configs = await MCPToolsBuilder.build_tools_from_servers(
            bot_id="bot-1", mcp_servers=[server_a, server_b]
        )

    tool_names = sorted(c.tool["function"]["name"] for c in configs)
    assert tool_names == ["a-cmd_tool", "b-cmd_tool"]


@pytest.mark.asyncio
async def test_build_tools_from_servers_uses_redis_cache():
    """The tool list is cached per (bot_id, server_id) so repeat
    calls don't re-query the MCP server.
    """
    server = _server()
    tool_list = [_tool("cached_tool")]

    with patch(
        "services.tool_executor.mcp_client",
        AsyncMock(return_value=tool_list),
    ) as mcp_client_mock:
        # First call: hits the MCP client.
        configs_first = await MCPToolsBuilder.build_tools_from_servers(
            bot_id="bot-1", mcp_servers=[server]
        )
        # Second call: should be served from the cache, NOT the client.
        configs_second = await MCPToolsBuilder.build_tools_from_servers(
            bot_id="bot-1", mcp_servers=[server]
        )

    # The MCP client is queried only once across the two calls.
    assert mcp_client_mock.call_count == 1
    assert len(configs_first) == 1
    assert len(configs_second) == 1
    # The cached tool carries the same payload (Django's LocMemCache
    # serialises on set/get, so identity is not preserved across the
    # boundary, but the values match exactly).
    assert configs_first[0].model_dump() == configs_second[0].model_dump()


@pytest.mark.asyncio
async def test_build_tools_from_servers_cache_keyed_per_bot():
    """Two bots pointing at the same MCP server get independent caches."""
    server = _server()
    tool_list = [_tool("shared")]

    with patch(
        "services.tool_executor.mcp_client",
        AsyncMock(return_value=tool_list),
    ) as mcp_client_mock:
        await MCPToolsBuilder.build_tools_from_servers(bot_id="bot-A", mcp_servers=[server])
        await MCPToolsBuilder.build_tools_from_servers(bot_id="bot-B", mcp_servers=[server])

    # Both bots had to query the MCP client because their cache keys differ.
    assert mcp_client_mock.call_count == 2


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

    with patch(
        "services.tool_executor.asyncio.run",
        return_value="hello",
    ):
        result = executor.execute_tool_call_sync({"name": "foo"})

    assert result == "hello"


def test_execute_tool_call_sync_returns_failed_marker_on_exception():
    cfg = MCPToolConfig(tool=_tool("foo"), config={}, transport_type="L")
    executor = ToolExecutor([cfg])

    with patch(
        "services.tool_executor.asyncio.run",
        side_effect=RuntimeError("boom"),
    ):
        result = executor.execute_tool_call_sync({"name": "foo"})

    assert result == TOOL_EXECUTION_FAILED


def test_execute_tool_call_sync_handles_missing_name_gracefully():
    executor = ToolExecutor([])
    result = executor.execute_tool_call_sync({})
    assert INVALID_TOOL.format(tool=None) in result or INVALID_TOOL.format(tool="None") in result


# ──────────────────────────────────────────────
# ToolExecutor.execute_default_tool
# ──────────────────────────────────────────────


def _default_executor():
    return ToolExecutor([MCPToolConfig(tool=_tool("foo"), config={}, transport_type="L")])


def test_execute_default_tool_returns_invalid_tool_when_unknown():
    executor = _default_executor()
    result = executor.execute_default_tool({"name": "not_a_real_tool", "arguments": {}})
    assert result == INVALID_TOOL.format(tool="not_a_real_tool")


def test_execute_default_tool_invokes_mapped_callable():
    """When the tool name is in FUNCTION_NAME_TO_CALLABLE_MAP the
    underlying callable is invoked with the supplied arguments.
    """
    name = next(iter(FUNCTION_NAME_TO_CALLABLE_MAP))
    captured = {}

    def fake(**kwargs):
        captured["kwargs"] = kwargs
        return "result-text"

    with patch.dict(FUNCTION_NAME_TO_CALLABLE_MAP, {name: fake}, clear=False):
        executor = _default_executor()
        result = executor.execute_default_tool(
            {"name": name, "arguments": {"x": 1}}, bot_id="bot-1"
        )

    assert result == "result-text"
    assert captured["kwargs"]["x"] == 1


def test_execute_default_tool_injects_bot_id_for_cron_tools():
    """The cron job tools have 'bot_id' injected before the call."""
    # Pick a cron tool that requires bot_id (any non-web/non-PDF tool).
    cron_tool_name = "list_cron_jobs"
    captured = {}

    def fake(bot_id, **kwargs):
        captured["bot_id"] = bot_id
        captured["kwargs"] = kwargs
        return "ok"

    with patch.dict(FUNCTION_NAME_TO_CALLABLE_MAP, {cron_tool_name: fake}, clear=False):
        executor = _default_executor()
        executor.execute_default_tool(
            {"name": cron_tool_name, "arguments": {"is_active": True}},
            bot_id="bot-uuid",
        )

    assert captured["bot_id"] == "bot-uuid"
    assert captured["kwargs"]["is_active"] is True


def test_execute_default_tool_injects_telegram_client_for_pdf_tool():
    """The PDF generator tool receives the live TelegramClient."""
    captured = {}

    def fake(client, **kwargs):
        captured["client"] = client
        captured["kwargs"] = kwargs
        return "ok"

    with patch.dict(FUNCTION_NAME_TO_CALLABLE_MAP, {GENERATE_PDF: fake}, clear=False):
        executor = _default_executor()
        tg = MagicMock(name="TelegramClient")
        executor.execute_default_tool(
            {"name": GENERATE_PDF, "arguments": {"contents": "<html></html>"}},
            telegram_client=tg,
        )

    assert captured["client"] is tg
    assert captured["kwargs"]["contents"] == "<html></html>"


def test_execute_default_tool_does_not_inject_for_web_search():
    """'web_search' and 'fetch_and_extract' take no injected context."""
    for name in (WEB_SEARCH, FETCH_AND_EXTRACT):
        captured = {}

        def fake(**kwargs):
            captured["kwargs"] = kwargs
            return "search-result"

        with patch.dict(FUNCTION_NAME_TO_CALLABLE_MAP, {name: fake}, clear=False):
            executor = _default_executor()
            executor.execute_default_tool(
                {"name": name, "arguments": {"query": "hi"}},
                bot_id="bot-1",
                telegram_client=MagicMock(),
            )

        # The callable was called without bot_id or telegram_client.
        assert "bot_id" not in captured["kwargs"]
        assert "client" not in captured["kwargs"]


def test_execute_default_tool_returns_failed_marker_on_exception():
    name = next(iter(FUNCTION_NAME_TO_CALLABLE_MAP))

    def fake(**kwargs):
        raise RuntimeError("callable boom")

    with patch.dict(FUNCTION_NAME_TO_CALLABLE_MAP, {name: fake}, clear=False):
        executor = _default_executor()
        result = executor.execute_default_tool({"name": name, "arguments": {}}, bot_id="bot-1")

    assert result.startswith(TOOL_EXECUTION_FAILED)
    assert "callable boom" in result


def test_execute_default_tool_returns_none_when_callable_returns_none():
    """If the underlying callable returns None, the executor returns None."""
    name = next(iter(FUNCTION_NAME_TO_CALLABLE_MAP))

    def fake(**kwargs):
        return None

    with patch.dict(FUNCTION_NAME_TO_CALLABLE_MAP, {name: fake}, clear=False):
        executor = _default_executor()
        result = executor.execute_default_tool({"name": name, "arguments": {}}, bot_id="bot-1")

    assert result is None


# ──────────────────────────────────────────────
# Construction
# ──────────────────────────────────────────────


def test_tool_executor_stores_tools():
    tools = [MCPToolConfig(tool=_tool("a"), config={}, transport_type="L")]
    executor = ToolExecutor(tools)
    assert executor.tools is tools


def test_tool_executor_with_empty_list_is_valid():
    executor = ToolExecutor([])
    assert executor.find_tool_by_call({"name": "any"}) is None
    assert executor.execute_tool_call_sync({"name": "any"}) == INVALID_TOOL.format(tool="any")
