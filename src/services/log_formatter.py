"""Log formatter service"""


class LogFormatter:
    """
    Builds human-readable log description strings for the Log model.

    Each field is on its own line in key=value format.
    Enum values use their full readable label (e.g. "Journal Entry" not "J").

    Usage:
        desc = LogFormatter("process_inbound_message")
            .add("bot", bot.name)
            .add("intent", "Journal Entry")
            .add("ollama", "1243ms")
            .build()

    Output:
        [process_inbound_message]
        bot=MyJournalBot
        intent=Journal Entry
        ollama=1243ms
    """

    def __init__(self, task_name: str):
        self._task_name = task_name
        self._fields: list[tuple[str, str]] = []

    def add(self, key: str, value) -> "LogFormatter":
        """Add a key=value field. Skips None values cleanly."""
        if value is not None:
            self._fields.append((key, str(value)))
        return self

    def build(self) -> str:
        lines = [f"[{self._task_name}]"] + [f"{k}={v}" for k, v in self._fields]
        return "\n".join(lines)
