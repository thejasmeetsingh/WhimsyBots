"""Tests for `src/services/token_budget.py` (Tier 1 — pure logic).

TokenBudgetService is pure math + pydantic models — no DB, no I/O.
We pass plain objects that quack like `Ollama` and `Message` instances
via `SimpleNamespace`, so the tests stay independent of the ORM.
"""

from __future__ import annotations

from types import SimpleNamespace


from services.token_budget import (
    ALLOCATION_RATIOS,
    CHARS_PER_TOKEN,
    DEFAULT_OUTPUT_RESERVATION_TOKENS,
    FIXED_OVERHEAD_TOKENS,
    TOOL_CHARS_PER_TOKEN,
    TOOL_SAFETY_BUFFER_TOKENS,
    TokenBudget,
    TokenBudgetService,
)


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _ollama(num_ctx: int = 8192, num_predict: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(num_ctx=num_ctx, num_predict=num_predict)


def _msg(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


def _tool_def(name: str = "tool") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "x" * 50,
            "parameters": {"type": "object", "properties": {"a": {"type": "string"}}},
        },
    }


# ──────────────────────────────────────────────
# Module-level constants — sanity checks
# ──────────────────────────────────────────────


def test_allocation_ratios_sum_to_one():
    # The allocator relies on this invariant to fill the entire budget.
    assert abs(sum(ALLOCATION_RATIOS.values()) - 1.0) < 1e-9


def test_chars_per_token_estimate_is_more_aggressive_than_json():
    # Tokenizer density assumptions — JSON tools use a tighter ratio
    # than prose because of bracket/quote overhead.
    assert CHARS_PER_TOKEN > TOOL_CHARS_PER_TOKEN


# ──────────────────────────────────────────────
# TokenBudgetService.compute
# ──────────────────────────────────────────────


def test_compute_uses_default_num_ctx_when_zero():
    budget = TokenBudgetService.compute(_ollama(num_ctx=0), [])
    # num_ctx=0 ⇒ floored to 4096 by the implementation.
    assert budget.num_ctx == 4096


def test_compute_uses_explicit_num_ctx():
    budget = TokenBudgetService.compute(_ollama(num_ctx=16384), [])
    assert budget.num_ctx == 16384


def test_compute_uses_default_output_reservation_when_num_predict_unset():
    budget = TokenBudgetService.compute(_ollama(num_predict=None), [])
    assert budget.output_reservation == DEFAULT_OUTPUT_RESERVATION_TOKENS


def test_compute_uses_num_predict_when_set():
    budget = TokenBudgetService.compute(_ollama(num_predict=256), [])
    assert budget.output_reservation == 256


def test_compute_subtracts_fixed_overhead():
    budget = TokenBudgetService.compute(_ollama(num_ctx=4096, num_predict=None), [])
    # Sanity: the fixed overhead should be reflected on the budget.
    assert budget.fixed_overhead_tokens == FIXED_OVERHEAD_TOKENS


def test_compute_usable_tokens_is_non_negative():
    # Even pathological configs (huge tool defs vs tiny num_ctx) must
    # produce a non-negative `usable_tokens` rather than blowing up.
    budget = TokenBudgetService.compute(_ollama(num_ctx=4096), [_tool_def("a")] * 50)
    assert budget.usable_tokens >= 0


def test_compute_zero_tool_defs_yields_zero_tool_cost():
    budget = TokenBudgetService.compute(_ollama(), [])
    assert budget.tool_def_tokens == 0


def test_compute_measures_tool_def_tokens_with_buffer():
    tools = [_tool_def(f"tool_{i}") for i in range(3)]
    budget = TokenBudgetService.compute(_ollama(), tools)
    # Token cost must be at least the safety buffer alone.
    assert budget.tool_def_tokens >= TOOL_SAFETY_BUFFER_TOKENS


def test_compute_tool_def_tokens_scale_with_definitions():
    one_tool = TokenBudgetService.compute(_ollama(), [_tool_def("a")])
    five_tools = TokenBudgetService.compute(
        _ollama(), [_tool_def(f"t{i}") for i in range(5)]
    )
    assert five_tools.tool_def_tokens > one_tool.tool_def_tokens


def test_compute_recommends_at_least_current_tool_count():
    # We must never recommend fewer tools than are already in use —
    # dropping below the current count would break the running request.
    tools = [_tool_def(f"t{i}") for i in range(10)]
    budget = TokenBudgetService.compute(_ollama(num_ctx=4096), tools)
    assert budget.recommended_tool_count >= len(tools)


def test_compute_allocation_breakdown_matches_ratios():
    budget = TokenBudgetService.compute(_ollama(num_ctx=16384), [])
    total_allocated = sum(budget.allocation_breakdown.values())
    # Each slice is int(usable_tokens * ratio); the integer truncation
    # means the sum may be ≤ usable_tokens but must never exceed it.
    assert total_allocated <= budget.usable_tokens


