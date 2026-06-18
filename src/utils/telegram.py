"""
Telegram payload parsing helpers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def parse_telegram_update(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalise a raw Telegram update payload into a flat dict.

    Telegram's webhook JSON is deeply nested and varies in shape
    depending on the update type. This helper pulls out the fields
    WhimsyBots actually uses, and returns None for payloads that
    don't carry a 'message' (e.g. inline queries, callback queries,
    edited-channel posts) so the caller can short-circuit cleanly.

    Args:
        update: Raw Telegram webhook update JSON.

    Returns:
        A flat dict with the following keys, or None if the update
        has no 'message' field:

        — 'update_id': Unique update ID.
        — 'chat_id': Chat identifier (stringified for safety).
        — 'text': Message text (stripped).
        — 'username': Sender's Telegram username (may be empty).
        — 'first_name': Sender's first name (may be empty).
        — 'date': Message timestamp as returned by Telegram.
    """

    msg = update.get("message")
    if not msg:
        return None

    return {
        "update_id": update["update_id"],
        "chat_id": str(msg["chat"]["id"]),
        "text": msg.get("text", "").strip(),
        "username": msg.get("from", {}).get("username", ""),
        "first_name": msg.get("from", {}).get("first_name", ""),
        "date": msg.get("date"),
    }
