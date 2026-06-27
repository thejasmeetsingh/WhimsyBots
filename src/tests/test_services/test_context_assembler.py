"""Tests for `src/services/context_assembler.py` (Tier 3).

ContextAssembler queries the ORM, computes a token budget, and joins
four context sources (system prompt, observed patterns, relevant
memories, conversation history) into a single payload for the LLM.

The Message ORM and EmbeddingService are the only collaborators —
we mock both so we can drive the assembly pipeline deterministically.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


from services.token_budget import TokenBudget
from services.context_assembler import (
    AssembledContext,
    ContextAssembler,
    RECENT_MESSAGES_CAP,
    SECTION_SEP,
)
from strings import SUMMARY_UNAVAILABLE


# ──────────────────────────────────────────────
# helpers — fake message ORM objects
# ──────────────────────────────────────────────


def _msg(role: str, content: str = "x", embedding=None, created_at=None):
    if created_at is None:
        created_at = SimpleNamespace(strftime=lambda fmt: "2024-01-01")
    return SimpleNamespace(
        id=f"m-{content}",
        role=role,
        content=content,
        content_embedding=embedding,
        created_at=created_at,
    )


def _bot(
    *,
    system_prompt: str = "You are a helpful assistant.",
    observed_patterns: str = "",
    embedding_dimensions: int = 768,
    id: str = "bot-1",
):
    return SimpleNamespace(
        id=id,
        system_prompt=system_prompt,
        observed_patterns=observed_patterns,
        embedding_model="nomic-embed-text",
        embedding_dimensions=embedding_dimensions,
    )


def _ollama(num_ctx=8192, num_predict=None, temperature=0.7):
    return SimpleNamespace(
        num_ctx=num_ctx, num_predict=num_predict, temperature=temperature
    )


# ──────────────────────────────────────────────
# MockQuerySet helper
# ──────────────────────────────────────────────


def _fake_budget(
    *,
    system_prompt_chars=10_000,
    patterns_chars=1_000,
    summary_chars=1_000,
    embedding_chars=2_000,
    history_tokens=1_000,
    recommended_tool_count=0,
):
    """Build a real TokenBudget instance for tests (pydantic validates the type)."""
    return TokenBudget(
        num_ctx=4096,
        output_reservation=512,
        tool_def_tokens=0,
        fixed_overhead_tokens=200,
        usable_tokens=history_tokens,
        recommended_tool_count=recommended_tool_count,
        history_tokens=history_tokens,
        embedding_chars=embedding_chars,
        tool_response_chars=embedding_chars,
        system_prompt_chars=system_prompt_chars,
        patterns_chars=patterns_chars,
        summary_chars=summary_chars,
        allocation_breakdown={},
    )


class MockQuerySet:
    """Minimal stand-in for a Django queryset supporting the operations
    ContextAssembler uses: filter(), annotate(), order_by(), exclude(),
    slicing via [:N]."""

    def __init__(self, items=None):
        self._items = list(items or [])
        self._ordered = False

    def filter(self, **kwargs):
        # Naive role-only filter is all we need.
        if "role__in" in kwargs:
            roles = set(kwargs["role__in"])
            items = [m for m in self._items if m.role in roles]
        elif "role" in kwargs:
            role = kwargs["role"]
            items = [m for m in self._items if m.role == role]
        else:
            items = list(self._items)
        return MockQuerySet(items)

    def first(self):
        return self._items[0] if self._items else None

    def annotate(self, **_):
        return self

    def order_by(self, *_):
        return self

    def exclude(self, **_):
        return self

    def __getitem__(self, slicer):
        return MockQuerySet(self._items[slicer])

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


# ──────────────────────────────────────────────
# Module-level constants
# ──────────────────────────────────────────────


def test_section_sep_is_the_documented_format():
    # 3 newlines, 3 dashes, 2 newlines — pinned so admin tweaks
    # don't silently break section boundaries.
    assert SECTION_SEP == "\n\n---\n\n"


def test_recent_messages_cap_is_two_hundred():
    # Hard cap that protects the conversation query from runaway scans.
    assert RECENT_MESSAGES_CAP == 200


# ──────────────────────────────────────────────
# assemble — happy path / section composition
# ──────────────────────────────────────────────


def test_assemble_returns_assembled_context_dataclass():
    bot = _bot()
    current_msg = _msg("U", "hi")
    qs = MockQuerySet([])

    with (
        patch("services.context_assembler.Message.objects", qs),
        patch(
            "services.context_assembler.TokenBudgetService.truncate_text",
            side_effect=lambda text, limit, suffix="…": (
                text[: limit - len(suffix)] + suffix if len(text) > limit else text
            ),
        ) as budget_svc,
        patch("services.context_assembler.TokenBudgetService.compute") as compute_mock,
        patch("services.context_assembler.EmbeddingService"),
    ):
        compute_mock.return_value = _fake_budget(
            system_prompt_chars=10_000,
            patterns_chars=1_000,
            summary_chars=1_000,
            embedding_chars=2_000,
            history_tokens=1_000,
            recommended_tool_count=0,
        )
        result = ContextAssembler(bot, _ollama(), current_msg).assemble(
            tool_definitions=[]
        )

    assert isinstance(result, AssembledContext)
    assert result.budget is compute_mock.return_value


def test_assemble_includes_system_prompt_when_present():
    bot = _bot(system_prompt="Be terse.")
    current_msg = _msg("U")
    qs = MockQuerySet([])

    with (
        patch("services.context_assembler.Message.objects", qs),
        patch(
            "services.context_assembler.TokenBudgetService.truncate_text",
            side_effect=lambda text, limit, suffix="…": (
                text[: limit - len(suffix)] + suffix if len(text) > limit else text
            ),
        ) as budget_svc,
        patch("services.context_assembler.TokenBudgetService.compute") as compute_mock,
        patch("services.context_assembler.EmbeddingService"),
    ):
        compute_mock.return_value = _fake_budget(
            system_prompt_chars=10_000,
            patterns_chars=1_000,
            summary_chars=1_000,
            embedding_chars=2_000,
            history_tokens=1_000,
            recommended_tool_count=0,
        )
        result = ContextAssembler(bot, _ollama(), current_msg).assemble(
            tool_definitions=[]
        )

    # system_prompt tokens make it into the history as the first item.
    assert "Be terse." in result.history[0]["content"]


def test_assemble_skips_patterns_section_when_bot_has_none():
    bot = _bot(observed_patterns="")
    current_msg = _msg("U")
    qs = MockQuerySet([])

    with (
        patch("services.context_assembler.Message.objects", qs),
        patch(
            "services.context_assembler.TokenBudgetService.truncate_text",
            side_effect=lambda text, limit, suffix="…": (
                text[: limit - len(suffix)] + suffix if len(text) > limit else text
            ),
        ) as budget_svc,
        patch("services.context_assembler.TokenBudgetService.compute") as compute_mock,
        patch("services.context_assembler.EmbeddingService"),
    ):
        compute_mock.return_value = _fake_budget(
            system_prompt_chars=10_000,
            patterns_chars=1_000,
            summary_chars=1_000,
            embedding_chars=2_000,
            history_tokens=1_000,
            recommended_tool_count=0,
        )
        result = ContextAssembler(bot, _ollama(), current_msg).assemble(
            tool_definitions=[]
        )

    full_text = SECTION_SEP.join(s["content"] for s in result.history)
    assert "Observed User Patterns" not in full_text


def test_assemble_includes_patterns_section_when_present():
    bot = _bot(observed_patterns="user prefers bullet points")
    current_msg = _msg("U")
    qs = MockQuerySet([])

    with (
        patch("services.context_assembler.Message.objects", qs),
        patch(
            "services.context_assembler.TokenBudgetService.truncate_text",
            side_effect=lambda text, limit, suffix="…": (
                text[: limit - len(suffix)] + suffix if len(text) > limit else text
            ),
        ) as budget_svc,
        patch("services.context_assembler.TokenBudgetService.compute") as compute_mock,
        patch("services.context_assembler.EmbeddingService"),
    ):
        compute_mock.return_value = _fake_budget(
            system_prompt_chars=10_000,
            patterns_chars=1_000,
            summary_chars=1_000,
            embedding_chars=2_000,
            history_tokens=1_000,
            recommended_tool_count=0,
        )
        result = ContextAssembler(bot, _ollama(), current_msg).assemble(
            tool_definitions=[]
        )

    full_text = SECTION_SEP.join(s["content"] for s in result.history)
    assert "user prefers bullet points" in full_text


def test_assemble_uses_summary_unavailable_when_no_system_message():
    bot = _bot()
    current_msg = _msg("U")
    qs = MockQuerySet([])  # no system message

    with (
        patch("services.context_assembler.Message.objects", qs),
        patch(
            "services.context_assembler.TokenBudgetService.truncate_text",
            side_effect=lambda text, limit, suffix="…": (
                text[: limit - len(suffix)] + suffix if len(text) > limit else text
            ),
        ) as budget_svc,
        patch("services.context_assembler.TokenBudgetService.compute") as compute_mock,
        patch("services.context_assembler.EmbeddingService"),
    ):
        compute_mock.return_value = _fake_budget(
            system_prompt_chars=10_000,
            patterns_chars=1_000,
            summary_chars=1_000,
            embedding_chars=2_000,
            history_tokens=1_000,
            recommended_tool_count=0,
        )
        result = ContextAssembler(bot, _ollama(), current_msg).assemble(
            tool_definitions=[]
        )

    assert SUMMARY_UNAVAILABLE in result.history[0]["content"]


def test_assemble_truncates_system_prompt_when_over_budget(caplog):
    import logging

    bot = _bot(system_prompt="x" * 1000)
    current_msg = _msg("U")
    qs = MockQuerySet([])

    with (
        patch("services.context_assembler.Message.objects", qs),
        patch(
            "services.context_assembler.TokenBudgetService.truncate_text",
            side_effect=lambda text, limit, suffix="…": (
                text[: limit - len(suffix)] + suffix if len(text) > limit else text
            ),
        ) as budget_svc,
        patch("services.context_assembler.TokenBudgetService.compute") as compute_mock,
        patch("services.context_assembler.EmbeddingService"),
        caplog.at_level(logging.INFO),
    ):
        # Force truncation by giving a tiny budget.
        compute_mock.return_value = _fake_budget(
            system_prompt_chars=100,
            patterns_chars=100,
            summary_chars=100,
            embedding_chars=100,
            history_tokens=100,
            recommended_tool_count=0,
        )
        # truncate_text is also mocked to behave like the real impl.
        budget_svc.truncate_text.side_effect = lambda text, limit, suffix="…": (
            text[: limit - len(suffix)] + suffix if len(text) > limit else text
        )
        result = ContextAssembler(bot, _ollama(), current_msg).assemble(
            tool_definitions=[]
        )

    assert any("truncated" in r.message.lower() for r in caplog.records)
    # The assembled prompt carries the suffix ⇒ truncation occurred.
    assert "…" in result.history[0]["content"]


# ──────────────────────────────────────────────
# assemble — memories path (skipped when no embedding)
# ──────────────────────────────────────────────


def test_assemble_skips_memories_when_current_message_has_no_embedding():
    bot = _bot()
    current_msg = _msg("U", embedding=None)  # no embedding yet
    qs = MockQuerySet([])

    with (
        patch("services.context_assembler.Message.objects", qs),
        patch(
            "services.context_assembler.TokenBudgetService.truncate_text",
            side_effect=lambda text, limit, suffix="…": (
                text[: limit - len(suffix)] + suffix if len(text) > limit else text
            ),
        ) as budget_svc,
        patch("services.context_assembler.TokenBudgetService.compute") as compute_mock,
        patch("services.context_assembler.EmbeddingService") as emb_cls,
    ):
        compute_mock.return_value = _fake_budget(
            system_prompt_chars=10_000,
            patterns_chars=1_000,
            summary_chars=1_000,
            embedding_chars=2_000,
            history_tokens=1_000,
            recommended_tool_count=0,
        )
        ContextAssembler(bot, _ollama(), current_msg).assemble(tool_definitions=[])

    # EmbeddingService was NOT instantiated when there's no embedding.
    emb_cls.assert_not_called()


def test_assemble_includes_memories_when_embedding_present():
    bot = _bot()
    current_msg = _msg("U", embedding=[0.1] * 768)
    qs = MockQuerySet([])

    with (
        patch("services.context_assembler.Message.objects", qs),
        patch(
            "services.context_assembler.TokenBudgetService.truncate_text",
            side_effect=lambda text, limit, suffix="…": (
                text[: limit - len(suffix)] + suffix if len(text) > limit else text
            ),
        ) as budget_svc,
        patch("services.context_assembler.TokenBudgetService.compute") as compute_mock,
        patch("services.context_assembler.EmbeddingService") as emb_cls,
    ):
        compute_mock.return_value = _fake_budget(
            system_prompt_chars=10_000,
            patterns_chars=1_000,
            summary_chars=1_000,
            embedding_chars=2_000,
            history_tokens=1_000,
            recommended_tool_count=0,
        )
        emb_cls.return_value.get_relevant_memories.return_value = [
            (_msg("U", "past fact"), 0.9)
        ]
        result = ContextAssembler(bot, _ollama(), current_msg).assemble(
            tool_definitions=[]
        )

    # Relevant Past Context section is present.
    full_text = SECTION_SEP.join(s["content"] for s in result.history)
    assert "Relevant Past Context" in full_text
    assert "past fact" in full_text


# ──────────────────────────────────────────────
# _fit_patterns
# ──────────────────────────────────────────────


def test_fit_patterns_returns_empty_when_no_patterns():
    bot = _bot(observed_patterns="")
    assembler = ContextAssembler(bot, _ollama(), _msg("U"))
    budget = SimpleNamespace(patterns_chars=1_000)
    assert assembler._fit_patterns(budget) == ""


def test_fit_patterns_wraps_in_section_header():
    bot = _bot(observed_patterns="prefers short replies")
    assembler = ContextAssembler(bot, _ollama(), _msg("U"))
    budget = SimpleNamespace(patterns_chars=1_000)
    result = assembler._fit_patterns(budget)
    assert "## Observed User Patterns" in result
    assert "prefers short replies" in result


# ──────────────────────────────────────────────
# _fit_memories
# ──────────────────────────────────────────────


def test_fit_memories_returns_empty_when_budget_zero():
    bot = _bot()
    assembler = ContextAssembler(bot, _ollama(), _msg("U", embedding=[0.1] * 768))
    budget = SimpleNamespace(embedding_chars=0)
    assert assembler._fit_memories(budget) == ""


def test_fit_memories_returns_empty_when_no_memories():
    bot = _bot()
    assembler = ContextAssembler(bot, _ollama(), _msg("U", embedding=[0.1] * 768))
    budget = SimpleNamespace(embedding_chars=1_000)
    with patch("services.context_assembler.EmbeddingService") as emb_cls:
        emb_cls.return_value.get_relevant_memories.return_value = []
        assert assembler._fit_memories(budget) == ""


def test_fit_memories_includes_relevant_section():
    bot = _bot()
    msg = _msg("U", "a memory")
    assembler = ContextAssembler(bot, _ollama(), _msg("U", embedding=[0.1] * 768))
    budget = SimpleNamespace(embedding_chars=1_000)
    with patch("services.context_assembler.EmbeddingService") as emb_cls:
        emb_cls.return_value.get_relevant_memories.return_value = [(msg, 0.9)]
        result = assembler._fit_memories(budget)

    assert "Relevant Past Context" in result
    assert "a memory" in result


def test_fit_memories_excludes_current_message_id():
    bot = _bot()
    current = _msg("U", "current")
    current.id = "current-id"
    assembler = ContextAssembler(bot, _ollama(), current)
    budget = SimpleNamespace(embedding_chars=1_000)
    with patch("services.context_assembler.EmbeddingService") as emb_cls:
        emb_cls.return_value.get_relevant_memories.return_value = []
        assembler._fit_memories(budget)
        kwargs = emb_cls.return_value.get_relevant_memories.call_args.kwargs
        assert kwargs["current_message_id"] == "current-id"


# ──────────────────────────────────────────────
# _fit_history
# ──────────────────────────────────────────────


def test_fit_history_returns_chronological_ollama_format():
    bot = _bot()
    current_msg = _msg("U", "now")
    assembler = ContextAssembler(bot, _ollama(), current_msg)
    budget = SimpleNamespace(history_tokens=10_000)
    # Input is newest-first (matching how Message ORM returns rows via
    # `-created_at` ordering); after fitting, output is oldest-first.
    messages = [_msg("U", "third"), _msg("A", "second"), _msg("U", "first")]

    result = assembler._fit_history(budget, messages=messages, system_prompt="SYSTEM")

    # First entry is the system prompt; the rest are chronological.
    assert result[0] == {"role": "system", "content": "SYSTEM"}
    assert result[1]["content"] == "first"
    assert result[-1]["content"] == "third"


def test_fit_history_drops_assistant_role_unless_present():
    # System messages (role='S') should be silently filtered out —
    # only U/A make it into the LLM context.
    bot = _bot()
    assembler = ContextAssembler(bot, _ollama(), _msg("U"))
    budget = SimpleNamespace(history_tokens=10_000)
    messages = [
        _msg("U", "hi"),
        _msg("S", "internal summary"),
        _msg("A", "hello"),
    ]
    result = assembler._fit_history(budget, messages=messages)
    assert all(m["role"] in {"user", "assistant", "system"} for m in result)
    assert all("internal summary" not in m["content"] for m in result)
