"""ContextAssembler — builds the final system prompt and history payload.

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

import logging
from typing import Any, Optional

from django.utils import timezone
from pydantic import BaseModel

from app.choices import MessageRole
from app.models import Bot, Message, Ollama
from prompts import DEFAULT_SYSTEM_PROMPT
from services.embedding import EmbeddingService
from services.token_budget import TokenBudget, TokenBudgetService
from strings import SUMMARY_UNAVAILABLE
from utils.formatting import convert_messages_to_ollama_format

logger = logging.getLogger(__name__)

# Separator used between sections in the system prompt.
SECTION_SEP = "\n\n---\n\n"

# Recent Messages Cap For History
RECENT_MESSAGES_CAP: int = 200


class AssembledContext(BaseModel):
    """Pydantic model holding the final context handed to the LLM."""

    history: list[dict[str, str]]  # Ollama-formatted message dicts [{"role": ..., "content": ...}]
    budget: TokenBudget  # Carried through for logging / debugging


class ContextAssembler:
    """Orchestrates system-prompt assembly and history fitting."""

    def __init__(self, bot: Bot, ollama: Ollama, current_message: Message):
        """Initialize the processor.

        Args:
            bot: Bot instance
            ollama: Ollama configuration
            current_message: Current Message
        """
        self.bot = bot
        self.ollama = ollama
        self.current_message = current_message

    def assemble(
        self,
        tool_definitions: list[dict[str, Any]],
    ) -> AssembledContext:
        """Run the full context-assembly pipeline.

        Steps:
            1. Compute token budget.
            2. Fetch + fit each context source within its budget.
            3. Assemble system prompt string.
            4. Fit conversation history to token budget.
        """
        # Fetch messages
        messages = Message.objects.filter(bot_id=self.bot.id)

        # Filter only conversational messages using the same messages QuerySet
        conversations = messages.filter(
            role__in=[MessageRole.USER.value[0], MessageRole.ASSISTANT.value[0]]
        )[:RECENT_MESSAGES_CAP]  # hard cap: never scan more than 200 messages

        # Retreive summary (if available) using the same messages QuerySet
        summary = messages.filter(role=MessageRole.SYSTEM.value[0]).first()
        summary_msg = summary.content if summary else SUMMARY_UNAVAILABLE

        # 1. Budget
        budget = TokenBudgetService.compute(self.ollama, tool_definitions)

        # 2. Fetch & truncate each source
        # System prompt — truncate from bottom (preserve the opening intent)
        raw_system_prompt = DEFAULT_SYSTEM_PROMPT.format(
            system_prompt=self.bot.system_prompt or "You are a helpful assistant",
            current_dt=timezone.now().isoformat(),
            summary=summary_msg,
        )

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

        # Observed patterns — generated async, stored on bot, always protected
        patterns_block = self._fit_patterns(budget)

        # Relevant memories via embeddings
        memories_block = (
            self._fit_memories(budget)
            if self.current_message.content_embedding is not None
            else None
        )

        # 3. Assemble system prompt
        sections: list[str] = []

        if fitted_system_prompt:
            sections.append(fitted_system_prompt)

        if patterns_block:
            sections.append(patterns_block)

        if memories_block:
            sections.append(memories_block)

        system_prompt = SECTION_SEP.join(sections)

        # 4. Fit conversation history
        history: list[dict[str, str]] = self._fit_history(
            budget, messages=conversations, system_prompt=system_prompt
        )

        return AssembledContext(
            history=history,
            budget=budget,
        )

    def _fit_patterns(self, budget: TokenBudget) -> str:
        """Render the observed-patterns block, truncating to fit the budget.

        Patterns are always protected — they're already capped at 4096 chars
        during generation (see ObservedPatternsService). We still apply
        budget.patterns_chars as a secondary safety net.
        """
        if not self.bot.observed_patterns:
            return ""

        fitted = TokenBudgetService.truncate_text(
            self.bot.observed_patterns,
            budget.patterns_chars,
        )

        return f"## Observed User Patterns\n{fitted}"

    def _fit_memories(
        self,
        budget: TokenBudget,
        top_k: int = 5,
    ) -> str:
        """Retrieve top-k past messages that fit the embedding budget.

        Retrieve top-k semantically similar past USER messages, then drop
        lowest-similarity results until they fit within embedding_chars.
        """
        if budget.embedding_chars <= 0:
            return ""

        # Returns list of (Message, similarity_score) sorted by score desc
        embedding_svc = EmbeddingService(self.bot, self.ollama)
        raw_memories = embedding_svc.get_relevant_memories(
            query_vector=self.current_message.content_embedding,
            current_message_id=str(self.current_message.id),
            top_k=top_k,
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
        for msg, _ in fitted:
            ts = msg.created_at.strftime("%Y-%m-%d")
            lines.append(f"[{ts}] {msg.content}")

        return "\n".join(lines)

    def _fit_history(
        self,
        budget: TokenBudget,
        messages: list[Message],
        system_prompt: Optional[str] = None,
    ) -> list[dict[str, str]]:
        """Fit conversation history into the history-token budget.

        Walks newest→oldest for the given messages and keeps them until
        the history_tokens budget is exhausted. Returns Ollama-formatted
        dicts in chronological order.
        """
        fitted = TokenBudgetService.fit_messages_to_token_budget(
            messages=messages,
            token_budget=budget.history_tokens,
        )

        return convert_messages_to_ollama_format(messages=fitted, system_prompt=system_prompt)
