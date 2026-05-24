"""
ContextAssembler — builds the final system prompt and history payload
that gets sent to Ollama for each message processing cycle.

This is the single place where all context sources are gathered,
truncated, and assembled in priority order.

Assembly order (top → bottom in system prompt):
  1. Bot system prompt     (user-authored, truncated if needed)
  2. Skills block          (static, always present, never truncated)
  3. Observed patterns     (async-generated user behavior profile, protected)
  4. Relevant memories     (top-k embedding results, lowest-sim dropped first)

Conversation history is passed separately as the messages array (not in
the system prompt), with oldest messages dropped first when budget is tight.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

from django.utils import timezone

from app.models import Bot, Message, Ollama
from app.utils import convert_messages_to_ollama_format
from services import TokenBudgetService, TokenBudget, SkillsRegistry, EmbeddingService


logger = logging.getLogger(__name__)

# Separator used between sections in the system prompt.
SECTION_SEP = "\n\n---\n\n"


@dataclass
class AssembledContext:
    system_prompt: str
    history: list[
        dict
    ]  # Ollama-formatted message dicts [{"role": ..., "content": ...}]
    budget: TokenBudget  # Carried through for logging / debugging


class ContextAssembler:
    @classmethod
    def assemble(
        cls,
        bot: "Bot",
        current_message: "Message",
        ollama_config: "Ollama",
        tool_definitions: list[dict],
        active_mcp_server_names: list[str],
    ) -> AssembledContext:
        """
        Full pipeline:
          1. Compute token budget
          2. Fetch + fit each context source within its budget
          3. Assemble system prompt string
          4. Fit conversation history to token budget
        """

        # ── 1. Budget ────────────────────────────────────────────────────────
        budget = TokenBudgetService.compute(ollama_config, tool_definitions)

        # ── 2. Fetch & truncate each source ──────────────────────────────────

        # System prompt — truncate from bottom (preserve the opening intent)
        raw_system_prompt = bot.system_prompt or ""
        fitted_system_prompt = TokenBudgetService.truncate_text(
            raw_system_prompt,
            budget.system_prompt_chars,
        )
        if len(raw_system_prompt) > budget.system_prompt_chars:
            logger.info(
                "System prompt truncated: original=%d chars, limit=%d chars",
                len(raw_system_prompt),
                budget.system_prompt_chars,
            )

        # Skills — static, always fits, no truncation needed
        skills_block = SkillsRegistry.get_skills_block(active_mcp_server_names)

        # Observed patterns — generated async, stored on bot, always protected
        patterns_block = cls._fit_patterns(bot, budget)

        # Relevant memories via embeddings
        memories_block = cls._fit_memories(current_message, bot, ollama_config, budget)

        # ── 3. Assemble system prompt ─────────────────────────────────────────
        sections = []

        if fitted_system_prompt:
            sections.append(fitted_system_prompt)

        if skills_block:
            sections.append(skills_block)

        if patterns_block:
            sections.append(patterns_block)

        if memories_block:
            sections.append(memories_block)

        # Always-present bot metadata injection (existing behaviour)
        sections.append(cls._bot_metadata_block(bot))

        system_prompt = SECTION_SEP.join(sections)

        # ── 4. Fit conversation history ───────────────────────────────────────
        history = cls._fit_history(bot, current_message, budget)

        return AssembledContext(
            system_prompt=system_prompt,
            history=history,
            budget=budget,
        )

    @staticmethod
    def _fit_patterns(bot: "Bot", budget: TokenBudget) -> str:
        """
        Patterns are always protected — they're already capped at 4096 chars
        during generation (see ObservedPatternsService). We still apply
        budget.patterns_chars as a secondary safety net.
        """

        if not bot.observed_patterns:
            return ""

        fitted = TokenBudgetService.truncate_text(
            bot.observed_patterns,
            budget.patterns_chars,
        )

        return f"## Observed User Patterns\n{fitted}"

    @staticmethod
    def _fit_memories(
        current_message: "Message",
        bot: "Bot",
        ollama_config: "Ollama",
        budget: TokenBudget,
        top_k: Optional[int] = 5,
    ) -> str:
        """
        Retrieve top-k semantically similar past USER messages, then drop
        lowest-similarity results until they fit within embedding_chars.
        """

        if budget.embedding_chars <= 0:
            return ""

        # Returns list of (Message, similarity_score) sorted by score desc
        raw_memories = EmbeddingService.get_relevant_memories(
            query_text=current_message.content,
            bot_id=str(bot.id),
            current_message_id=str(current_message.id),
            top_k=top_k,
            ollama_config=ollama_config,
        )

        if not raw_memories:
            return ""

        fitted = TokenBudgetService.fit_embeddings_to_char_budget(
            raw_memories,
            budget.embedding_chars,
        )

        if not fitted:
            return ""

        lines = ["## Relevant Past Context"]
        lines.append(
            "The following are past messages from this conversation that may be relevant:\n"
        )
        for msg, _score in fitted:
            ts = msg.created_at.strftime("%Y-%m-%d")
            lines.append(f"[{ts}] {msg.content}")

        return "\n".join(lines)

    @staticmethod
    def _fit_history(
        bot: "Bot",
        current_message: "Message",
        budget: TokenBudget,
    ) -> list[dict]:
        """
        Fetch recent messages, walk newest→oldest, keep until history_tokens
        budget is exhausted. Returns Ollama-formatted dicts in chronological order.
        """

        # Exclude the current message (it's sent as the live user turn, not history)
        recent = list(
            Message.objects.filter(bot_id=bot.id)
            .exclude(id=current_message.id)
            .order_by("-created_at")[
                # newest first for budget walk
                :200
            ]  # hard cap: never scan more than 200 messages
        )

        fitted = TokenBudgetService.fit_messages_to_token_budget(
            messages=recent,
            token_budget=budget.history_tokens,
        )

        return convert_messages_to_ollama_format(messages=fitted)

    @staticmethod
    def _bot_metadata_block(bot: "Bot") -> str:
        """
        Always-present metadata injected at the end of the system prompt.
        Matches existing behaviour in BotMessageProcessor.
        """

        tz_name = str(timezone.get_current_timezone())
        return f"Bot ID: {bot.id}\nCurrent timezone: {tz_name}"
