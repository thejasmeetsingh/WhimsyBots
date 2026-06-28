"""Tests for 'src/app/forms.py'.

The form layer is mostly glue between Django's ModelForm machinery and
the Ollama model cache. We patch the cache helpers + the Ollama client
to avoid any HTTP / Redis access.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.forms import (
    EMPTY_MODEL_CHOICES,
    MCPServerForm,
    Model,
    fetch_ollama_models_with_capabilities,
    get_filtered_models,
    get_models_from_cache,
    save_models_to_cache,
)

# ──────────────────────────────────────────────
# Module-level constants
# ──────────────────────────────────────────────


def test_empty_model_choices_has_default_entry():
    assert EMPTY_MODEL_CHOICES == [(None, "Select a model")]


# ──────────────────────────────────────────────
# Model pydantic class
# ──────────────────────────────────────────────


def test_model_pydantic_validates_both_fields():
    m = Model(model="llama3", is_embedding=False)
    assert m.model == "llama3"
    assert m.is_embedding is False


def test_model_pydantic_rejects_missing_field():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Model(model="llama3")


# ──────────────────────────────────────────────
# fetch_ollama_models_with_capabilities
# ──────────────────────────────────────────────


def test_fetch_ollama_models_returns_empty_on_exception():
    with patch("app.forms.OllamaClient") as Client:
        Client.return_value.list_models.side_effect = RuntimeError("ollama down")
        result = fetch_ollama_models_with_capabilities("http://x", None)
    assert result == []


def test_fetch_ollama_models_includes_tool_and_embedding_capable_models():
    # Mock client returns two model names; capabilities fetch returns
    # different tags per model.
    with patch("app.forms.OllamaClient") as Client:
        Client.return_value.list_models.return_value = ["llama3", "nomic-embed"]
        Client.return_value.fetch_model_capabilities.side_effect = [
            ["tools"],
            ["embedding"],
        ]
        result = fetch_ollama_models_with_capabilities("http://x", None)

    names = [m.model for m in result]
    assert names == ["llama3", "nomic-embed"]
    # Only the embed model is flagged as embedding-capable.
    assert result[0].is_embedding is False
    assert result[1].is_embedding is True


def test_fetch_ollama_models_skips_models_with_no_capabilities():
    with patch("app.forms.OllamaClient") as Client:
        Client.return_value.list_models.return_value = ["unflagged"]
        Client.return_value.fetch_model_capabilities.return_value = []
        result = fetch_ollama_models_with_capabilities("http://x", None)
    # No capabilities ⇒ not tools/embedding ⇒ skipped.
    assert result == []


def test_fetch_ollama_models_continues_past_individual_failures():
    """A failure on one model's capabilities fetch must not abort the loop."""
    with patch("app.forms.OllamaClient") as Client:
        Client.return_value.list_models.return_value = ["a", "b", "c"]
        Client.return_value.fetch_model_capabilities.side_effect = [
            ["tools"],
            RuntimeError("one model failed"),
            ["embedding"],
        ]
        result = fetch_ollama_models_with_capabilities("http://x", None)

    # Both the working models are returned; the failed one is silently dropped.
    names = [m.model for m in result]
    assert names == ["a", "c"]


# ──────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────


def test_get_models_from_cache_returns_none_on_missing():
    with patch("app.forms.cache") as cache:
        cache.get.return_value = None
        assert get_models_from_cache() is None


def test_get_models_from_cache_returns_validated_models():
    cached = [{"model": "llama3", "is_embedding": False}]
    with patch("app.forms.cache") as cache:
        cache.get.return_value = cached
        result = get_models_from_cache()
    assert len(result) == 1
    assert result[0].model == "llama3"


def test_get_models_from_cache_returns_none_on_exception():
    """A corrupt cache entry should NOT crash the form — return None
    so the form falls back to a fresh fetch."""
    with patch("app.forms.cache") as cache:
        cache.get.side_effect = RuntimeError("redis down")
        assert get_models_from_cache() is None


def test_save_models_to_cache_returns_true_on_success():
    models = [Model(model="llama3", is_embedding=False)]
    with patch("app.forms.cache") as cache:
        assert save_models_to_cache(models) is True
    cache.set.assert_called_once()


def test_save_models_to_cache_returns_false_on_exception():
    with patch("app.forms.cache") as cache:
        cache.set.side_effect = RuntimeError("redis down")
        assert save_models_to_cache([]) is False


# ──────────────────────────────────────────────
# get_filtered_models
# ──────────────────────────────────────────────


def test_get_filtered_models_returns_only_matching_kind():
    models = [
        Model(model="llama3", is_embedding=False),
        Model(model="nomic-embed", is_embedding=True),
    ]
    chat_choices = get_filtered_models(models, is_embedding=False)
    embed_choices = get_filtered_models(models, is_embedding=True)

    assert chat_choices == [("llama3", "llama3")]
    assert embed_choices == [("nomic-embed", "nomic-embed")]


def test_get_filtered_models_returns_empty_when_none_match():
    models = [Model(model="llama3", is_embedding=False)]
    assert get_filtered_models(models, is_embedding=True) == []


def test_get_filtered_models_handles_empty_input():
    assert get_filtered_models([], is_embedding=False) == []


# ──────────────────────────────────────────────
# MCPServerForm
# ──────────────────────────────────────────────


def test_mcp_server_form_has_secrets_as_json_field():
    """'secrets' is a JSONField on the form so the admin can pass
    arbitrary JSON in for env-vars / headers."""

    form = MCPServerForm()
    # The form must expose 'secrets' as a JSONField (not the model's
    # EncryptedJSONField — that's why the form exists at all).
    from django.forms import JSONField

    assert isinstance(form.fields["secrets"], JSONField)
    assert form.fields["secrets"].required is False
