"""Conversation Summary Service."""

import asyncio
import logging
from typing import Optional

from app.choices import MessageRole
from app.models import Bot, Message, Ollama
from clients.ollama import OllamaClient
from prompts import SUMMARY_PROMPT
from services.token_budget import TokenBudgetService
from services.tool_executor import MCPToolsBuilder
from strings import SUMMARY_UNAVAILABLE

logger = logging.getLogger(__name__)


class ConversationSummaryService:
    """Handles context window management for a single bot.

    Determines whether the bot's message history exceeds its token budget
    (sourced from TokenBudgetService), and if so summarizes the overflow
    messages via Ollama and persists the result as a role='S' Message record.

    The history token budget already accounts for tool definitions, output
    reservation, skills, embeddings, and all other fixed costs — so the
    split here reflects exactly how many messages ContextAssembler will
    actually be able to include.
    """

    def __init__(
        self,
        bot: Bot,
        ollama: Ollama,
        ollama_client: OllamaClient,
    ):
        """Initialize the service with bound bot, Ollama config, and client.

        Args:
            bot: Bot instance.
            ollama: Ollama configuration.
            ollama_client: Configured OllamaClient for the summary call.
        """
        self.bot = bot
        self.ollama = ollama
        self.ollama_client = ollama_client

        self.history_token_budget = 0
        self.summary_chars = 0

    def _set_summary_budget(self):
        # Build tools from servers
        mcp_servers = getattr(self.bot, "mcp_servers_list", [])
        tools_config = asyncio.run(
            MCPToolsBuilder.build_tools_from_servers(
                bot_id=str(self.bot.id), mcp_servers=mcp_servers
            )
        )

        # Derive the history token budget the same way ContextAssembler does,
        # so the split point here is always consistent with what gets sent to
        # the LLM during message processing.
        budget = TokenBudgetService.compute(self.ollama, [tool.tool for tool in tools_config])
        self.history_token_budget = budget.history_tokens
        self.summary_chars = budget.summary_chars

    def process(self) -> Optional[tuple[Message, bool]]:
        """Evaluate the bot's message history against the history token budget.

        If history exceeds the budget, summarizes the overflow into a single
        Message(role='S') record. If history fits within the budget,
        returns None (no action needed).

        Returns:
            (Message, created: bool) where Message has role='S', or None.
        """
        conversations = getattr(self.bot, "conversations")
        if not conversations:
            return None

        summary_msg = (
            self.bot.system_messages[0]
            if hasattr(self.bot, "system_messages") and self.bot.system_messages
            else None
        )

        if not conversations:
            return None

        self._set_summary_budget()

        in_window, overflowed = self._split_by_budget(conversations)

        if not overflowed:
            logger.info("No overflowed messages for bot: %s", self.bot.name)
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
            "Summarized %d overflowed messages, %d remain in window for bot: %s",
            len(overflowed),
            len(in_window),
            self.bot.name,
        )

        return summary_msg, created

    def _split_by_budget(self, messages: list[Message]) -> tuple[list[Message], list[Message]]:
        """Split messages into in-window and overflowed lists.

        Delegates the budget-aware split to TokenBudgetService so the logic
        is never duplicated. Messages that fit within history_token_budget
        form the 'in_window' list; everything older is 'overflowed'.

        Args:
            messages: Chronologically ordered list of Message instances.

        Returns:
            (in_window, overflowed) — both in chronological order.
        """
        in_window = TokenBudgetService.fit_messages_to_token_budget(
            messages=messages,
            token_budget=self.history_token_budget,
        )

        in_window_ids = {str(m.id) for m in in_window}
        overflowed = [m for m in messages if str(m.id) not in in_window_ids]

        return in_window, overflowed

    def _summarize(self, messages: list[Message], summary_msg: Optional[Message]) -> Optional[str]:
        """Calls Ollama to produce a summary of the overflowed messages.

        Args:
            messages:    Overflow messages to summarize (chronological order).
            summary_msg: Existing summary Message (role='S'), if any.

        Returns:
            Summary text string, or None if the call fails.
        """
        formatted = "\n".join(
            f"{'User' if m.role == MessageRole.USER.value[0] else 'Assistant'}: {m.content}"
            for m in messages
        )

        previous_summary = summary_msg.content if summary_msg else SUMMARY_UNAVAILABLE

        prompt = SUMMARY_PROMPT.format(
            limit=self.summary_chars,
            previous_summary=previous_summary,
            messages=formatted,
        )

        try:
            response = self.ollama_client.chat(
                model=self.bot.ollama_model,
                messages=[{"role": "user", "content": prompt}],
                options={
                    "temperature": 0.3,
                    "num_ctx": self.ollama.num_ctx,
                    "num_predict": self.ollama.num_predict,
                },
            )
            return response.get("message", "")

        except Exception:
            logger.error(
                "Failed to generate summary for bot: %s",
                self.bot.name,
                exc_info=True,
            )
            return None
