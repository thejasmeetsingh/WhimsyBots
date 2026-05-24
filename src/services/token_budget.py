"""
Token Budget Service — treats the context window like OS RAM.

Priority (most protected → truncated first):
  1. Skills         — static, always fits, never touched
  2. Patterns       — protected, capped at 4096 chars by design
  3. System prompt  — truncate from bottom if tight
  4. History        — drop oldest messages first
  5. Embeddings     — drop lowest-similarity results first
  6. Tool responses — truncated most aggressively (verbose, LLM-generated)

All values are in CHARS. We never have an exact tokenizer for arbitrary
Ollama models, so we use a conservative 3.5 chars/token estimate.
This intentionally under-budgets slightly, giving a safe headroom.
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field

from app.models import Ollama
from app.utils import split_message


logger = logging.getLogger(__name__)


CHARS_PER_TOKEN: float = 3.5

# Fixed overhead: StructuredOutput JSON schema + base system instructions
# that are always present regardless of context. Measured conservatively.
FIXED_OVERHEAD_TOKENS: int = 200

# Skills are static and author-controlled. We carve this out first so they
# never compete with dynamic sources. Keep skill descriptions under this.
SKILLS_RESERVED_TOKENS: int = 250  # ~875 chars

# How many tokens to reserve for the LLM's own output when num_predict is
# not explicitly set on the Ollama config.
DEFAULT_OUTPUT_RESERVATION_TOKENS: int = 512

# Allocation ratios applied to the remaining usable budget after all
# fixed reservations are carved out. Must sum to 1.0.
#
# Priority tiers:
#   Tier 1 (equal) — history + embeddings: the model's primary context
#   Tier 2         — tool_responses: the model's only window into live data
#   Tier 3         — system_prompt + patterns: important but bounded by design
ALLOCATION_RATIOS: dict[str, float] = {
    "history": 0.30,  # 30% — recency context, Tier 1
    "embeddings": 0.30,  # 30% — semantic memory, Tier 1 (equal to history)
    "tool_responses": 0.25,  # 25% — live data from MCP tools, Tier 2
    "system_prompt": 0.10,  # 10% — user-authored prompt, Tier 3
    "patterns": 0.05,  # 5%  — behavioral profile, Tier 3 (already capped at 4096 chars)
}

assert abs(sum(ALLOCATION_RATIOS.values()) - 1.0) < 1e-9, "Ratios must sum to 1.0"


@dataclass
class TokenBudget:
    # Raw inputs
    num_ctx: int
    output_reservation: int
    tool_def_tokens: int
    fixed_overhead_tokens: int
    skills_tokens: int

    # Derived
    usable_tokens: int

    # Allocations — ordered by priority tier
    # Tier 1: primary context (equal weight)
    history_tokens: int  # kept as tokens for message-count estimation
    embedding_chars: int
    # Tier 2: live data from tools
    tool_response_chars: int
    # Tier 3: bounded by design, rarely need their full slice
    system_prompt_chars: int
    patterns_chars: int

    # Diagnostics
    allocation_breakdown: dict[str, int] = field(default_factory=dict)

    def log_summary(self) -> None:
        logger.info(
            "TokenBudget computed",
            extra={
                "num_ctx": self.num_ctx,
                "usable_tokens": self.usable_tokens,
                "system_prompt_chars": self.system_prompt_chars,
                "patterns_chars": self.patterns_chars,
                "embedding_chars": self.embedding_chars,
                "history_tokens": self.history_tokens,
                "tool_response_chars": self.tool_response_chars,
            },
        )


class TokenBudgetService:
    """
    Computes a TokenBudget for a single message processing cycle.

    Usage:
        budget = TokenBudgetService.compute(ollama_config, tool_definitions)
        # then pass budget into context assembly
    """

    @classmethod
    def compute(
        cls,
        ollama_config: "Ollama",
        tool_definitions: list[dict],
    ) -> TokenBudget:
        """
        Steps:
          1. Start with num_ctx
          2. Subtract output reservation (num_predict or default)
          3. Subtract estimated tool definition tokens (MCP tools are verbose JSON)
          4. Subtract fixed overhead (StructuredOutput schema + base instructions)
          5. Subtract skills reservation (always protected, carved out first)
          6. Split remainder across system_prompt / patterns / embeddings / history / tool_responses
        """

        num_ctx = max(ollama_config.num_ctx or 4096, 4096)

        # --- Step 2: Output reservation ---
        output_reservation = (
            ollama_config.num_predict
            if ollama_config.num_predict
            else DEFAULT_OUTPUT_RESERVATION_TOKENS
        )

        # --- Step 3: Tool definition cost ---
        tool_def_tokens = cls._estimate_tool_def_tokens(tool_definitions)

        # --- Step 4 & 5: Fixed carve-outs ---
        total_reserved = (
            output_reservation
            + tool_def_tokens
            + FIXED_OVERHEAD_TOKENS
            + SKILLS_RESERVED_TOKENS
        )

        usable_tokens = max(num_ctx - total_reserved, 0)

        if usable_tokens == 0:
            logger.warning(
                "TokenBudget: num_ctx=%d is too small to fit fixed overhead. "
                "Consider increasing context window or reducing MCP tools.",
                num_ctx,
            )

        # --- Step 6: Proportional allocation ---
        allocations = {
            source: int(usable_tokens * ratio)
            for source, ratio in ALLOCATION_RATIOS.items()
        }

        budget = TokenBudget(
            num_ctx=num_ctx,
            output_reservation=output_reservation,
            tool_def_tokens=tool_def_tokens,
            fixed_overhead_tokens=FIXED_OVERHEAD_TOKENS,
            skills_tokens=SKILLS_RESERVED_TOKENS,
            usable_tokens=usable_tokens,
            history_tokens=allocations["history"],
            embedding_chars=cls._tokens_to_chars(allocations["embeddings"]),
            tool_response_chars=cls._tokens_to_chars(allocations["tool_responses"]),
            system_prompt_chars=cls._tokens_to_chars(allocations["system_prompt"]),
            patterns_chars=cls._tokens_to_chars(allocations["patterns"]),
            allocation_breakdown=allocations,
        )

        budget.log_summary()
        return budget

    @staticmethod
    def truncate_text(text: str, char_limit: int, suffix: str = "…") -> str:
        """
        Hard-truncate text to char_limit, appending suffix if truncated.
        Used for system_prompt and patterns.
        """

        if not text or len(text) <= char_limit:
            return text

        limit = char_limit - len(suffix)
        truncate_text = split_message(text, limit=limit)
        truncate_text = truncate_text[0] if truncate_text else text[:limit]

        return truncate_text + suffix

    @staticmethod
    def fit_messages_to_token_budget(
        messages: list,  # list of Message ORM objects, newest-first
        token_budget: int,
    ) -> list:
        """
        Walk backwards through messages (newest → oldest), accumulating
        token cost until the budget is exhausted. Returns the kept subset
        in chronological order (oldest → newest) for correct LLM context.

        This is the "drop oldest first" strategy for history.
        """

        kept = []
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
        memories: list,  # list of (Message, similarity_score), highest score first
        char_budget: int,
    ) -> list:
        """
        Include embeddings from highest similarity downward until char_budget
        is exhausted. Lowest-similarity results are dropped first naturally
        since they appear at the end of the ranked list.
        """

        kept = []
        chars_used = 0

        for msg, score in memories:
            cost = len(msg.content)
            if chars_used + cost > char_budget:
                # Partial inclusion not useful for a message — skip remainder
                break
            kept.append((msg, score))
            chars_used += cost

        return kept

    @staticmethod
    def truncate_tool_response(response: str, char_budget: int) -> str:
        """
        Truncate a single MCP tool response to char_budget.
        Appends a note so the LLM knows the response was clipped.
        """

        if len(response) <= char_budget:
            return response
        note = "\n[...response truncated to fit context window]"
        return response[: char_budget - len(note)] + note

    @classmethod
    def _estimate_tool_def_tokens(cls, tool_definitions: list[dict]) -> int:
        """
        Tool definitions are JSON — chars map more closely to tokens than prose.
        We use a slightly tighter ratio here (3.0) to be conservative.
        """

        if not tool_definitions:
            return 0
        raw = json.dumps(tool_definitions)
        return int(len(raw) / 3.0)

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        return int(len(text) / CHARS_PER_TOKEN)

    @staticmethod
    def _tokens_to_chars(tokens: int) -> int:
        return int(tokens * CHARS_PER_TOKEN)
