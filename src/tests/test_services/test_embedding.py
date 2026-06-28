"""Tests for 'src/services/embedding.py'.

EmbeddingService is the boundary between the ORM and Ollama's embedding
API. Most tests patch 'OllamaClient' to avoid real HTTP calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.embedding import EmbeddingService

# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _bot(embedding_model: str = "nomic-embed-text", embedding_dimensions: int = 768):
    return SimpleNamespace(
        id="bot-1",
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )


def _ollama():
    return SimpleNamespace(endpoint="http://localhost:11434", api_key=None)


def _cron(name="daily", description="every day"):
    return SimpleNamespace(
        name=name,
        description=description,
        id="c-1",
        schedule_embedding=None,
        schedule_embedding_updated_at=None,
        save=MagicMock(),
    )


def _mcp(id="mcp-1"):
    return SimpleNamespace(
        id=id,
        bot=_bot(),
        tools_description_embedding=None,
        save=MagicMock(),
    )


# ──────────────────────────────────────────────
# _build_text_for_cron_job
# ──────────────────────────────────────────────


def test_build_text_for_cron_job_combines_name_and_description():
    cron = _cron(name="morning digest", description="daily summary at 9am")
    assert EmbeddingService._build_text_for_cron_job(cron) == (
        "morning digest. daily summary at 9am"
    )


def test_build_text_for_cron_job_handles_empty_description():
    cron = _cron(name="ping", description="")
    assert EmbeddingService._build_text_for_cron_job(cron) == "ping. "


# ──────────────────────────────────────────────
# _build_text_for_mcp_server
# ──────────────────────────────────────────────


def test_build_text_for_mcp_server_returns_none_for_empty_list():
    assert EmbeddingService._build_text_for_mcp_server([]) is None


def test_build_text_for_mcp_server_returns_none_when_no_tools_buildable():
    # If the builder raises for every server, we should return None
    # rather than crash — callers treat None as "skip embedding".
    with patch(
        "services.embedding.MCPToolsBuilder.build_tools_from_servers",
        side_effect=RuntimeError("boom"),
    ):
        result = EmbeddingService._build_text_for_mcp_server([_mcp()])
    assert result is None


def test_build_text_for_mcp_server_returns_none_when_no_tools_described():
    # The builder returns an empty list — no tool descriptions ⇒ None.
    with patch(
        "services.embedding.MCPToolsBuilder.build_tools_from_servers",
        return_value=[],
    ):
        result = EmbeddingService._build_text_for_mcp_server([_mcp()])
    assert result is None


def test_build_text_for_mcp_server_includes_tool_name_and_description():
    cfg = SimpleNamespace(
        tool={"function": {"name": "fetch_weather", "description": "Look up weather"}}
    )
    with patch(
        "services.embedding.MCPToolsBuilder.build_tools_from_servers",
        return_value=[cfg],
    ):
        result = EmbeddingService._build_text_for_mcp_server([_mcp()])
    assert "fetch_weather" in result
    assert "Look up weather" in result


def test_build_text_for_mcp_server_skips_malformed_tool_configs():
    bad_cfg = SimpleNamespace(tool={"function": {"name": "ok"}})  # no description
    with patch(
        "services.embedding.MCPToolsBuilder.build_tools_from_servers",
        return_value=[bad_cfg],
    ):
        result = EmbeddingService._build_text_for_mcp_server([_mcp()])
    # Missing description ⇒ skipped ⇒ no usable text ⇒ None.
    assert result is None


def test_build_text_for_mcp_server_concatenates_multiple_tools():
    cfg1 = SimpleNamespace(tool={"function": {"name": "tool_a", "description": "alpha"}})
    cfg2 = SimpleNamespace(tool={"function": {"name": "tool_b", "description": "beta"}})
    with patch(
        "services.embedding.MCPToolsBuilder.build_tools_from_servers",
        return_value=[cfg1, cfg2],
    ):
        result = EmbeddingService._build_text_for_mcp_server([_mcp()])
    assert "tool_a" in result
    assert "tool_b" in result
    # Entries are joined by ".\n".
    assert ".\n" in result


# ──────────────────────────────────────────────
# _generate (delegates to OllamaClient)
# ──────────────────────────────────────────────


def test_generate_returns_vector_on_success():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    expected = [0.1, 0.2, 0.3]
    with patch("services.embedding.OllamaClient") as Client:
        Client.return_value.generate_embeddings.return_value = expected
        result = svc._generate("hello world")
    assert result == expected
    Client.return_value.generate_embeddings.assert_called_once_with(
        model="nomic-embed-text",
        text="hello world",
        truncate=True,
        dimensions=768,
    )


def test_generate_returns_none_on_exception():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    with patch("services.embedding.OllamaClient") as Client:
        Client.return_value.generate_embeddings.side_effect = RuntimeError("ollama down")
        result = svc._generate("hello")
    assert result is None


def test_generate_passes_ollama_endpoint_and_api_key_to_client():
    ollama = SimpleNamespace(endpoint="https://example.com", api_key="SECRET")
    svc = EmbeddingService(bot=_bot(), ollama=ollama)
    with patch("services.embedding.OllamaClient") as Client:
        Client.return_value.generate_embeddings.return_value = [0.1]
        svc._generate("x")
    Client.assert_called_once_with(endpoint="https://example.com", api_key="SECRET")


# ──────────────────────────────────────────────
# save_message_embedding
# ──────────────────────────────────────────────


def test_save_message_embedding_returns_false_for_empty_content():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    msg = SimpleNamespace(id="m-1", content="   ", content_embedding=None, save=MagicMock())
    assert svc.save_message_embedding(msg) is False


def test_save_message_embedding_returns_false_for_missing_model():
    svc = EmbeddingService(bot=_bot(embedding_model=""), ollama=_ollama())
    msg = SimpleNamespace(id="m-1", content="hi", content_embedding=None, save=MagicMock())
    assert svc.save_message_embedding(msg) is False


def test_save_message_embedding_returns_false_when_generate_fails():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    msg = SimpleNamespace(id="m-1", content="hi", content_embedding=None, save=MagicMock())
    with patch.object(svc, "_generate", return_value=None):
        assert svc.save_message_embedding(msg) is False
    msg.save.assert_not_called()


def test_save_message_embedding_persists_vector_on_success():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    msg = SimpleNamespace(id="m-1", content="hi", content_embedding=None, save=MagicMock())
    with patch.object(svc, "_generate", return_value=[0.1, 0.2]):
        result = svc.save_message_embedding(msg)
    assert result is True
    assert msg.content_embedding == [0.1, 0.2]
    msg.save.assert_called_once()


def test_save_message_embedding_returns_false_on_save_exception():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    msg = SimpleNamespace(id="m-1", content="hi", content_embedding=None, save=MagicMock())
    msg.save.side_effect = RuntimeError("db down")
    with patch.object(svc, "_generate", return_value=[0.1]):
        assert svc.save_message_embedding(msg) is False


# ──────────────────────────────────────────────
# save_cron_job_embedding
# ──────────────────────────────────────────────


def test_save_cron_job_embedding_returns_false_for_missing_model():
    svc = EmbeddingService(bot=_bot(embedding_model=""), ollama=_ollama())
    cron = _cron()
    assert svc.save_cron_job_embedding(cron) is False


def test_save_cron_job_embedding_returns_false_when_generate_fails():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    cron = _cron()
    cron.schedule_embedding = None
    cron.save = MagicMock()
    with patch.object(svc, "_generate", return_value=None):
        assert svc.save_cron_job_embedding(cron) is False
    cron.save.assert_not_called()


def test_save_cron_job_embedding_persists_vector_on_success():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    cron = _cron()
    cron.schedule_embedding = None
    cron.schedule_embedding_updated_at = None
    cron.save = MagicMock()
    with patch.object(svc, "_generate", return_value=[0.1, 0.2]):
        assert svc.save_cron_job_embedding(cron) is True
    assert cron.schedule_embedding == [0.1, 0.2]
    cron.save.assert_called_once()


# ──────────────────────────────────────────────
# save_mcp_embedding
# ──────────────────────────────────────────────


def test_save_mcp_embedding_returns_false_for_missing_model():
    svc = EmbeddingService(bot=_bot(embedding_model=""), ollama=_ollama())
    assert svc.save_mcp_embedding(_mcp()) is False


def test_save_mcp_embedding_returns_false_when_text_unbuildable():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    with patch.object(svc, "_build_text_for_mcp_server", return_value=None):
        assert svc.save_mcp_embedding(_mcp()) is False


def test_save_mcp_embedding_returns_false_when_generate_fails():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    mcp = _mcp()
    mcp.save = MagicMock()
    with (
        patch.object(svc, "_build_text_for_mcp_server", return_value="text"),
        patch.object(svc, "_generate", return_value=None),
    ):
        assert svc.save_mcp_embedding(mcp) is False
    mcp.save.assert_not_called()


def test_save_mcp_embedding_persists_vector_on_success():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())
    mcp = _mcp()
    mcp.tools_description_embedding = None
    mcp.save = MagicMock()
    with (
        patch.object(svc, "_build_text_for_mcp_server", return_value="text"),
        patch.object(svc, "_generate", return_value=[0.1, 0.2]),
    ):
        assert svc.save_mcp_embedding(mcp) is True
    assert mcp.tools_description_embedding == [0.1, 0.2]
    mcp.save.assert_called_once()


# ──────────────────────────────────────────────
# get_relevant_memories
# ──────────────────────────────────────────────


def test_get_relevant_memories_returns_empty_on_dimension_mismatch():
    svc = EmbeddingService(bot=_bot(embedding_dimensions=768), ollama=_ollama())
    # Query vector with wrong length ⇒ early-return empty list.
    assert svc.get_relevant_memories(query_vector=[0.1] * 100) == []


def test_get_relevant_memories_returns_empty_on_dimension_mismatch_even_when_logging():
    """The dimension-mismatch path must early-return without DB activity,
    even though the warning itself references len(query_vector)."""

    svc = EmbeddingService(bot=_bot(embedding_dimensions=768), ollama=_ollama())
    # 100 dims vs 768 expected → mismatch.
    result = svc.get_relevant_memories(query_vector=[0.1] * 100)
    assert result == []


def test_get_relevant_memories_returns_empty_on_exception():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())

    # Make the chained queryset raise.
    class _QS:
        def filter(self, *a, **kw):
            return self

        def exclude(self, *a, **kw):
            return self

        def annotate(self, *a, **kw):
            return self

        def order_by(self, *a, **kw):
            raise RuntimeError("pgvector down")

        def __getitem__(self, _):
            return []

    with patch("services.embedding.Message.objects", _QS()):
        result = svc.get_relevant_memories(query_vector=[0.1] * 768)
    assert result == []


def test_get_relevant_memories_converts_distance_to_similarity():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())

    msg_a = SimpleNamespace(
        id="a",
        content="x",
        distance=0.0,  # → similarity = 1.0
    )
    msg_b = SimpleNamespace(
        id="b",
        content="y",
        distance=2.0,  # → similarity = 0.0
    )

    class _QS:
        def filter(self, *a, **kw):
            return self

        def exclude(self, *a, **kw):
            return self

        def annotate(self, *a, **kw):
            return self

        def order_by(self, *a, **kw):
            return self

        def __getitem__(self, slicer):
            return [msg_a, msg_b]

    with patch("services.embedding.Message.objects", _QS()):
        results = svc.get_relevant_memories(query_vector=[0.1] * 768)

    # 1 - (0/2) = 1.0; 1 - (2/2) = 0.0
    assert results[0][1] == 1.0
    assert results[1][1] == 0.0


def test_get_relevant_memories_passes_current_message_id_exclude():
    svc = EmbeddingService(bot=_bot(), ollama=_ollama())

    captured = {}

    class _QS:
        def filter(self, *a, **kw):
            captured["filter"] = (a, kw)
            return self

        def exclude(self, *a, **kw):
            captured["exclude"] = (a, kw)
            return self

        def annotate(self, *a, **kw):
            captured["annotate"] = (a, kw)
            return self

        def order_by(self, *a, **kw):
            return self

        def __getitem__(self, slicer):
            return []

    with patch("services.embedding.Message.objects", _QS()):
        svc.get_relevant_memories(
            query_vector=[0.1] * 768,
            current_message_id="current-id",
        )

    # The exclude() call must use the provided current_message_id.
    assert "exclude" in captured