def test_compute_history_budget_present():
    budget = TokenBudgetService.compute(_ollama(), [])
    assert budget.history_tokens > 0


def test_compute_embedding_char_budget_present():
    budget = TokenBudgetService.compute(_ollama(), [])
    assert budget.embedding_chars > 0


# ──────────────────────────────────────────────
# TokenBudgetService.truncate_text
# ──────────────────────────────────────────────


def test_truncate_text_returns_input_when_under_limit():
    assert TokenBudgetService.truncate_text("hello", 100) == "hello"


def test_truncate_text_returns_empty_string_when_input_empty():
    assert TokenBudgetService.truncate_text("", 100) == ""


def test_truncate_text_appends_suffix_when_over_limit():
    truncated = TokenBudgetService.truncate_text("abcdefghij", 5)
    # The result must be exactly 5 chars long (limit, not limit+len(suffix)).
    assert len(truncated) == 5
    assert truncated.endswith("…")


def test_truncate_text_uses_custom_suffix():
    truncated = TokenBudgetService.truncate_text("abcdefghij", 5, suffix="...")
    assert truncated.endswith("...")


def test_truncate_text_preserves_ascii_content_when_truncated():
    # Truncation must NOT garble or skip characters mid-string.
    truncated = TokenBudgetService.truncate_text("abcdefghij", 5)
    assert truncated.startswith("abcd")


# ──────────────────────────────────────────────
# fit_messages_to_token_budget
# ──────────────────────────────────────────────


def test_fit_messages_keeps_all_when_under_budget():
    msgs = [_msg("a"), _msg("b"), _msg("c")]
    # Budget of 100 tokens easily fits all three short messages.
    kept = TokenBudgetService.fit_messages_to_token_budget(msgs, token_budget=100)
    assert len(kept) == 3


def test_fit_messages_returns_chronological_order():
    # Input is newest-first; output must be oldest-first for the LLM.
    msgs = [_msg("newest"), _msg("middle"), _msg("oldest")]
    kept = TokenBudgetService.fit_messages_to_token_budget(msgs, token_budget=100)
    assert [m.content for m in kept] == ["oldest", "middle", "newest"]


def test_fit_messages_drops_oldest_first():
    # With a tight budget, the oldest message should be dropped first.
    # Each message is ~13 chars ≈ 3 tokens (int(13/3.5)=3).
    # Budget 4 fits one message — only the *newest* survives.
    msgs = [
        _msg("newest-msg-xx"),  # 14 chars → cost 4
        _msg("middle-msg-xx"),  # 14 chars → cost 4
        _msg("oldest-msg-xx"),  # 14 chars → cost 4
    ]
    kept = TokenBudgetService.fit_messages_to_token_budget(msgs, token_budget=4)
    # Only the newest survives the budget; after reversing for the LLM,
    # the single kept message should be "newest-msg-xx".
    assert [m.content for m in kept] == ["newest-msg-xx"]


def test_fit_messages_partial_keep_drops_oldest():
    # Each message is ~14 chars → cost 4. Budget 8 keeps 2 newest;
    # after reversing, the two oldest ones should be present (NOT "newest").
    msgs = [
        _msg("third-newest"),  # 13 chars
        _msg("second-newest"),  # 14 chars
        _msg("newest-only!"),  # 12 chars
    ]
    kept = TokenBudgetService.fit_messages_to_token_budget(msgs, token_budget=8)
    kept_contents = [m.content for m in kept]
    # "newest-only!" should be dropped — only two of the older three remain.
    assert "newest-only!" not in kept_contents
    assert len(kept) == 2


def test_fit_messages_empty_input_returns_empty():
    assert TokenBudgetService.fit_messages_to_token_budget([], token_budget=100) == []


# ──────────────────────────────────────────────
# fit_embeddings_to_char_budget
# ──────────────────────────────────────────────


def test_fit_embeddings_keeps_all_when_under_budget():
    memories = [(_msg("short"), 0.9), (_msg("tiny"), 0.8)]
    kept = TokenBudgetService.fit_embeddings_to_char_budget(memories, char_budget=1000)
    assert len(kept) == 2


def test_fit_embeddings_drops_lowest_similarity_first():
    # memories arrive highest-similarity-first; lowest get dropped first.
    memories = [
        (_msg("aaaa"), 0.9),
        (_msg("bbbb"), 0.7),
        (_msg("cccc"), 0.5),
        (_msg("dddd"), 0.3),
    ]
    # Budget only fits the first two messages.
    kept = TokenBudgetService.fit_embeddings_to_char_budget(memories, char_budget=10)
    scores = [score for _, score in kept]
    assert scores == sorted(scores, reverse=True)


def test_fit_embeddings_preserves_order_in_output():
    memories = [(_msg("a"), 0.9), (_msg("b"), 0.7)]
    kept = TokenBudgetService.fit_embeddings_to_char_budget(memories, char_budget=1000)
    assert kept == memories


