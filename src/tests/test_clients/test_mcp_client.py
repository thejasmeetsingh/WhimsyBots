"""Tests for `src/clients/mcp.py` (Tier 4 — External-System Clients).

MCPClient wraps the `mcp` Python SDK and connects to MCP servers over
either stdio (local) or streamable-HTTP (remote). The local
[`conftest.py`](src/tests/test_clients/conftest.py) installs lightweight
stand-ins for the `mcp` SDK package so these tests run outside Docker.

Patching strategy:
    We patch symbols at their *use site* — `clients.mcp.<name>` — not at
    the `mcp.*` import site. That way the conftest stubs keep the import
    graph happy while each test controls individual symbols.

Highlights from the test plan:
  - LOCAL (L) → stdio_client; REMOTE (R) → streamable_http_client
  - MCP `inputSchema` → Ollama `{type:function,function:{name,description,parameters}}`
  - `mcp_client()` convenience: payload=None → list_tools, else execute_tool
  - Session is cached on `_connect()` for reuse
  - `cleanup()` closes the AsyncExitStack

NOTE on `AsyncExitStack.enter_async_context`:
    Real-world async context managers' `__aenter__` returns the managed
    object (a `ClientSession`). When we mock `ClientSession` we must use a
    *real* async context manager class so `enter_async_context` yields our
    session mock — not an auto-generated child `AsyncMock`. The
    `_FakeSessionCM` helper below handles this.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.mcp import MCPClient, mcp_client


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


class _FakeSessionCM:
    """Async context manager whose ``__aenter__`` returns ``session``."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return None


def _make_session():
    """Return a freshly mocked ClientSession-shaped object."""
    session = MagicMock(name="ClientSession")
    session.initialize = AsyncMock(return_value=None)
    return session


def _make_tool(name: str = "get_weather", description: str = "Get weather"):
    """Build a fake MCP tool entry returned by ``session.list_tools()``."""
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )


def _tool_list_response(*tools):
    return SimpleNamespace(tools=list(tools))


def _call_tool_response(text: str = "sunny", is_error: bool = False):
    """Build a fake ``session.call_tool()`` response with a ``model_dump``."""
    obj = SimpleNamespace(isError=is_error, content=[{"type": "text", "text": text}])
    obj.model_dump = MagicMock(
        return_value={"isError": is_error, "content": [{"type": "text", "text": text}]}
    )
    return obj


def _patch_stdio(monkeypatch, *, session=None):
    """Patch stdio_client + ClientSession and return (session, read, write)."""
    if session is None:
        session = _make_session()
    read = MagicMock(name="read")
    write = MagicMock(name="write")

    @asynccontextmanager
    async def _stdio(params):
        yield read, write

    monkeypatch.setattr("clients.mcp.stdio_client", _stdio)
    monkeypatch.setattr(
        "clients.mcp.ClientSession", lambda *a, **kw: _FakeSessionCM(session)
    )
    return session, read, write


def _patch_http(monkeypatch, *, session=None, http_client=None):
    """Patch streamable_http_client + httpx.AsyncClient + ClientSession."""
    if session is None:
        session = _make_session()
    if http_client is None:
        http_client = MagicMock(name="httpx.AsyncClient")
    read = MagicMock(name="read")
    write = MagicMock(name="write")

    @asynccontextmanager
    async def _http(url, http_client=None):  # noqa: A002 - mirrors SDK signature
        yield read, write, "unused_get_session_id"

    monkeypatch.setattr("clients.mcp.streamable_http_client", _http)
    monkeypatch.setattr(
        "clients.mcp.httpx.AsyncClient", lambda headers=None: http_client
    )
    monkeypatch.setattr(
        "clients.mcp.ClientSession", lambda *a, **kw: _FakeSessionCM(session)
    )
    return session, read, write


@pytest.fixture
def local_config():
    return {
        "command": "python",
        "args": ["-m", "mcp_server"],
        "env": {"FOO": "bar"},
    }


@pytest.fixture
def remote_config():
    return {
        "url": "https://api.example.com/mcp",
        "headers": {"Authorization": "Bearer token-123"},
    }


# ──────────────────────────────────────────────
# __init__
# ──────────────────────────────────────────────


def test_init_stores_transport_type_and_config(local_config):
    client = MCPClient("L", local_config)
    assert client.transport_type == "L"
    assert client.config == local_config


