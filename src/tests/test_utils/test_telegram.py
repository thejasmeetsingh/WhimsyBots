"""Tests for `src/utils/telegram.py` (Tier 1 — pure logic)."""

from __future__ import annotations

from typing import Any, Dict


from utils.telegram import parse_telegram_update


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _update(
    *,
    update_id: int = 1,
    chat_id: Any = 999,
    text: str = "hi",
    username: str = "alice",
    first_name: str = "Alice",
    date: Any = 1700000000,
) -> Dict[str, Any]:
    """Build a representative Telegram update payload."""
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
            "from": {"username": username, "first_name": first_name},
            "date": date,
        },
    }


# ──────────────────────────────────────────────
# parse_telegram_update
# ──────────────────────────────────────────────


def test_parse_telegram_update_returns_normalised_dict():
    result = parse_telegram_update(_update())
    assert result == {
        "update_id": 1,
        "chat_id": "999",
        "text": "hi",
        "username": "alice",
        "first_name": "Alice",
        "date": 1700000000,
    }


def test_parse_telegram_update_returns_none_when_message_missing():
    # Inline queries / callback queries / edited channel posts have
    # no "message" — caller should be able to short-circuit on None.
    payload = {"update_id": 2, "inline_query": {"q": "x"}}
    assert parse_telegram_update(payload) is None


def test_parse_telegram_update_returns_none_when_message_is_falsy():
    # Defensive: a None message should also short-circuit, not raise.
    assert parse_telegram_update({"update_id": 3, "message": None}) is None


def test_parse_telegram_update_strips_text():
    result = parse_telegram_update(_update(text="  hello world  \n"))
    assert result["text"] == "hello world"


def test_parse_telegram_update_stringifies_integer_chat_id():
    result = parse_telegram_update(_update(chat_id=123456789))
    assert result["chat_id"] == "123456789"
    assert isinstance(result["chat_id"], str)


def test_parse_telegram_update_preserves_string_chat_id():
    # Some webhook payloads carry chat_id as a string already — must
    # not be double-wrapped or stringified weirdly.
    result = parse_telegram_update(_update(chat_id="-1001234567890"))
    assert result["chat_id"] == "-1001234567890"


def test_parse_telegram_update_defaults_missing_username_and_first_name():
    # No `from` block ⇒ empty defaults rather than a KeyError.
    payload = {
        "update_id": 5,
        "message": {"chat": {"id": 1}, "text": "ping", "date": 0},
    }
    result = parse_telegram_update(payload)
    assert result["username"] == ""
    assert result["first_name"] == ""


def test_parse_telegram_update_defaults_missing_text():
    # Some message types (stickers, photos) have no text — default "".
    payload = {
        "update_id": 6,
        "message": {
            "chat": {"id": 1},
            "from": {"username": "bob", "first_name": "Bob"},
            "date": 0,
        },
    }
    result = parse_telegram_update(payload)
    assert result["text"] == ""
