"""Cross-domain formatting helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_messages_to_ollama_format(
    messages, system_prompt: Optional[str] = None
) -> List[Dict[str, str]]:
    """Convert 'app.models.Message' rows to Ollama chat format.

    WhimsyBots stores the conversation role as a single-letter code
    ('U' for user, 'A' for assistant) on the database side.
    Ollama's chat API expects the verbose role name ('user' /
    'assistant') instead. This helper does that translation and
    also prepends a system prompt when one is provided.

    Messages whose role is not in the role mapping (e.g. system /
    summary messages) are silently skipped — the caller is expected to
    pass only conversation messages.

    Args:
        messages: Queryset or iterable of 'app.models.Message'
            instances (user / assistant only).
        system_prompt: Optional runtime system prompt. When provided it
            is always inserted as the first message.

    Returns:
        A list of '{"role": ..., "content": ...}' dicts ready to be sent to Ollama.
    """
    role_mapping = {
        "U": "user",
        "A": "assistant",
    }

    ollama_messages: list[dict[str, str]] = []

    if system_prompt:
        ollama_messages.append({"role": "system", "content": system_prompt})

    for message in messages:
        if message.role not in role_mapping:
            continue
        ollama_role = role_mapping[message.role]
        ollama_messages.append({"role": ollama_role, "content": message.content})

    return ollama_messages


def get_admin_link(model: str, value: int, obj: Any) -> str:
    """Render an HTML anchor for filtered admin list views.

    Used in admin list displays to create quick-access links to the
    filtered related-object list (e.g. clicking a message count links
    to the messages filtered by 'bot_id').

    Args:
        model: Model name in lowercase (e.g. 'message', 'mcpserver', 'log').
        value: Display value / count to use as the link text.
        obj: Parent object instance — its 'id' is used as the filter value.

    Returns:
        An HTML '<a>' tag, or the literal string '0' when
        'obj' is missing or 'value' is falsy.
    """
    if not obj or not value:
        return "0"
    return f"<a href='/admin/app/{model}/?bot_id={str(obj.id)}' target='_blank'>{value}</a>"
