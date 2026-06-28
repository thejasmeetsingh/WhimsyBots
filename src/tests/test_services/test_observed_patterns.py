"""Tests for 'src/services/observed_patterns.py'.

The service is a thin wrapper around an LLM call. We patch
'OllamaClient.chat' and the 'Message.objects' queryset.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.choices import MessageRole
from services.observed_patterns import (
    MAX_PATTERN_CHARS,
    PATTERN_ANALYSIS_MESSAGE_LIMIT,
    PATTERN_REGEN_EVERY_N_MESSAGES,
    ObservedPatternsService,
)

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────


def test_constants_match_plan():
    # Hard cap must always fit in the smallest supported context window.
    assert MAX_PATTERN_CHARS == 4096
    assert PATTERN_ANALYSIS_MESSAGE_LIMIT == 100
    assert PATTERN_REGEN_EVERY_N_MESSAGES == 20


# ──────────────────────────────────────────────
# should_regenerate
# ──────────────────────────────────────────────


def _bot():
    return SimpleNamespace(id="bot-1", ollama_model="llama3", observed_patterns="")


def _ollama():
    return SimpleNamespace(endpoint="http://x", api_key=None)


def test_should_regenerate_true_when_count_is_multiple_of_n():
    with patch("services.observed_patterns.Message.objects") as m:
        m.filter.return_value.count.return_value = 20
        svc = ObservedPatternsService(bot=_bot(), ollama=_ollama())
        assert svc.should_regenerate() is True


def test_should_regenerate_true_for_higher_multiples():
    with patch("services.observed_patterns.Message.objects") as m:
        m.filter.return_value.count.return_value = 80
        svc = ObservedPatternsService(bot=_bot(), ollama=_ollama())
        assert svc.should_regenerate() is True


def test_should_regenerate_false_when_count_not_a_multiple():
    with patch("services.observed_patterns.Message.objects") as m:
        m.filter.return_value.count.return_value = 21
        svc = ObservedPatternsService(bot=_bot(), ollama=_ollama())
        assert svc.should_regenerate() is False


def test_should_regenerate_false_when_zero_messages():
    with patch("services.observed_patterns.Message.objects") as m:
        m.filter.return_value.count.return_value = 0
        svc = ObservedPatternsService(bot=_bot(), ollama=_ollama())
        assert svc.should_regenerate() is False


def test_should_regenerate_filters_by_bot_and_user_role():
    """The count should be scoped to the bot and USER role only."""

    captured = {}

    class _QS:
        def count(self_inner):
            return 10

    def fake_filter(**kwargs):
        captured["filter_kwargs"] = kwargs
        return _QS()

    with patch("services.observed_patterns.Message.objects") as m:
        m.filter.side_effect = fake_filter
        svc = ObservedPatternsService(bot=_bot(), ollama=_ollama())
        svc.should_regenerate()

    assert captured["filter_kwargs"]["bot_id"] == "bot-1"
    assert captured["filter_kwargs"]["role"] == MessageRole.USER.value[0]


def test_should_regenerate_respects_custom_n():
    with patch("services.observed_patterns.Message.objects") as m:
        m.filter.return_value.count.return_value = 5
        svc = ObservedPatternsService(bot=_bot(), ollama=_ollama())
        assert svc.should_regenerate(n=5) is True
        assert svc.should_regenerate(n=3) is False


# ──────────────────────────────────────────────
# _format_history
# ──────────────────────────────────────────────


def _msg(role, content, ts="2024-01-01 09:00"):
    return SimpleNamespace(
        role=role, content=content, created_at=SimpleNamespace(strftime=lambda fmt: ts)
    )


def test_format_history_includes_user_and_assistant():
    messages = [
        _msg(MessageRole.USER.value[0], "hi"),
        _msg(MessageRole.ASSISTANT.value[0], "hello"),
    ]
    text = ObservedPatternsService(bot=_bot(), ollama=_ollama())._format_history(messages)
    assert "User: hi" in text
    assert "Assistant: hello" in text


def test_format_history_skips_system_role_messages():
    messages = [
        _msg(MessageRole.USER.value[0], "hi"),
        _msg(MessageRole.SYSTEM.value[0], "internal summary"),
        _msg(MessageRole.ASSISTANT.value[0], "hello"),
    ]
    text = ObservedPatternsService(bot=_bot(), ollama=_ollama())._format_history(messages)
    assert "internal summary" not in text


def test_format_history_includes_timestamp():
    messages = [_msg(MessageRole.USER.value[0], "ping", ts="2024-06-15 10:30")]
    text = ObservedPatternsService(bot=_bot(), ollama=_ollama())._format_history(messages)
    assert "[2024-06-15 10:30]" in text


def test_format_history_uses_unknown_label_for_unknown_role():
    messages = [_msg("Z", "mystery")]
    text = ObservedPatternsService(bot=_bot(), ollama=_ollama())._format_history(messages)
    assert "Unknown: mystery" in text


def test_format_history_returns_empty_for_empty_list():
    text = ObservedPatternsService(bot=_bot(), ollama=_ollama())._format_history([])
    assert text == ""


# ──────────────────────────────────────────────
# _call_llm
# ──────────────────────────────────────────────


def test_call_llm_returns_message_from_response():
    svc = ObservedPatternsService(bot=_bot(), ollama=_ollama())
    with patch("services.observed_patterns.OllamaClient") as Client:
        Client.return_value.chat.return_value = {"message": "patterns go here"}
        result = svc._call_llm("prompt")
    assert result == "patterns go here"


def test_call_llm_returns_none_on_exception():
    svc = ObservedPatternsService(bot=_bot(), ollama=_ollama())
    with patch("services.observed_patterns.OllamaClient") as Client:
        Client.return_value.chat.side_effect = RuntimeError("llm down")
        assert svc._call_llm("prompt") is None


def test_call_llm_passes_model_from_bot():
    bot = SimpleNamespace(id="b", ollama_model="mistral", observed_patterns="")
    svc = ObservedPatternsService(bot=bot, ollama=_ollama())
    with patch("services.observed_patterns.OllamaClient") as Client:
        Client.return_value.chat.return_value = {"message": "x"}
        svc._call_llm("p")
    kwargs = Client.return_value.chat.call_args.kwargs
    assert kwargs["model"] == "mistral"
    # Pattern gen uses plain text — only 'model' and 'messages' are passed.
    assert "messages" in kwargs
    assert "tools" not in kwargs
    assert "format" not in kwargs


# ──────────────────────────────────────────────
# regenerate
# ──────────────────────────────────────────────


def test_regenerate_skips_when_no_messages(caplog):
    import logging

    bot = SimpleNamespace(id="b", ollama_model="m", observed_patterns="", save=MagicMock())
    svc = ObservedPatternsService(bot=bot, ollama=_ollama())

    # Empty queryset ⇒ skip and log.
    with patch("services.observed_patterns.Message.objects") as m:
        m.filter.return_value = []  # iterable, no entries
        with caplog.at_level(logging.INFO):
            svc.regenerate()

    bot.save.assert_not_called()
    assert any("skipping" in r.message.lower() for r in caplog.records)


def test_regenerate_skips_when_llm_returns_empty(caplog):
    import logging

    bot = SimpleNamespace(id="b", ollama_model="m", observed_patterns="", save=MagicMock())
    svc = ObservedPatternsService(bot=bot, ollama=_ollama())
    msg = _msg(MessageRole.USER.value[0], "hi")

    with (
        patch("services.observed_patterns.Message.objects") as m,
        patch.object(svc, "_call_llm", return_value=""),
    ):
        m.filter.return_value = [msg]
        with caplog.at_level(logging.WARNING):
            svc.regenerate()

    bot.save.assert_not_called()


def test_regenerate_truncates_output_to_max_chars():
    bot = SimpleNamespace(id="b", ollama_model="m", observed_patterns="", save=MagicMock())
    svc = ObservedPatternsService(bot=bot, ollama=_ollama())
    msg = _msg(MessageRole.USER.value[0], "hi")

    too_long = "x" * (MAX_PATTERN_CHARS + 500)
    with (
        patch("services.observed_patterns.Message.objects") as m,
        patch.object(svc, "_call_llm", return_value=too_long),
    ):
        m.filter.return_value = [msg]
        svc.regenerate()

    assert len(bot.observed_patterns) == MAX_PATTERN_CHARS
    bot.save.assert_called_once()


def test_regenerate_saves_patterns_on_success():
    bot = SimpleNamespace(id="b", ollama_model="m", observed_patterns="", save=MagicMock())
    svc = ObservedPatternsService(bot=bot, ollama=_ollama())
    msg = _msg(MessageRole.USER.value[0], "hi")

    with (
        patch("services.observed_patterns.Message.objects") as m,
        patch.object(svc, "_call_llm", return_value="user prefers concise replies"),
    ):
        m.filter.return_value = [msg]
        svc.regenerate()

    assert bot.observed_patterns == "user prefers concise replies"
    bot.save.assert_called_once()
    # Only the patterns + updated_at fields are persisted.
    update_fields = bot.save.call_args.kwargs["update_fields"]
    assert "observed_patterns" in update_fields


def test_regenerate_reverses_messages_for_chronological_order():
    """Messages arrive newest-first from the ORM; we reverse before formatting."""

    captured = {}
    bot = SimpleNamespace(id="b", ollama_model="m", observed_patterns="", save=MagicMock())
    svc = ObservedPatternsService(bot=bot, ollama=_ollama())
    msg1 = _msg(MessageRole.USER.value[0], "oldest")
    msg2 = _msg(MessageRole.USER.value[0], "newest")

    with (
        patch("services.observed_patterns.Message.objects") as m,
        patch.object(svc, "_call_llm", return_value="ok"),
        patch.object(
            svc,
            "_format_history",
            side_effect=lambda msgs: (captured.setdefault("order", list(msgs)), "")[1],
        ),
    ):
        m.filter.return_value = [msg1, msg2]  # newest-first
        svc.regenerate()

    # After reversal, the newest comes last.
    assert captured["order"] == [msg2, msg1]
