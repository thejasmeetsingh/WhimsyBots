"""Conversation Summary Service"""

import logging
from typing import Optional

from app.models import Bot, Message, Ollama
from app.choices import MessageRole
from clients.ollama import OllamaClient
from prompts import SUMMARY_PROMPT

logger = logging.getLogger(__name__)

# Fraction of num_ctx to use as our character budget.
# Remaining 20% is headroom for tool definitions, system prompt, and LLM response.
CTX_BUDGET_RATIO = 0.8

# Rough characters-per-token estimate (industry standard approximation).
CHARS_PER_TOKEN = 4


class ConversationSummaryService:
    """
    Handles context window management for a single bot.

    Calculates whether the bot's message history exceeds the configured
    num_ctx budget, and if so summarizes the overflow messages via Ollama
    and persists the result as a role='S' Message record.
    """

    def __init__(self, bot: Bot, ollama: Ollama, ollama_client: OllamaClient):
        self.bot = bot
        self.ollama = ollama
        self.ollama_client = ollama_client
        self.budget = int(ollama.num_ctx * CHARS_PER_TOKEN * CTX_BUDGET_RATIO)

    def process(self) -> Optional[tuple[Message, bool]]:
        """
        Evaluate the bot's message history against the context budget.

        If history exceeds the budget, summarizes the overflow into a single
        Message(role='S') record. If history fits within the budget,
        any existing summary is deleted (it's no longer needed).

        Returns:
            tuple consisting:
                Message instance with role='S'
                A boolean identifies the operation
        """

        # Fetch only messages in chronological order
        messages = Message.objects.filter(bot_id=self.bot.id).order_by("created_at")
        conversations = list(
            messages.filter(
                role__in=[MessageRole.USER.value[0], MessageRole.ASSISTANT.value[0]]
            )
        )
        summary_msg = messages.filter(role=MessageRole.SYSTEM.value[0]).first()

        if not conversations:
            return None

        in_window, overflowed = self._split_by_budget(conversations)
        if not overflowed:
            logger.info(f"No overflowed messages found for bot: {self.bot.name}")
            return None

        # Generate summary for overflowed messages
        summary_text = self._summarize(messages=overflowed, summary_msg=summary_msg)
        if not summary_text:
            return None

        if summary_msg:
            created = False
            summary_msg.content = summary_text
        else:
            created = True
            summary_msg = Message(
                bot=self.bot,
                role=MessageRole.SYSTEM.value[0],
                content=summary_text,
            )

        logger.info(
            f"covered {len(overflowed)} messages, {len(in_window)} in window for bot: {self.bot.name}"
        )

        return summary_msg, created

    def _split_by_budget(
        self, messages: list[Message]
    ) -> tuple[list[Message], list[Message]]:
        """
        Walk messages newest-to-oldest, accumulating character count.
        Messages that fit within the budget form the 'in_window' list.
        Everything older is 'overflowed' and should be summarized.

        Args:
            messages: Chronologically ordered list of Message instances

        Returns:
            (in_window, overflowed) — both in chronological order
        """

        accumulated = 0
        in_window = []

        for msg in reversed(messages):
            msg_len = len(msg.content)
            if accumulated + msg_len <= self.budget:
                accumulated += msg_len
                in_window.append(msg)
            else:
                break

        # in_window was built newest-first, reverse back to chronological
        in_window.reverse()

        # Everything not in the window is overflow
        in_window_ids = {str(m.id) for m in in_window}
        overflowed = [m for m in messages if str(m.id) not in in_window_ids]

        return in_window, overflowed

    def _summarize(
        self, messages: list[Message], summary_msg: Optional[Message]
    ) -> Optional[str]:
        """
        Call Ollama to produce a summary of the given messages.

        Args:
            messages: Overflow messages to summarize (chronological order)
            summary_msg: Message object with role='S'

        Returns:
            Summary text string, or None if the call fails.
        """

        # Format messages for the prompt
        formatted = "\n".join(
            f"{('User' if m.role == MessageRole.USER.value[0] else 'Assistant')}: {m.content}"
            for m in messages
        )

        previous_summary = summary_msg.content if summary_msg else "NA"

        prompt = SUMMARY_PROMPT.format(
            previous_summary=previous_summary, messages=formatted
        )

        try:
            response = self.ollama_client.chat(
                model=self.ollama.default_model,
                messages=[{"role": "user", "content": prompt}],
                options={
                    "temperature": 0.3,  # lower temp for factual summarization
                    "num_ctx": self.ollama.num_ctx,
                    "num_predict": self.ollama.num_predict,
                },
            )
            return response.get("message", "")

        except Exception:
            logger.error(
                f"Failed to generate summary for bot: {self.bot.name}",
                exc_info=True,
            )
            return None
