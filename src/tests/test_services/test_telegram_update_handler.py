"""Tests for `src/services/telegram_update_handler.py` (Tier 3).

TelegramUpdateHandler.handle_update is a synchronous orchestrator that:
  1. Parses the raw Telegram payload via `utils.telegram.parse_telegram_update`.
  2. Persists the user message.
  3. Updates `bot.telegram_chat_id` if not yet set.
  4. Sends a typing indicator.
  5. Queues the embedding-generation task.

The local import `from app.tasks import generate_embedding` is patched
at the `app.tasks` module level (per the test plan), and the
TelegramClient constructor is patched similarly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from app.choices import MessageRole
from services.telegram_update_handler import TelegramUpdateHandler


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _bot(telegram_chat_id: str | None = None, id: str = "bot-1"):
    bot = SimpleNamespace(
        id=id,
        telegram_chat_id=telegram_chat_id,
        save=MagicMock(),
    )
    return bot


def _update(text: str = "hello", chat_id: int = 999):
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
            "from": {"username": "alice", "first_name": "Alice"},
            "date": 1700000000,
        },
    }


# ──────────────────────────────────────────────
# Early returns / no-ops
# ──────────────────────────────────────────────


def test_handle_update_returns_when_payload_unparseable(caplog):
    import logging

    bot = _bot()
    with caplog.at_level(logging.WARNING):
        # Empty message → parse_telegram_update returns None.
        TelegramUpdateHandler.handle_update(bot, {"update_id": 1})
    bot.save.assert_not_called()


# ──────────────────────────────────────────────
# chat_id persistence
# ──────────────────────────────────────────────


def test_handle_update_sets_chat_id_when_unset():
    bot = _bot(telegram_chat_id=None)
    fake_msg = SimpleNamespace(id="m-1", save=MagicMock())

    with (
        patch("services.telegram_update_handler.parse_telegram_update") as parse,
        patch("services.telegram_update_handler.TelegramClientManager") as mgr,
        patch(
            "services.telegram_update_handler.Message.objects.create",
            return_value=fake_msg,
        ),
        patch("app.tasks.generate_embedding") as emb_task,
    ):
        parse.return_value = {
            "chat_id": "999",
            "text": "hi",
            "username": "u",
            "first_name": "U",
            "date": 0,
        }
        TelegramUpdateHandler.handle_update(bot, _update(chat_id=999))

    # Chat id was stringified and persisted.
    assert bot.telegram_chat_id == "999"
    bot.save.assert_called_once()
    save_kwargs = bot.save.call_args.kwargs
    assert "telegram_chat_id" in save_kwargs["update_fields"]


def test_handle_update_does_not_overwrite_existing_chat_id():
    bot = _bot(telegram_chat_id="existing-chat")
    fake_msg = SimpleNamespace(id="m-1", save=MagicMock())

    with (
        patch("services.telegram_update_handler.parse_telegram_update") as parse,
        patch("services.telegram_update_handler.TelegramClientManager") as mgr,
        patch(
            "services.telegram_update_handler.Message.objects.create",
            return_value=fake_msg,
        ),
        patch("app.tasks.generate_embedding") as emb_task,
    ):
        parse.return_value = {
            "chat_id": "999",
            "text": "hi",
            "username": "u",
            "first_name": "U",
            "date": 0,
        }
        TelegramUpdateHandler.handle_update(bot, _update(chat_id=999))

    # chat_id unchanged — we never overwrote an existing value.
    assert bot.telegram_chat_id == "existing-chat"
    bot.save.assert_not_called()


# ──────────────────────────────────────────────
# Message persistence
# ──────────────────────────────────────────────


def test_handle_update_persists_user_message_with_correct_role():
    bot = _bot(telegram_chat_id="999")
    fake_msg = SimpleNamespace(id="m-1", save=MagicMock())

    with (
        patch("services.telegram_update_handler.parse_telegram_update") as parse,
        patch("services.telegram_update_handler.TelegramClientManager") as mgr,
        patch(
            "services.telegram_update_handler.Message.objects.create",
            return_value=fake_msg,
        ) as create,
        patch("app.tasks.generate_embedding") as emb_task,
    ):
        parse.return_value = {
            "chat_id": "999",
            "text": "hi",
            "username": "u",
            "first_name": "U",
            "date": 0,
        }
        TelegramUpdateHandler.handle_update(bot, _update(text="hi"))

    create.assert_called_once()
    kwargs = create.call_args.kwargs
    assert kwargs["bot"] is bot
    assert kwargs["role"] == MessageRole.USER.value[0]
    assert kwargs["content"] == "hi"


# ──────────────────────────────────────────────
# Typing indicator + task dispatch
# ──────────────────────────────────────────────


def test_handle_update_sends_typing_indicator():
    bot = _bot(telegram_chat_id="999")
    fake_msg = SimpleNamespace(id="m-1", save=MagicMock())
    client = MagicMock()

    with (
        patch("services.telegram_update_handler.parse_telegram_update") as parse,
        patch(
            "services.telegram_update_handler.TelegramClientManager.create_client",
            return_value=client,
        ) as create_client,
        patch(
            "services.telegram_update_handler.Message.objects.create",
            return_value=fake_msg,
        ),
        patch("app.tasks.generate_embedding") as emb_task,
    ):
        parse.return_value = {
            "chat_id": "999",
            "text": "hi",
            "username": "u",
            "first_name": "U",
            "date": 0,
        }
        TelegramUpdateHandler.handle_update(bot, _update())

    create_client.assert_called_once_with(bot)
    client.send_typing_action.assert_called_once()


def test_handle_update_queues_embedding_task_with_message_id():
    bot = _bot(telegram_chat_id="999")
    fake_msg = SimpleNamespace(id="m-uuid", save=MagicMock())

    with (
        patch("services.telegram_update_handler.parse_telegram_update") as parse,
        patch("services.telegram_update_handler.TelegramClientManager"),
        patch(
            "services.telegram_update_handler.Message.objects.create",
            return_value=fake_msg,
        ),
        patch("app.tasks.generate_embedding") as emb_task,
    ):
        parse.return_value = {
            "chat_id": "999",
            "text": "hi",
            "username": "u",
            "first_name": "U",
            "date": 0,
        }
        TelegramUpdateHandler.handle_update(bot, _update())

    emb_task.apply_async.assert_called_once()
    kwargs = emb_task.apply_async.call_args.kwargs
    assert kwargs["queue"] == "default"
    assert kwargs["kwargs"] == {"message_id": "m-uuid"}


# ──────────────────────────────────────────────
# Error handling
# ──────────────────────────────────────────────


def test_handle_update_swallows_and_logs_exceptions(caplog):
    import logging

    bot = _bot(telegram_chat_id="999")

    # Force parse_telegram_update to raise to trigger the error path.
    with (
        patch(
            "services.telegram_update_handler.parse_telegram_update",
            side_effect=RuntimeError("boom"),
        ),
        caplog.at_level(logging.ERROR),
    ):
        # Must NOT raise — errors are logged and swallowed.
        TelegramUpdateHandler.handle_update(bot, _update())

    assert any("Failed to handle inbound update" in r.message for r in caplog.records)
