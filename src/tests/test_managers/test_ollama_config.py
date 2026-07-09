"""Tests for 'src/managers/ollama_config.py'.

After the caching refactor, every successful call invokes
'refresh_from_db()' on the cached object so operators always see
the latest persisted settings. The module-level cache must still be
reset between tests so cached rows from one test don't leak into the
next.
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
    """When the DB has an Ollama row, return the cached (refreshed) instance."""
    ollama_cfg = MagicMock(name="Ollama")  # supports refresh_from_db
    with patch("app.models.Ollama.objects.first", return_value=ollama_cfg):
        result = OllamaConfigManager.get_ollama_config()
    assert result is ollama_cfg
    ollama_cfg.refresh_from_db.assert_called_once_with()


def test_get_ollama_config_returns_none_when_no_row():
    """When the DB has no Ollama row, the helper returns None."""
    with patch("app.models.Ollama.objects.first", return_value=None):
        result = OllamaConfigManager.get_ollama_config()
    assert result is None


def test_get_ollama_config_queries_db_only_once_for_cache_fill():
    """The first call hits the DB and caches the result; subsequent
    calls reuse the cache rather than re-querying the DB.
    """
    ollama_cfg = MagicMock(name="Ollama")
    with patch("app.models.Ollama.objects.first", return_value=ollama_cfg) as mock_first:
        OllamaConfigManager.get_ollama_config()
        OllamaConfigManager.get_ollama_config()
        OllamaConfigManager.get_ollama_config()

    # 'Ollama.objects.first()' is called exactly once.
    assert mock_first.call_count == 1


def test_get_ollama_config_refreshes_from_db_on_every_call():
    """Even when the cache is populated, every call invokes
    'refresh_from_db()' so the caller always sees the latest values.
    """
    ollama_cfg = MagicMock(name="Ollama")
    with patch("app.models.Ollama.objects.first", return_value=ollama_cfg) as mock_first:
        OllamaConfigManager.get_ollama_config()
        OllamaConfigManager.get_ollama_config()
        OllamaConfigManager.get_ollama_config()

    # 'Ollama.objects.first()' called only once but 'refresh_from_db' is
    # invoked on every call.
    assert mock_first.call_count == 1
    assert ollama_cfg.refresh_from_db.call_count == 3


def test_get_ollama_config_uses_cache_when_populated():
    """Manually seeding the cache bypasses the DB query entirely."""
    cached = MagicMock(name="CachedOllama")
    OllamaConfigManager._cached_ollama = cached

    with patch("app.models.Ollama.objects.first") as mock_first:
        result = OllamaConfigManager.get_ollama_config()

    assert result is cached
    mock_first.assert_not_called()
    # But refresh_from_db is still called once.
    cached.refresh_from_db.assert_called_once_with()


def test_get_ollama_config_propagates_db_exceptions():
    """A DB error must not be swallowed by the cache layer."""
    ollama_cfg = MagicMock(name="Ollama")
    ollama_cfg.refresh_from_db.side_effect = RuntimeError("db down")
    with patch("app.models.Ollama.objects.first", return_value=ollama_cfg):
        with pytest.raises(RuntimeError):
            OllamaConfigManager.get_ollama_config()


def test_get_ollama_config_re_queries_when_first_returns_none():
    """If first() returns None, the helper should NOT cache the absence
    on the first hit; subsequent calls re-query the DB so a newly
    persisted row is picked up.
    """
    cfg_later = MagicMock(name="OllamaLater")
    mock_first = MagicMock(side_effect=[None, cfg_later])
    with patch("app.models.Ollama.objects.first", mock_first):
        assert OllamaConfigManager.get_ollama_config() is None
        assert OllamaConfigManager.get_ollama_config() is cfg_later

    assert mock_first.call_count == 2
