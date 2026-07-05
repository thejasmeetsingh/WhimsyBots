"""Token Budget Service — treats the context window like OS RAM.

Priority (most protected → truncated first):
  1. Patterns       — protected, capped at 4096 chars by design
  2. System prompt  — truncate from bottom if tight (includes skills block inline)
  3. History        — drop oldest messages first
  4. Embeddings     — drop lowest-similarity results first

All values are in CHARS. We never have an exact tokenizer for arbitrary
Ollama models, so we use a conservative 3.5 chars/token estimate.
This intentionally under-budgets slightly, giving a safe headroom.
"""

import json
import logging
from typing import Any

from pydantic import BaseModel

from app.models import Message, Ollama

logger = logging.getLogger(__name__)


CHARS_PER_TOKEN: float = 3.5

# Tool definitions are JSON with lots of nested keys, brackets, and quotes
# that tokenize more densely than prose. Using a tighter ratio here avoids
# underestimating tool costs which causes Ollama schema validation errors.
TOOL_CHARS_PER_TOKEN: float = 2.5

# Safety buffer subtracted on top of the measured tool definition cost.
# Accounts for tokenizer variance across different Ollama models.
TOOL_SAFETY_BUFFER_TOKENS: int = 50

# Fixed overhead: StructuredOutput JSON schema + base system instructions
# that are always present regardless of context. Measured conservatively.
FIXED_OVERHEAD_TOKENS: int = 200

# How many tokens to reserve for the LLM's own output when num_predict is
# not explicitly set on the Ollama config.
DEFAULT_OUTPUT_RESERVATION_TOKENS: int = 512

# Average token cost of a single MCP tool definition (schema + description).
# Used to derive the maximum recommended tool count from the available budget.
# We measure actual cost where possible, but this is the floor estimate used
# for capacity planning against num_ctx.
TOOL_DEF_AVG_TOKENS: int = 150

# Allocation ratios applied to the remaining usable budget after all
# fixed reservations are carved out. Must sum to 1.0.
#
# Priority tiers:
#   Tier 1 — history + embeddings: the model's primary context
#   Tier 2 — system_prompt + patterns + summary: important but bounded by design
#
# Tool responses are not budgeted here: there is no downstream consumer
# that reads `tool_response_chars`, so reserving 25% of the budget was
# dead weight. If/when tool-response truncation is wired in, reintroduce
# a tier and rebalance.
#
# Skills are inlined into the system prompt, so the 5% previously carved
# out for SKILLS_RESERVED_TOKENS is folded back into system_prompt.
ALLOCATION_RATIOS: dict[str, float] = {
    "history": 0.40,  # 40% — recency context, Tier 1
    "embeddings": 0.30,  # 30% — semantic memory, Tier 1
    "system_prompt": 0.15,  # 15% — user-authored prompt + inline skills, Tier 2
    "patterns": 0.10,  # 10% — behavioral profile, Tier 2
    "summary": 0.05,  # 5%  — past conversation summary, Tier 2
}

assert abs(sum(ALLOCATION_RATIOS.values()) - 1.0) < 1e-9, "Ratios must sum to 1.0"


class TokenBudget(BaseModel):
    """Pydantic model carrying the computed token budget for one cycle."""

    # Raw inputs
    num_ctx: int
    output_reservation: int
    tool_def_tokens: int
    fixed_overhead_tokens: int

    # Derived
    usable_tokens: int
    recommended_tool_count: int  # max tools we can fit given num_ctx + allocations

    # Allocations — ordered by priority tier
    # Tier 1: primary context
    history_tokens: int  # kept as tokens for message-count estimation
    embedding_chars: int
    # Tier 2: bounded by design, rarely need their full slice
    system_prompt_chars: int
    patterns_chars: int
    summary_chars: int

    # Diagnostics
    allocation_breakdown: dict[str, int]

    def log_summary(self) -> None:
        """Emit a structured info log summarizing the computed budget."""
        logger.info(
            {
                "msg": "TokenBudget computed",
                "num_ctx": self.num_ctx,
                "usable_tokens": self.usable_tokens,
                "system_prompt_chars": self.system_prompt_chars,
                "patterns_chars": self.patterns_chars,
                "summary_chars": self.summary_chars,
                "embedding_chars": self.embedding_chars,
                "history_tokens": self.history_tokens,
                "recommended_tool_count": self.recommended_tool_count,
            }
        )


