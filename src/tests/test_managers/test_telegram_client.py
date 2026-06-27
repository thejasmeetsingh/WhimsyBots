"""Tests for `src/managers/telegram_client.py` (Tier 2 — service logic with mocks).

`TelegramClientManager.create_client` is a thin wrapper that pulls
the token + chat_id off the Bot ORM object and forwards them to the
`clients.TelegramClient` constructor. We patch the constructor so no
real HTTP / Redis client is instantiated.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from managers.telegram_client import TelegramClientManager


# ──────────────────────────────────────────────
# create_client
# ──────────────────────────────────────────────


def test_create_client_constructs_with_token_and_chat_id():
    bot = MagicMock()
    bot.telegram_bot_token = "the-bot-token"
    bot.telegram_chat_id = "123456789"

    with patch("clients.TelegramClient") as client_cls:
        client_cls.return_value = MagicMock(name="TelegramClient")
        result = TelegramClientManager.create_client(bot)

    # Constructor wired with token first, chat_id second.
    client_cls.assert_called_once_with("the-bot-token", "123456789")
    assert result is client_cls.return_value


def test_create_client_preserves_token_order():
    # The constructor signature is (token, chat_id) — verify we don't
    # accidentally swap them. This is a regression guard.
    bot = MagicMock()
    bot.telegram_bot_token = "TOK"
    bot.telegram_chat_id = "CHAT"

    with patch("clients.TelegramClient") as client_cls:
        TelegramClientManager.create_client(bot)

    call_args = client_cls.call_args.args
    assert call_args == ("TOK", "CHAT")


def test_create_client_propagates_chat_id_none():
    # A bot may not have a chat_id configured yet — verify it is
    # passed through as None rather than coerced or rejected.
    bot = MagicMock()
    bot.telegram_bot_token = "TOK"
    bot.telegram_chat_id = None

    with patch("clients.TelegramClient") as client_cls:
        TelegramClientManager.create_client(bot)

    client_cls.assert_called_once_with("TOK", None)


def test_create_client_returns_instance_from_constructor():
    # The returned object must be exactly what `clients.TelegramClient(...)`
    # returned — no wrappers, no copies.
    bot = MagicMock()
    bot.telegram_bot_token = "TOK"
    bot.telegram_chat_id = "CHAT"

    sentinel = object()
    with patch("clients.TelegramClient", return_value=sentinel):
        assert TelegramClientManager.create_client(bot) is sentinel


def test_create_client_uses_lazy_import():
    # The manager does `from clients import TelegramClient` inside the
    # function body (to avoid a circular import). Verify by patching the
    # `clients` module's TelegramClient attribute and checking that the
    # patch was honored at call time.
    bot = MagicMock()
    bot.telegram_bot_token = "TOK"
    bot.telegram_chat_id = "CHAT"

    with patch("clients.TelegramClient") as client_cls:
        result = TelegramClientManager.create_client(bot)

    # Constructor was called with token + chat_id.
    client_cls.assert_called_once_with("TOK", "CHAT")
    # The wrapper returns whatever the constructor returned.
    assert result is client_cls.return_value