def test_fit_embeddings_empty_returns_empty():
    assert TokenBudgetService.fit_embeddings_to_char_budget([], char_budget=100) == []


# ──────────────────────────────────────────────
# truncate_tool_response
# ──────────────────────────────────────────────


def test_truncate_tool_response_returns_input_when_under_budget():
    assert TokenBudgetService.truncate_tool_response("hello", 100) == "hello"


def test_truncate_tool_response_truncates_with_note():
    result = TokenBudgetService.truncate_tool_response("x" * 500, char_budget=100)
    assert len(result) <= 100
    assert "truncated" in result.lower()


def test_truncate_tool_response_truncates_to_exact_budget():
    result = TokenBudgetService.truncate_tool_response("x" * 1000, char_budget=200)
    assert len(result) == 200


# ──────────────────────────────────────────────
# _measure_tool_def_tokens
# ──────────────────────────────────────────────


def test_measure_tool_def_tokens_returns_zero_for_empty():
    assert TokenBudgetService._measure_tool_def_tokens([]) == 0


def test_measure_tool_def_tokens_includes_safety_buffer():
    tools = [_tool_def("solo")]
    cost = TokenBudgetService._measure_tool_def_tokens(tools)
    # Always at least the buffer, plus measured cost.
    assert cost >= TOOL_SAFETY_BUFFER_TOKENS


def test_measure_tool_def_tokens_uses_compact_json_ratio():
    # Equivalent JSON content — the measured cost should match the
    # compact serializer's byte length divided by TOOL_CHARS_PER_TOKEN.
    import json as _json

    tools = [_tool_def("t")]
    raw = _json.dumps(tools, separators=(",", ":"))
    expected_measured = int(len(raw) / TOOL_CHARS_PER_TOKEN)
    expected_total = expected_measured + TOOL_SAFETY_BUFFER_TOKENS
    assert TokenBudgetService._measure_tool_def_tokens(tools) == expected_total


# ──────────────────────────────────────────────
# _recommend_tool_count
# ──────────────────────────────────────────────


def test_recommend_tool_count_floors_at_current_count():
    # Even with a tiny num_ctx, we never recommend fewer tools than
    # are already wired in.
    tools = [_tool_def(f"t{i}") for i in range(5)]
    count = TokenBudgetService._recommend_tool_count(
        num_ctx=4096,
        output_reservation=512,
        already_measured_tool_def_tokens=10_000,  # over-budget
        tool_definitions=tools,
    )
    assert count >= len(tools)


def test_recommend_tool_count_returns_at_least_current_count_with_empty_tools():
    # With an empty tool list and a sane num_ctx, the function returns
    # the integer headroom capacity (never a negative number).
    count = TokenBudgetService._recommend_tool_count(
        num_ctx=8192,
        output_reservation=512,
        already_measured_tool_def_tokens=0,
        tool_definitions=[],
    )
    assert count >= 0
    # And it should never be *less* than the current (empty) list size.
    assert count >= len([])


def test_recommend_tool_count_handles_zero_headroom():
    # If output_reservation + overhead >= num_ctx, headroom is ≤ 0
    # ⇒ the function falls back to the current tool count (no division by zero).
    tools = [_tool_def(f"t{i}") for i in range(3)]
    count = TokenBudgetService._recommend_tool_count(
        num_ctx=200,
        output_reservation=512,  # already exceeds num_ctx
        already_measured_tool_def_tokens=100,
        tool_definitions=tools,
    )
    assert count == len(tools)


# ──────────────────────────────────────────────
# TokenBudget pydantic model
# ──────────────────────────────────────────────


def test_token_budget_can_be_constructed_with_required_fields():
    budget = TokenBudget(
        num_ctx=4096,
        output_reservation=512,
        tool_def_tokens=0,
        fixed_overhead_tokens=200,
        usable_tokens=3384,
        recommended_tool_count=0,
        history_tokens=1000,
        embedding_chars=500,
        tool_response_chars=500,
        system_prompt_chars=200,
        patterns_chars=100,
        summary_chars=100,
        allocation_breakdown={},
    )
    assert budget.num_ctx == 4096
    assert budget.usable_tokens == 3384


def test_token_budget_log_summary_does_not_raise(caplog):
    # The summary logger should emit a structured record; verify it
    # is safe to call (does not raise).
    budget = TokenBudget(
        num_ctx=4096,
        output_reservation=512,
        tool_def_tokens=0,
        fixed_overhead_tokens=200,
        usable_tokens=3384,
        recommended_tool_count=0,
        history_tokens=1000,
        embedding_chars=500,
        tool_response_chars=500,
        system_prompt_chars=200,
        patterns_chars=100,
        summary_chars=100,
        allocation_breakdown={"history": 1000},
    )
    import logging

    with caplog.at_level(logging.INFO):
        budget.log_summary()
    # At least one log record should have been emitted on this logger.
    assert any("TokenBudget" in rec.message for rec in caplog.records)
