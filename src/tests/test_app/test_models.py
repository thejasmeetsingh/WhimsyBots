"""Tests for 'src/app/models.py'.

We exercise 'Bot.save' and 'MCPServer.clean' without hitting the real
DB. 'super().save()' is patched per-test so we can verify both the
hash population logic and the 'clean()' validator chain.

Note: 'MCPServer.get_default_mcp_servers' was removed when the
'cron_job' and 'pdf_generator' standalone MCP server packages were
replaced with the in-process 'mcp_tools' registry. The default tool
catalog is now exposed by 'mcp_tools.tools.CRON_JOB_TOOLS',
'PDF_GENERATOR_TOOLS', and 'WEB_SEARCH_TOOLS' — see 'test_mcp_tools/'.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from app.choices import MCPTransportType
from app.models import Bot, MCPServer
from utils.crypto import get_token_hash

# ──────────────────────────────────────────────
# Bot.save
# ──────────────────────────────────────────────


def test_bot_save_populates_token_hash_when_token_provided():
    """`Bot.save` must compute and store the SHA-256 hash of the token when
    the token is truthy."""

    bot = Bot(telegram_bot_token="MY-SECRET-TOKEN")

    # Patch the parent's save so we don't touch the DB.
    with patch("django.db.models.Model.save") as parent_save:
        bot.save()

    expected = get_token_hash("MY-SECRET-TOKEN")
    assert bot.telegram_bot_token_hash == expected
    parent_save.assert_called_once()


def test_bot_save_does_not_change_hash_when_token_missing():
    """If 'telegram_bot_token' is falsy (None / empty), we MUST NOT overwrite
    'telegram_bot_token_hash' — that's how we detect duplicates on update."""

    existing_hash = "pre-existing-hash"
    bot = Bot(telegram_bot_token=None, telegram_bot_token_hash=existing_hash)

    with patch("django.db.models.Model.save") as parent_save:
        bot.save()

    assert bot.telegram_bot_token_hash == existing_hash
    parent_save.assert_called_once()


def test_bot_save_does_not_change_hash_when_token_empty_string():
    bot = Bot(telegram_bot_token="", telegram_bot_token_hash="preserved")

    with patch("django.db.models.Model.save"):
        bot.save()

    assert bot.telegram_bot_token_hash == "preserved"


def test_bot_save_overwrites_hash_when_token_changed():
    bot = Bot(
        telegram_bot_token="NEW-TOKEN",
        telegram_bot_token_hash="old-hash",
    )

    with patch("django.db.models.Model.save"):
        bot.save()

    # The hash must reflect the NEW token.
    assert bot.telegram_bot_token_hash == get_token_hash("NEW-TOKEN")
    assert bot.telegram_bot_token_hash != "old-hash"


def test_bot_save_hash_is_deterministic():
    """Same token should always produce the same hash (this is the property
    used for duplicate detection on update)."""

    bot1 = Bot(telegram_bot_token="ABC")
    bot2 = Bot(telegram_bot_token="ABC")

    with patch("django.db.models.Model.save"):
        bot1.save()
        bot2.save()

    assert bot1.telegram_bot_token_hash == bot2.telegram_bot_token_hash


def test_bot_save_passes_through_kwargs():
    """`*args, **kwargs` must reach `super().save(...)` unchanged."""

    bot = Bot(telegram_bot_token="T")

    with patch("django.db.models.Model.save") as parent_save:
        bot.save(update_fields=["name"], using="replica")

    parent_save.assert_called_once_with(update_fields=["name"], using="replica")


# ──────────────────────────────────────────────
# MCPServer.clean
# ──────────────────────────────────────────────


def test_mcp_server_clean_local_with_command_passes():
    server = MCPServer(
        transport=MCPTransportType.LOCAL.value[0],
        command="python",
        endpoint=None,
    )
    # Should not raise.
    server.clean()


def test_mcp_server_clean_local_without_command_raises():
    server = MCPServer(
        transport=MCPTransportType.LOCAL.value[0],
        command=None,
        endpoint=None,
    )
    with pytest.raises(ValidationError):
        server.clean()


def test_mcp_server_clean_remote_with_endpoint_passes():
    server = MCPServer(
        transport=MCPTransportType.REMOTE.value[0],
        command=None,
        endpoint="https://mcp.example.com/sse",
    )
    server.clean()


def test_mcp_server_clean_remote_without_endpoint_raises():
    server = MCPServer(
        transport=MCPTransportType.REMOTE.value[0],
        command=None,
        endpoint=None,
    )
    with pytest.raises(ValidationError):
        server.clean()


def test_mcp_server_clean_calls_super_clean():
    """`MCPServer.clean` must call `super().clean()` after transport validation."""

    server = MCPServer(
        transport=MCPTransportType.LOCAL.value[0],
        command="python",
        endpoint=None,
    )
    with patch("django.db.models.Model.clean") as parent_clean:
        server.clean()
    parent_clean.assert_called_once()


# ──────────────────────────────────────────────
# Sanity: default tool catalog moved to mcp_tools
# ──────────────────────────────────────────────


def test_default_mcp_servers_factory_was_removed():
    """The old 'MCPServer.get_default_mcp_servers' class method was
    retired when 'cron_job' / 'pdf_generator' were folded into the
    'mcp_tools' registry. The replacement lives in
    'mcp_tools.tools.{CRON_JOB_TOOLS,PDF_GENERATOR_TOOLS,WEB_SEARCH_TOOLS}'.
    """

    assert not hasattr(MCPServer, "get_default_mcp_servers")
