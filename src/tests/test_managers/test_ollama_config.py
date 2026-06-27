"""Tests for `src/managers/ollama_config.py` (Tier 2 - service logic with mocks).

The class caches the Ollama config at module level, so every test
needs an `autouse` fixture to reset that cache before each run.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from managers.ollama_config import OllamaConfigManager


# ──────────────────────────────────────────────
# cache reset fixture
# ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the module-level cache before and after every test."""

    OllamaConfigManager._cached_ollama = None
    yield
    OllamaConfigManager._cached_ollama = None


# ──────────────────────────────────────────────
# get_ollama_config
# ──────────────────────────────────────────────


def test_get_ollama_config_returns_db_row_when_present():
    ollama_cfg = object()  # sentinel - could be any object
    with patch("app.models.Ollama.objects.first", return_value=ollama_cfg):
        result = OllamaConfigManager.get_ollama_config()
    assert result is ollama_cfg


def test_get_ollama_config_returns_none_when_no_row():
    with patch("app.models.Ollama.objects.first", return_value=None):
        result = OllamaConfigManager.get_ollama_config()
    assert result is None


def test_get_ollama_config_caches_after_first_call():
    """After the first call, subsequent calls must use the cache
    rather than hitting the DB again."""

    ollama_cfg = object()
    with patch(
        "app.models.Ollama.objects.first", return_value=ollama_cfg
    ) as mock_first:
        OllamaConfigManager.get_ollama_config()
        OllamaConfigManager.get_ollama_config()
        OllamaConfigManager.get_ollama_config()

    # `Ollama.objects.first()` should have been called exactly once.
    assert mock_first.call_count == 1


def test_get_ollama_config_uses_cache_when_populated():
    # Manually seed the cache and verify the DB is never queried.
    cached = object()
    OllamaConfigManager._cached_ollama = cached

    with patch("app.models.Ollama.objects.first") as mock_first:
        result = OllamaConfigManager.get_ollama_config()

    assert result is cached
    mock_first.assert_not_called()


def test_get_ollama_config_propagates_db_exceptions():
    # Caching None would mask DB outages, so the helper must let
    # the exception propagate.
    with patch("app.models.Ollama.objects.first", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            OllamaConfigManager.get_ollama_config()


def test_get_ollama_config_re_queries_when_first_returns_none():
    # If first() returns None, we should still query the DB on the next
    # call (do not permanently cache the absence).
    mock_first = MagicMock(side_effect=[None, object()])
    with patch("app.models.Ollama.objects.first", mock_first):
        assert OllamaConfigManager.get_ollama_config() is None
        assert OllamaConfigManager.get_ollama_config() is not None

    assert mock_first.call_count == 2
