"""Tests for 'src/utils/formatting.py'."""

from __future__ import annotations

from types import SimpleNamespace

from utils.formatting import convert_messages_to_ollama_format, get_admin_link

# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _msg(role: str, content: str) -> SimpleNamespace:
    """Build a Message-like object without hitting the ORM."""
    return SimpleNamespace(role=role, content=content)


# ──────────────────────────────────────────────
# convert_messages_to_ollama_format
# ──────────────────────────────────────────────


def test_convert_messages_maps_user_and_assistant_roles():
    messages = [_msg("U", "hi"), _msg("A", "hello there")]

    result = convert_messages_to_ollama_format(messages)

    assert result == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ]


def test_convert_messages_skips_system_role_messages():
    # Only U/A are mapped — system / summary messages (S) are silently
    # dropped so the caller can pre-filter if needed.
    messages = [_msg("U", "hi"), _msg("S", "internal summary"), _msg("A", "hey")]

    result = convert_messages_to_ollama_format(messages)

    assert result == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hey"},
    ]


def test_convert_messages_prepends_system_prompt_when_provided():
    messages = [_msg("U", "ping")]
    result = convert_messages_to_ollama_format(messages, system_prompt="You are helpful.")
    assert result[0] == {"role": "system", "content": "You are helpful."}
    assert result[1:] == [{"role": "user", "content": "ping"}]


def test_convert_messages_without_system_prompt_has_no_system_message():
    result = convert_messages_to_ollama_format([_msg("U", "hi")])
    assert all(m["role"] != "system" for m in result)


def test_convert_messages_with_empty_iterable():
    # No messages and no system prompt ⇒ empty list.
    assert convert_messages_to_ollama_format([]) == []


def test_convert_messages_with_only_system_prompt():
    # Only the system prompt is returned, no empty user/assistant entries.
    result = convert_messages_to_ollama_format([], system_prompt="be brief")
    assert result == [{"role": "system", "content": "be brief"}]


def test_convert_messages_accepts_queryset_like_iterable():
    # A generator should be exhausted correctly (no length assumption).
    def gen():
        yield _msg("U", "1")
        yield _msg("A", "2")

    result = convert_messages_to_ollama_format(gen())
    assert len(result) == 2


# ──────────────────────────────────────────────
# get_admin_link
# ──────────────────────────────────────────────


def test_get_admin_link_renders_anchor_with_model_and_id():
    obj = SimpleNamespace(id="bot-uuid-123")
    link = get_admin_link("message", 42, obj)

    assert link == ("<a href='/admin/app/message/?bot_id=bot-uuid-123' target='_blank'>42</a>")


def test_get_admin_link_uses_stringified_id_in_href():
    # obj.id may be a UUID; ensure it's stringified before insertion.
    obj = SimpleNamespace(id=42)  # integer id
    link = get_admin_link("log", 7, obj)
    assert "bot_id=42" in link
    assert ">7</a>" in link


def test_get_admin_link_returns_zero_when_value_is_falsy():
    obj = SimpleNamespace(id="x")
    assert get_admin_link("message", 0, obj) == "0"


def test_get_admin_link_returns_zero_when_obj_is_none():
    # `not obj` should short-circuit to "0" even if value is truthy.
    assert get_admin_link("message", 5, None) == "0"


def test_get_admin_link_returns_zero_when_value_is_none():
    obj = SimpleNamespace(id="x")
    assert get_admin_link("message", None, obj) == "0"
