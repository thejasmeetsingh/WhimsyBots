"""Tests for 'src/services/conversation_summary.py'.

ConversationSummaryService reads 'bot.conversations' and
'bot.system_messages' directly (set via Prefetch in 'app.tasks'), so
we synthesise those attributes in the test fixtures.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.choices import MessageRole
from services.conversation_summary import ConversationSummaryService
from strings import SUMMARY_UNAVAILABLE

# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _msg(role: str, content: str = "x", id: str | None = None):
    return SimpleNamespace(
        id=id or f"id-{content[:5]}",
        role=role,
        content=content,
    )


def _bot(*, conversations=None, system_messages=None, name="botA"):
    return SimpleNamespace(
        id="bot-1",
        name=name,
        ollama_model="llama3",
        conversations=conversations or [],
        system_messages=system_messages or [],
        mcp_servers_list=[],
    )


def _ollama():
    return SimpleNamespace(num_ctx=4096, num_predict=None)


def _ollama_client():
    return MagicMock(name="OllamaClient")


# ──────────────────────────────────────────────
# process — short-circuits
# ──────────────────────────────────────────────


def test_process_returns_none_when_no_conversations():
    svc = ConversationSummaryService(_bot(conversations=[]), _ollama(), _ollama_client())
    assert svc.process() is None


def test_process_does_not_call_llm_when_no_conversations():
    client = _ollama_client()
    svc = ConversationSummaryService(_bot(conversations=[]), _ollama(), client)
    svc.process()
    client.chat.assert_not_called()


def test_process_returns_none_when_no_overflow():
    # A small conversation set fits inside the budget ⇒ nothing to summarise.
    bot = _bot(conversations=[_msg("U", "hi"), _msg("A", "hello")])
    client = _ollama_client()

    svc = ConversationSummaryService(bot, _ollama(), client)
    # Force the inner _set_summary_budget to give a huge budget so nothing overflows.
    svc.history_token_budget = 1_000_000
    svc.summary_chars = 1_000_000

    assert svc.process() is None


# ──────────────────────────────────────────────
# process — summary creation path
# ──────────────────────────────────────────────


def test_process_creates_new_summary_message_when_none_exists():
    from services.token_budget import TokenBudget

    bot = _bot(
        conversations=[
            _msg("U", "u" * 100),
            _msg("A", "a" * 100),
            _msg("U", "u" * 100),
            _msg("A", "a" * 100),
        ],
        system_messages=[],
    )
    client = _ollama_client()
    client.chat.return_value = {"message": "summary text"}

    tiny_budget = TokenBudget(
        num_ctx=4096,
        output_reservation=512,
        tool_def_tokens=0,
        fixed_overhead_tokens=200,
        usable_tokens=0,
        recommended_tool_count=0,
        history_tokens=1,
        embedding_chars=0,
        tool_response_chars=0,
        system_prompt_chars=0,
        patterns_chars=0,
        summary_chars=1000,
        allocation_breakdown={},
    )

    # A fake Message class — the real one needs DB access to instantiate.
    class FakeMessage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    svc = ConversationSummaryService(bot, _ollama(), client)

    with (
        patch(
            "services.conversation_summary.MCPServer.get_default_mcp_servers",
            return_value={},
        ),
        patch(
            "services.conversation_summary.MCPToolsBuilder.build_tools_from_servers",
            return_value=[],
        ),
        patch(
            "services.conversation_summary.TokenBudgetService.compute",
            return_value=tiny_budget,
        ),
        patch("services.conversation_summary.Message", FakeMessage),
    ):
        summary_msg, created = svc.process()

    assert created is True
    assert summary_msg.role == MessageRole.SYSTEM.value[0]
    assert summary_msg.content == "summary text"
    assert summary_msg.bot is bot


def test_process_updates_existing_summary_when_present():
    from services.token_budget import TokenBudget

    existing_summary = _msg("S", "old summary")
    bot = _bot(
        conversations=[
            _msg("U", "u" * 100),
            _msg("A", "a" * 100),
            _msg("U", "u" * 100),
        ],
        system_messages=[existing_summary],
    )
    client = _ollama_client()
    client.chat.return_value = {"message": "new summary"}

    tiny_budget = TokenBudget(
        num_ctx=4096,
        output_reservation=512,
        tool_def_tokens=0,
        fixed_overhead_tokens=200,
        usable_tokens=0,
        recommended_tool_count=0,
        history_tokens=1,
        embedding_chars=0,
        tool_response_chars=0,
        system_prompt_chars=0,
        patterns_chars=0,
        summary_chars=1000,
        allocation_breakdown={},
    )

    svc = ConversationSummaryService(bot, _ollama(), client)

    with (
        patch(
            "services.conversation_summary.MCPServer.get_default_mcp_servers",
            return_value={},
        ),
        patch(
            "services.conversation_summary.MCPToolsBuilder.build_tools_from_servers",
            return_value=[],
        ),
        patch(
            "services.conversation_summary.TokenBudgetService.compute",
            return_value=tiny_budget,
        ),
    ):
        summary_msg, created = svc.process()

    assert created is False
    # In-place update: same object, new content.
    assert summary_msg is existing_summary
    assert summary_msg.content == "new summary"


# ──────────────────────────────────────────────
# _summarize — formatting & fallback
# ──────────────────────────────────────────────


def test_summarize_returns_none_when_llm_fails():
    client = _ollama_client()
    client.chat.side_effect = RuntimeError("ollama down")
    svc = ConversationSummaryService(_bot(), _ollama(), client)
    assert svc._summarize(messages=[_msg("U", "hi")], summary_msg=None) is None


def test_summarize_passes_existing_summary_in_prompt():
    client = _ollama_client()
    client.chat.return_value = {"message": "ok"}

    svc = ConversationSummaryService(_bot(), _ollama(), client)
    svc.summary_chars = 500
    existing = _msg("S", "previous chunk")

    svc._summarize(messages=[_msg("U", "hi")], summary_msg=existing)

    # The existing summary's content should appear in the prompt.
    prompt_arg = client.chat.call_args.kwargs["messages"][0]["content"]
    assert "previous chunk" in prompt_arg


def test_summarize_uses_unavailable_marker_when_no_existing_summary():
    client = _ollama_client()
    client.chat.return_value = {"message": "ok"}

    svc = ConversationSummaryService(_bot(), _ollama(), client)
    svc.summary_chars = 500
    svc._summarize(messages=[_msg("U", "hi")], summary_msg=None)

    prompt_arg = client.chat.call_args.kwargs["messages"][0]["content"]
    assert SUMMARY_UNAVAILABLE in prompt_arg


def test_summarize_labels_messages_by_role():
    client = _ollama_client()
    client.chat.return_value = {"message": "ok"}

    svc = ConversationSummaryService(_bot(), _ollama(), client)
    svc.summary_chars = 500
    svc._summarize(
        messages=[_msg("U", "user msg"), _msg("A", "bot msg")],
        summary_msg=None,
    )

    prompt_arg = client.chat.call_args.kwargs["messages"][0]["content"]
    assert "User: user msg" in prompt_arg
    assert "Assistant: bot msg" in prompt_arg


def test_summarize_uses_low_temperature_for_deterministic_output():
    client = _ollama_client()
    client.chat.return_value = {"message": "ok"}

    svc = ConversationSummaryService(_bot(), _ollama(), client)
    svc.summary_chars = 500
    svc._summarize(messages=[_msg("U", "x")], summary_msg=None)

    options = client.chat.call_args.kwargs["options"]
    assert options["temperature"] == 0.3


# ──────────────────────────────────────────────
# _split_by_budget
# ──────────────────────────────────────────────


def test_split_by_budget_separates_old_and_new():
    messages = [_msg("U", f"m{i}") for i in range(5)]
    svc = ConversationSummaryService(_bot(), _ollama(), _ollama_client())
    svc.history_token_budget = 1_000_000  # fits everything
    in_window, overflowed = svc._split_by_budget(messages)
    assert len(in_window) == 5
    assert overflowed == []


def test_split_by_budget_overflows_oldest_when_tight():
    # Use long-enough messages that each consumes >1 token, so a
    # zero-token budget overflows everything.
    messages = [_msg("U", "x" * 20 + f"-{i}") for i in range(5)]
    svc = ConversationSummaryService(_bot(), _ollama(), _ollama_client())
    svc.history_token_budget = 0
    in_window, overflowed = svc._split_by_budget(messages)
    assert in_window == []
    assert len(overflowed) == 5