def test_init_creates_async_exit_stack(local_config):
    client = MCPClient("L", local_config)
    # AsyncExitStack() — just verify it exists and is not None.
    assert client.exit_stack is not None


def test_init_starts_with_no_session(local_config):
    client = MCPClient("L", local_config)
    assert client._session is None


# ──────────────────────────────────────────────
# _connect — LOCAL
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_local_uses_stdio_client(monkeypatch, local_config):
    session, _read, _write = _patch_stdio(monkeypatch)

    client = MCPClient("L", local_config)
    await client._connect()

    session.initialize.assert_awaited_once()
    assert client._session is session


@pytest.mark.asyncio
async def test_connect_local_builds_stdio_server_parameters(monkeypatch, local_config):
    """Verify StdioServerParameters receives the raw config dict."""
    captured: dict = {}

    def _params_ctor(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    _patch_stdio(monkeypatch)
    monkeypatch.setattr("clients.mcp.StdioServerParameters", _params_ctor)

    client = MCPClient("L", local_config)
    await client._connect()

    assert captured == local_config


@pytest.mark.asyncio
async def test_connect_caches_session_on_second_call(monkeypatch, local_config):
    """Once connected, `_connect()` must reuse the cached session."""
    session = _make_session()
    call_count = {"n": 0}

    def _cs_factory(*a, **kw):
        call_count["n"] += 1
        return _FakeSessionCM(session)

    @asynccontextmanager
    async def _stdio(params):
        yield MagicMock(), MagicMock()

    monkeypatch.setattr("clients.mcp.stdio_client", _stdio)
    monkeypatch.setattr("clients.mcp.ClientSession", _cs_factory)

    client = MCPClient("L", local_config)
    await client._connect()
    await client._connect()

    # ClientSession is constructed exactly once → session is cached.
    assert call_count["n"] == 1


# ──────────────────────────────────────────────
# _connect — REMOTE
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_remote_uses_streamable_http_client(monkeypatch, remote_config):
    session, _read, _write = _patch_http(monkeypatch)

    client = MCPClient("R", remote_config)
    await client._connect()

    session.initialize.assert_awaited_once()
    assert client._session is session


@pytest.mark.asyncio
async def test_connect_remote_creates_httpx_async_client_with_headers(
    monkeypatch, remote_config
):
    """The HTTP client's headers come from config['headers']."""
    captured: dict = {}

    def _client_ctor(headers=None):
        captured["headers"] = headers
        return MagicMock()

    _patch_http(monkeypatch)
    monkeypatch.setattr("clients.mcp.httpx.AsyncClient", _client_ctor)

    client = MCPClient("R", remote_config)
    await client._connect()

    assert captured["headers"] == {"Authorization": "Bearer token-123"}


# ──────────────────────────────────────────────
# list_tools — schema conversion
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tools_converts_mcp_schema_to_ollama_format(
    monkeypatch, local_config
):
    session, _, _ = _patch_stdio(monkeypatch)
    session.list_tools = AsyncMock(
        return_value=_tool_list_response(
            _make_tool("get_weather", "Get weather for a city")
        )
    )

    client = MCPClient("L", local_config)
    tools = await client.list_tools()

    assert tools == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_list_tools_returns_empty_list_when_server_has_none(
    monkeypatch, local_config
):
    session, _, _ = _patch_stdio(monkeypatch)
    session.list_tools = AsyncMock(return_value=_tool_list_response())

    client = MCPClient("L", local_config)
    tools = await client.list_tools()

    assert tools == []


@pytest.mark.asyncio
async def test_list_tools_uses_default_object_when_type_missing(
    monkeypatch, local_config
):
    """MCP inputSchema is expected to have 'type' but defaults to 'object'."""
    session, _, _ = _patch_stdio(monkeypatch)
    session.list_tools = AsyncMock(
        return_value=SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="t",
                    description="d",
                    inputSchema={"properties": {}, "required": []},
                )
            ]
        )
    )

    client = MCPClient("L", local_config)
    tools = await client.list_tools()

    assert tools[0]["function"]["parameters"]["type"] == "object"
    assert tools[0]["function"]["parameters"]["properties"] == {}


