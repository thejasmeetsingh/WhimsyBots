"""Tests for 'src/clients/ollama.py'.

OllamaClient wraps the official 'ollama' Python SDK. We patch
'ollama.Client' so no real HTTP calls are made.

Highlights from the test plan:
  - localhost → host.docker.internal
  - keep_alive "-1"/"0" → int, else preserved as string
  - Bearer Authorization header when 'api_key' is provided
  - Embedding list response flattening (nested → flat)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from clients.ollama import OllamaClient

# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _model(name: str = "llama3"):
    """Build a fake entry for `client.list()`."""
    return SimpleNamespace(model=name)


def _chat_response(
    content: str = "Hello there!",
    tool_calls: list | None = None,
    total_duration_ns: int | None = 1_500_000_000,
):
    """Build a fake `client.chat()` response."""
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls),
        total_duration=total_duration_ns,
    )


def _embed_response(embeddings):
    return SimpleNamespace(embeddings=embeddings)


@pytest.fixture
def client():
    """Return an OllamaClient whose `ollama.Client` is patched out."""
    with patch("clients.ollama.ollama.Client") as sdk:
        sdk.return_value = MagicMock(name="ollama.Client")
        yield OllamaClient(endpoint="http://localhost:11434")


# ──────────────────────────────────────────────
# _get_clean_endpoint
# ──────────────────────────────────────────────


def test_get_clean_endpoint_replaces_localhost_with_host_docker_internal():
    out = OllamaClient._get_clean_endpoint("http://localhost:11434")
    assert out == "http://host.docker.internal:11434"


def test_get_clean_endpoint_replaces_localhost_with_port():
    out = OllamaClient._get_clean_endpoint("http://localhost:12345/")
    assert out == "http://host.docker.internal:12345"


def test_get_clean_endpoint_preserves_remote_hostname():
    out = OllamaClient._get_clean_endpoint("https://api.example.com/")
    assert out == "https://api.example.com"


def test_get_clean_endpoint_strips_trailing_slashes():
    out = OllamaClient._get_clean_endpoint("https://api.example.com///")
    assert out == "https://api.example.com"


def test_get_clean_endpoint_handles_no_scheme_localhost():
    out = OllamaClient._get_clean_endpoint("localhost:11434")
    assert out == "host.docker.internal:11434"


# ──────────────────────────────────────────────
# __init__
# ──────────────────────────────────────────────


def test_init_uses_default_endpoint_when_empty_string():
    with patch("clients.ollama.ollama.Client") as sdk:
        sdk.return_value = MagicMock()
        OllamaClient(endpoint="")
    # Empty endpoint falls back to localhost → host.docker.internal.
    sdk.assert_called_once_with(host="http://host.docker.internal:11434", headers=None)


def test_init_uses_default_endpoint_when_none():
    with patch("clients.ollama.ollama.Client") as sdk:
        sdk.return_value = MagicMock()
        OllamaClient(endpoint=None)
    sdk.assert_called_once_with(host="http://host.docker.internal:11434", headers=None)


def test_init_adds_bearer_header_when_api_key_provided():
    with patch("clients.ollama.ollama.Client") as sdk:
        sdk.return_value = MagicMock()
        OllamaClient(endpoint="https://api.example.com", api_key="secret-key-123")
    sdk.assert_called_once_with(
        host="https://api.example.com",
        headers={"Authorization": "Bearer secret-key-123"},
    )


def test_init_no_auth_header_when_api_key_missing():
    with patch("clients.ollama.ollama.Client") as sdk:
        sdk.return_value = MagicMock()
        OllamaClient(endpoint="https://api.example.com", api_key=None)
    assert sdk.call_args.kwargs["headers"] is None


# ──────────────────────────────────────────────
# list_models
# ──────────────────────────────────────────────


def test_list_models_returns_model_names(client):
    client._client.list.return_value = SimpleNamespace(
        models=[_model("llama3"), _model("mistral"), _model("nomic-embed-text")]
    )
    assert client.list_models() == ["llama3", "mistral", "nomic-embed-text"]


def test_list_models_empty(client):
    client._client.list.return_value = SimpleNamespace(models=[])
    assert client.list_models() == []


def test_list_models_skips_entries_with_empty_model_attr(client):
    # Source uses `if model.model:` — falsy `.model` values (None or "")
    # are silently dropped from the returned list. Pin that behaviour.
    client._client.list.return_value = SimpleNamespace(
        models=[
            SimpleNamespace(model=""),
            SimpleNamespace(model=None),
            _model("llama3"),
        ]
    )
    assert client.list_models() == ["llama3"]


def test_list_models_skips_entries_missing_model_attr(client):
    # Source uses 'model.model'; a SimpleNamespace without a 'model' attr
    # raises AttributeError and that entry propagates as an exception.
    # Pin that current behaviour.
    client._client.list.return_value = SimpleNamespace(
        models=[SimpleNamespace(), _model("mistral")]
    )
    with pytest.raises(AttributeError):
        client.list_models()


# ──────────────────────────────────────────────
# chat — basic + tools + timing
# ──────────────────────────────────────────────


def test_chat_returns_message_tools_and_timing(client):
    client._client.chat.return_value = _chat_response(
        content="  hi there  ",
        total_duration_ns=2_500_000_000,
    )
    result = client.chat(
        model="llama3",
        messages=[{"role": "user", "content": "hello"}],
    )
    assert result["message"] == "hi there"  # stripped
    assert result["tools"] is None
    assert result["ollama_ms"] == 2500  # 2.5s in ms


def test_chat_handles_missing_total_duration(client):
    client._client.chat.return_value = _chat_response(total_duration_ns=None)
    result = client.chat(
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result["ollama_ms"] is None


def test_chat_extracts_tool_calls_from_response(client):
    """The client must return tool_calls as-is from the SDK message object
    (each entry keeps its `function` wrapper). Callers index `tc["function"]`
    before reading tool name / arguments.
    """
    tool_calls = [{"function": {"name": "get_weather", "arguments": {"city": "NYC"}}}]
    client._client.chat.return_value = _chat_response(
        content="checking weather",
        tool_calls=tool_calls,
    )
    result = client.chat(
        model="llama3",
        messages=[{"role": "user", "content": "weather?"}],
    )
    # Returned shape preserved — the `function` wrapper is NOT stripped.
    assert result["tools"] == [{"function": {"name": "get_weather", "arguments": {"city": "NYC"}}}]
    assert result["tools"][0]["function"]["name"] == "get_weather"


def test_chat_returns_empty_tool_calls_list(client):
    """An empty `tool_calls` list from the SDK must propagate verbatim
    (None falsy vs [] truthy matters to the coordinator — see
    `if not response.get("tools")`).
    """
    client._client.chat.return_value = _chat_response(
        content="hi",
        tool_calls=[],
    )
    result = client.chat(
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result["tools"] == []
    # Coordinator treats falsy `tools` as "no further action"; [] qualifies.
    assert not result["tools"]


def test_chat_preserves_multiple_tool_calls_in_order(client):
    tool_calls = [
        {"function": {"name": "first", "arguments": {}}},
        {"function": {"name": "second", "arguments": {}}},
        {"function": {"name": "third", "arguments": {}}},
    ]
    client._client.chat.return_value = _chat_response(
        content="multiple",
        tool_calls=tool_calls,
    )
    result = client.chat(
        model="llama3",
        messages=[{"role": "user", "content": "go"}],
    )
    assert [tc["function"]["name"] for tc in result["tools"]] == [
        "first",
        "second",
        "third",
    ]


def test_chat_forwards_keep_alive_tools_options_to_sdk(client):
    client._client.chat.return_value = _chat_response()
    client.chat(
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
        keep_alive="10m",
        tools=[{"type": "function", "function": {"name": "t"}}],
        options={"temperature": 0.5},
    )
    kwargs = client._client.chat.call_args.kwargs
    assert kwargs["model"] == "llama3"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["keep_alive"] == "10m"
    assert kwargs["tools"] == [{"type": "function", "function": {"name": "t"}}]
    assert kwargs["options"] == {"temperature": 0.5}


# ──────────────────────────────────────────────
# chat — keep_alive normalization
# ──────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [("-1", -1), ("0", 0), ("  -1  ", -1)])
def test_chat_normalises_dash_one_and_zero_to_int(client, raw, expected):
    client._client.chat.return_value = _chat_response()
    client.chat(
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
        keep_alive=raw,
    )
    assert client._client.chat.call_args.kwargs["keep_alive"] == expected


@pytest.mark.parametrize("raw", ["10m", "1h", "30s", "5"])
def test_chat_keeps_non_special_keep_alive_as_string(client, raw):
    client._client.chat.return_value = _chat_response()
    client.chat(
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
        keep_alive=raw,
    )
    assert client._client.chat.call_args.kwargs["keep_alive"] == raw


def test_chat_passes_none_keep_alive_when_unset(client):
    client._client.chat.return_value = _chat_response()
    client.chat(
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert client._client.chat.call_args.kwargs["keep_alive"] is None


# ──────────────────────────────────────────────
# generate_embeddings
# ──────────────────────────────────────────────


def test_generate_embeddings_returns_flat_list(client):
    client._client.embed.return_value = _embed_response([0.1, 0.2, 0.3])
    out = client.generate_embeddings(model="nomic-embed-text", text="hello")
    assert out == [0.1, 0.2, 0.3]


def test_generate_embeddings_flattens_nested_list(client):
    # Some Ollama endpoints return embeddings wrapped in an extra list.
    client._client.embed.return_value = _embed_response([[0.1, 0.2, 0.3]])
    out = client.generate_embeddings(model="nomic-embed-text", text="hello")
    assert out == [0.1, 0.2, 0.3]


def test_generate_embeddings_forwards_truncate_and_dimensions(client):
    client._client.embed.return_value = _embed_response([0.0])
    client.generate_embeddings(
        model="nomic-embed-text",
        text="hello",
        truncate=True,
        dimensions=512,
    )
    kwargs = client._client.embed.call_args.kwargs
    assert kwargs["truncate"] is True
    assert kwargs["dimensions"] == 512


# ──────────────────────────────────────────────
# fetch_model_capabilities
# ──────────────────────────────────────────────


def test_fetch_model_capabilities_returns_capabilities_list(client):
    client._client.show.return_value = SimpleNamespace(capabilities=["completion", "tools"])
    assert client.fetch_model_capabilities("llama3") == ["completion", "tools"]


def test_fetch_model_capabilities_empty(client):
    client._client.show.return_value = SimpleNamespace(capabilities=[])
    assert client.fetch_model_capabilities("llama3") == []