class TokenBudgetService:
    """Computes a TokenBudget for a single message processing cycle.

    Usage:
        budget = TokenBudgetService.compute(ollama_config, tool_definitions)
        # then pass budget into context assembly
    """

    @classmethod
    def compute(
        cls,
        ollama_config: "Ollama",
        tool_definitions: list[dict[str, Any]],
    ) -> TokenBudget:
        """Compute the per-cycle token budget.

        Steps:
            1. Start with num_ctx.
            2. Subtract output reservation (num_predict or default).
            3. Subtract estimated tool definition tokens (MCP tools are verbose JSON).
            4. Subtract fixed overhead (StructuredOutput schema + base instructions).
            5. Compute recommended_tool_count from num_ctx + existing allocations.
            6. Split remainder across system_prompt / patterns / embeddings
               / history / tool_responses.
        """
        num_ctx = max(ollama_config.num_ctx or 4096, 4096)

        # --- Step 2: Output reservation ---
        output_reservation = (
            ollama_config.num_predict
            if ollama_config.num_predict
            else DEFAULT_OUTPUT_RESERVATION_TOKENS
        )

        # --- Step 3: Tool definition cost (measured, not estimated) ---

        # Uses actual serialized size + safety buffer so tool definitions
        # are never underestimated and never enter the truncation pool.
        tool_def_tokens = cls._measure_tool_def_tokens(tool_definitions)

        # --- Step 4: Fixed carve-outs ---
        total_reserved = output_reservation + tool_def_tokens + FIXED_OVERHEAD_TOKENS

        usable_tokens = max(num_ctx - total_reserved, 0)

        if usable_tokens == 0:
            logger.warning(
                "TokenBudget: usable_tokens=0 after reservations "
                "(num_ctx=%d, output=%d, tool_defs=%d, overhead=%d). "
                "Increase num_ctx in the admin panel or reduce connected MCP tools.",
                num_ctx,
                output_reservation,
                tool_def_tokens,
                FIXED_OVERHEAD_TOKENS,
            )

        # --- Step 5: Recommended tool count ---
        # The headroom left after every other reservation tells us how many
        # more tool definitions we *could* fit. Floor at the number of tools
        # we already measured (we can never recommend fewer than are in use).
        recommended_tool_count = cls._recommend_tool_count(
            num_ctx=num_ctx,
            output_reservation=output_reservation,
            already_measured_tool_def_tokens=tool_def_tokens,
            tool_definitions=tool_definitions,
        )

        # --- Step 6: Proportional allocation ---
        allocations = {
            source: int(usable_tokens * ratio) for source, ratio in ALLOCATION_RATIOS.items()
        }

        budget = TokenBudget(
            num_ctx=num_ctx,
            output_reservation=output_reservation,
            tool_def_tokens=tool_def_tokens,
            fixed_overhead_tokens=FIXED_OVERHEAD_TOKENS,
            usable_tokens=usable_tokens,
            recommended_tool_count=recommended_tool_count,
            history_tokens=allocations["history"],
            embedding_chars=cls._tokens_to_chars(allocations["embeddings"]),
            system_prompt_chars=cls._tokens_to_chars(allocations["system_prompt"]),
            patterns_chars=cls._tokens_to_chars(allocations["patterns"]),
            summary_chars=cls._tokens_to_chars(allocations["summary"]),
            allocation_breakdown=allocations,
        )

        budget.log_summary()
        return budget

    @staticmethod
    def truncate_text(text: str, char_limit: int, suffix: str = "…") -> str:
        """Hard-truncate text to char_limit, appending suffix if truncated.

        Used for system_prompt and patterns.
        """
        if not text or len(text) <= char_limit:
            return text

        limit = char_limit - len(suffix)
        return text[:limit] + suffix

    @staticmethod
    def fit_messages_to_token_budget(
        messages: list[Message],  # list of Message ORM objects, newest-first
        token_budget: int,
    ) -> list[Message]:
        """Keep newest messages until the token budget is exhausted.

        Walk backwards through messages (newest → oldest), accumulating
        token cost until the budget is exhausted. Returns the kept subset
        in chronological order (oldest → newest) for correct LLM context.

        This is the "drop oldest first" strategy for history.
        """
        kept: list[Message] = []
        tokens_used = 0

        for msg in messages:  # already sorted newest-first by caller
            cost = TokenBudgetService._estimate_text_tokens(msg.content)
            if tokens_used + cost > token_budget:
                break
            kept.append(msg)
            tokens_used += cost

        kept.reverse()  # restore chronological order
        return kept

    @staticmethod
    def fit_embeddings_to_char_budget(
        memories: list[
            tuple[Message, float]
        ],  # list of (Message, similarity_score), highest score first
        char_budget: int,
    ) -> list[tuple[Message, float]]:
        """Include embeddings from highest similarity downward until exhausted.

        Lowest-similarity results are dropped first naturally since they
        appear at the end of the ranked list.
        """
        kept: list[tuple[Message, float]] = []
        chars_used = 0

        for msg, score in memories:
            cost = len(msg.content)
            if chars_used + cost > char_budget:
                # Partial inclusion not useful for a message — skip remainder
                break
            kept.append((msg, score))
            chars_used += cost

        return kept

    @classmethod
    def _measure_tool_def_tokens(cls, tool_definitions: list[dict[str, Any]]) -> int:
        """Measure the actual serialized token cost of the tool definitions.

        Measures the actual serialized token cost of tool definitions
        rather than estimating. Uses a tighter chars/token ratio for JSON
        plus a safety buffer to account for tokenizer variance across models.

        This ensures tool definitions are fully reserved and never enter
        the truncation pool — partial tool schemas cause Ollama validation errors.
        """
        if not tool_definitions:
            return 0

        raw = json.dumps(tool_definitions, separators=(",", ":"))  # compact JSON
        measured = int(len(raw) / TOOL_CHARS_PER_TOKEN)
        return measured + TOOL_SAFETY_BUFFER_TOKENS

    @classmethod
    def _recommend_tool_count(
        cls,
        num_ctx: int,
        output_reservation: int,
        already_measured_tool_def_tokens: int,
        tool_definitions: list[dict[str, Any]],
    ) -> int:
        """Derive the maximum number of MCP tool definitions we can safely host.

        Math:
            headroom = num_ctx - output_reservation - fixed_overhead
            capacity = headroom // TOOL_DEF_AVG_TOKENS  (floored)
            return max(capacity, len(tool_definitions))

        The floor at the current count ensures we never recommend fewer
        tools than are already wired into this request — dropping below
        the current count would break the running request.

        If the *actual* measured cost of the current tool set already
        exceeds what 'headroom' can support, log a warning so the operator
        sees the context is over-budget (the returned count is still
        floored at the current size to keep the request intact).
        """
        headroom = num_ctx - output_reservation - FIXED_OVERHEAD_TOKENS
        currently_used = len(tool_definitions)

        if headroom <= 0 or TOOL_DEF_AVG_TOKENS <= 0:
            # If we can't even afford one tool, fall back to the current count
            # (caller can decide whether to drop tools downstream).
            return currently_used

        capacity = headroom // TOOL_DEF_AVG_TOKENS

        # 'already_measured_tool_def_tokens' includes TOOL_SAFETY_BUFFER_TOKENS,
        # which is *not* subtracted from headroom in the standard carve-out.
        # Compare against headroom + buffer so the warning only fires when
        # the underlying measured cost truly exceeds what the window can hold.
        if (
            already_measured_tool_def_tokens > (headroom + TOOL_SAFETY_BUFFER_TOKENS)
            and currently_used > 0
        ):
            logger.warning(
                "TokenBudget: tool definitions exceed headroom "
                "(measured=%d tokens, headroom=%d tokens, tools=%d). "
                "Increase num_ctx or reduce connected MCP tools.",
                already_measured_tool_def_tokens,
                headroom,
                currently_used,
            )

        # Floor at the current count so we never recommend fewer tools than
        # are already wired into this request.
        return max(int(capacity), currently_used)

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        return int(len(text) / CHARS_PER_TOKEN)

    @staticmethod
    def _tokens_to_chars(tokens: int) -> int:
        return int(tokens * CHARS_PER_TOKEN)