@pytest.mark.asyncio
async def test_list_tools_supports_remote_transport(monkeypatch, remote_config):
    session, _, _ = _patch_http(monkeypatch)
    session.list_tools = AsyncMock(
        return_value=_tool_list_response(_make_tool("get_time"))
    )

    client = MCPClient("R", remote_config)
    tools = await client.list_tools()

    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "get_time"


# ──────────────────────────────────────────────
# execute_tool
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_returns_model_dump(monkeypatch, local_config):
    session, _, _ = _patch_stdio(monkeypatch)
    session.call_tool = AsyncMock(
        return_value=_call_tool_response(text="sunny, 25°C", is_error=False)
    )

    client = MCPClient("L", local_config)
    result = await client.execute_tool("get_weather", {"city": "NYC"})

    session.call_tool.assert_awaited_once_with("get_weather", {"city": "NYC"})
    assert result == {
        "isError": False,
        "content": [{"type": "text", "text": "sunny, 25°C"}],
    }


@pytest.mark.asyncio
async def test_execute_tool_supports_no_arguments(monkeypatch, local_config):
    session, _, _ = _patch_stdio(monkeypatch)
    session.call_tool = AsyncMock(return_value=_call_tool_response(text="now"))

    client = MCPClient("L", local_config)
    result = await client.execute_tool("get_current_time")

    # Source signature: call_tool(name, arguments=None)
    session.call_tool.assert_awaited_once_with("get_current_time", None)
    assert result["isError"] is False


@pytest.mark.asyncio
async def test_execute_tool_reports_errors(monkeypatch, local_config):
    session, _, _ = _patch_stdio(monkeypatch)
    session.call_tool = AsyncMock(
        return_value=_call_tool_response(text="boom", is_error=True)
    )

    client = MCPClient("L", local_config)
    result = await client.execute_tool("broken", {})

    assert result["isError"] is True
    assert result["content"][0]["text"] == "boom"


# ──────────────────────────────────────────────
# cleanup
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_closes_exit_stack(monkeypatch, local_config):
    _patch_stdio(monkeypatch)

    client = MCPClient("L", local_config)
    client.exit_stack.aclose = AsyncMock()

    await client.cleanup()

    client.exit_stack.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_clears_cached_session(monkeypatch, local_config):
    """After cleanup, ``_session`` is None — a subsequent ``_connect()`` reconnects."""
    session = _make_session()
    call_count = {"n": 0}

    def _cs_factory(*a, **kw):
        call_count["n"] += 1
        return _FakeSessionCM(session)

    @asynccontextmanager
    async def _stdio(params):
        yield MagicMock(), MagicMock()

    monkeypatch.setattr("clients.mcp.stdio_client", _stdio)
    monkeypatch.setattr("clients.mcp.ClientSession", _cs_factory)

    client = MCPClient("L", local_config)
    client.exit_stack.aclose = AsyncMock()

    await client._connect()
    assert client._session is not None

    await client.cleanup()
    assert client._session is None

    # Re-connecting after cleanup creates a new ClientSession.
    await client._connect()
    assert call_count["n"] == 2


# ──────────────────────────────────────────────
# mcp_client() — convenience wrapper
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_client_lists_tools_when_payload_is_none(monkeypatch, local_config):
    session, _, _ = _patch_stdio(monkeypatch)
    session.list_tools = AsyncMock(return_value=_tool_list_response(_make_tool("t")))

    tools = await mcp_client("L", local_config)

    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "t"


@pytest.mark.asyncio
async def test_mcp_client_executes_tool_when_payload_given(monkeypatch, local_config):
    session, _, _ = _patch_stdio(monkeypatch)
    session.call_tool = AsyncMock(return_value=_call_tool_response(text="done"))

    # Payload keys must match `execute_tool(self, name, arguments=None)` —
    # i.e. ``name`` and ``arguments`` (NOT ``args``).
    result = await mcp_client(
        "L",
        local_config,
        payload={"name": "echo", "arguments": {"msg": "hi"}},
    )

    session.call_tool.assert_awaited_once_with("echo", {"msg": "hi"})
    assert result["content"][0]["text"] == "done"


@pytest.mark.asyncio
async def test_mcp_client_supports_remote_transport(monkeypatch, remote_config):
    session, _, _ = _patch_http(monkeypatch)
    session.list_tools = AsyncMock(return_value=_tool_list_response())

    tools = await mcp_client("R", remote_config)

    assert tools == []
