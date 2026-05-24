"""
ObservedPatternsService — generates and refreshes a behavioral profile
for each bot's user, stored on Bot.observed_patterns.

Triggered every PATTERN_REGEN_EVERY_N_MESSAGES user messages (configured
in settings). Runs async via Celery — never blocks message processing.

What gets captured:
  - Communication preferences (tone, format, length)
  - Recurring topics or themes
  - Behavioral habits (time of day, journaling style, goal patterns)
  - Implicit expectations (e.g. "user prefers concise answers")

The output is always capped at MAX_PATTERN_CHARS (4096) so it fits in the
minimum context window regardless of how verbose the LLM gets.
"""

from __future__ import annotations
import logging

from clients.ollama import OllamaClient
from app.models import Bot, Ollama, Message
from app.choices import MessageRole
from prompts import PATTERN_GENERATION_PROMPT


logger = logging.getLogger(__name__)

# Hard cap — must always fit in the smallest supported context window.
MAX_PATTERN_CHARS: int = 4096

# How many recent messages to analyze when regenerating patterns.
# More messages = richer profile, but higher LLM cost per regen.
PATTERN_ANALYSIS_MESSAGE_LIMIT: int = 100


class ObservedPatternsService:
    @classmethod
    def should_regenerate(cls, bot_id: str, n: int) -> bool:
        """
        Returns True if the current user message count is a multiple of N,
        meaning it's time to trigger a pattern regeneration.

        Called from process_inbound_message after saving the user message.

        Args:
            bot_id: The bot's UUID string
            n: Regen interval from settings (PATTERN_REGEN_EVERY_N_MESSAGES)
        """

        count = Message.objects.filter(
            bot_id=bot_id,
            role=MessageRole.USER.value[0],
        ).count()

        return count > 0 and count % n == 0

    @classmethod
    def regenerate(cls, bot: "Bot", ollama_config: "Ollama") -> None:
        """
        Main entry point called by the Celery task.

        1. Fetch the last N user+assistant messages for context richness
        2. Format as a readable conversation transcript
        3. Ask the LLM to extract behavioral patterns
        4. Truncate result to MAX_PATTERN_CHARS
        5. Save to Bot.observed_patterns
        """

        messages = list(
            Message.objects.filter(bot_id=bot.id).order_by("-created_at")[
                :PATTERN_ANALYSIS_MESSAGE_LIMIT
            ]
        )
        messages.reverse()  # chronological order for readability

        if not messages:
            logger.info("No messages found for bot %s, skipping pattern regen", bot.id)
            return

        conversation_history = cls._format_history(messages)

        prompt = PATTERN_GENERATION_PROMPT.format(
            max_chars=MAX_PATTERN_CHARS,
            conversation_history=conversation_history,
        )

        raw_patterns = cls._call_llm(prompt, bot, ollama_config)

        if not raw_patterns:
            logger.warning(
                "Pattern generation returned empty result for bot %s", bot.id
            )
            return

        # Hard cap — LLM instructions alone can't be fully trusted
        truncated = raw_patterns[:MAX_PATTERN_CHARS]

        bot.observed_patterns = truncated
        bot.save(update_fields=["observed_patterns", "updated_at"])

        logger.info(
            "Observed patterns updated for bot %s (%d chars)",
            bot.id,
            len(truncated),
        )

    @staticmethod
    def _format_history(messages: list) -> str:
        """
        Formats messages as a readable transcript for the LLM to analyze.
        Example:
          [2024-01-15 09:30] User: I want to track my water intake
          [2024-01-15 09:31] Assistant: Great idea! I can help you with that.
        """

        role_labels = {
            MessageRole.USER.value[0]: "User",
            MessageRole.ASSISTANT.value[0]: "Assistant",
        }

        lines = []
        for msg in messages:
            if msg.role == MessageRole.SYSTEM.value[0]:
                continue
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M")
            label = role_labels.get(msg.role, "Unknown")
            lines.append(f"[{ts}] {label}: {msg.content}")
        return "\n".join(lines)

    @staticmethod
    def _call_llm(prompt: str, bot: "Bot", ollama_config: "Ollama") -> str | None:
        """
        Simple single-turn LLM call (no tools, no structured output).
        Patterns are freeform prose — we just need a clean text response.
        """

        client = OllamaClient(ollama_config.endpoint, api_key=ollama_config.api_key)
        model = bot.ollama_model

        try:
            response = client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                # No tools, no structured format — plain text output
            )

            # Extract text content from the response
            return response.get("message", "")
        except Exception as exc:
            logger.exception(
                "Pattern generation LLM call failed for bot %s: %s", bot.id, exc
            )
            return None
