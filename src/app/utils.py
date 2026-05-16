"""
Utility functions for WhimsyBots application.

This module provides helper functions for:
- Scheduling and time calculations
- Message formatting and parsing
- PDF generation from HTML content
- Admin interface helpers
- Message format conversions
"""

import re
import base64
import hashlib
import logging
from io import BytesIO
from typing import Optional, Dict, List
from cryptography.fernet import Fernet

from weasyprint import HTML
from croniter import croniter
from django.utils import timezone
from django.conf import settings


logger = logging.getLogger(__name__)


def calculate_next_run_at(cron_expression: Optional[str]) -> timezone.datetime:
    """
    Calculate the next scheduled run time for a cron job.

    Args:
        cron_expression (str | None): Standard cron expression for scheduling

    Returns:
        datetime: Next run timestamp

    Example:
        >>> next_run = calculate_next_run_at(cron_expression="0 9 * * *")
    """

    current_dt = timezone.now()
    return croniter(cron_expression, current_dt).get_next(timezone.datetime)


def get_admin_link(model: str, value: int, obj) -> str:
    """
    Generate an HTML link for admin filtering in Django admin.

    Used in admin list displays to create quick-access links to filtered
    related objects (e.g., clicking message count links to messages).

    Args:
        model (str): Model name in lowercase (e.g., 'message', 'mcpserver', 'log')
        value (int): Display value / count to show as link text
        obj: Parent object instance (used to get filter value)

    Returns:
        str: HTML anchor tag or plain value if no filtering needed

    Example:
        >>> get_admin_link("message", 42, bot_obj)
        # Returns: <a href='/admin/app/message/?bot_id=<uuid>' target='_blank'>42</a>
    """

    if not obj or not value:
        return value
    return f"<a href='/admin/app/{model}/?bot_id={str(obj.id)}' target='_blank'>{value}</a>"


def split_message(text: str, limit: int = 4096) -> List[str]:
    """
    Split a long message into chunks respecting Telegram's character limit.

    Attempts to split at the most natural boundary available, in this priority:
        1. End of a closing code fence (```) — never break inside a code block
        2. Paragraph boundary (double newline)
        3. Line boundary (single newline)
        4. Sentence boundary ('. ', '! ', '? ')
        5. Word boundary (space)
        6. Hard cut (fallback — should rarely happen)

    If a split occurs mid-code-block, the next chunk is automatically prefixed
    with a re-opened code fence (preserving the language tag if present) so
    Telegram renders it correctly.

    Args:
        text (str): Text to split
        limit (int): Maximum characters per chunk (default: 4096 for Telegram)

    Returns:
        list[str]: List of text chunks, each <= limit characters
    """

    if len(text) <= limit:
        return [text]

    chunks = []
    # Tracks the opening line of an unclosed code fence e.g. "```python"
    # so we can re-open it at the start of the next chunk if needed.
    open_fence: str | None = None

    while text:
        # If we're inside a code block, reserve space for the re-opening fence
        # line and a closing fence at the end of this chunk.
        if open_fence:
            # "```python\n" + chunk content + "\n```"
            reserved = len(open_fence) + 1 + 4  # +1 for \n after tag, +4 for \n```
            effective_limit = limit - reserved
        else:
            effective_limit = limit

        if len(text) <= effective_limit:
            # Remaining text fits — close any open fence and we're done.
            chunk = f"{open_fence}\n{text}\n```" if open_fence else text
            chunks.append(chunk)
            break

        # --- Find the best split point within effective_limit ---
        window = text[:effective_limit]
        split_at = _find_split_point(window)

        chunk_text = text[:split_at].rstrip()
        text = text[split_at:].lstrip()

        # --- Code fence tracking ---
        # Check if this chunk contains an unclosed code fence.
        open_fence = _get_unclosed_fence(chunk_text, open_fence)

        # Wrap chunk with fence markers if we were inside a code block.
        if open_fence:
            # Close the fence at the end of this chunk so it renders properly,
            # then re-open it at the start of the next iteration.
            chunk = (
                f"{open_fence}\n{chunk_text}\n```"
                if _needs_fence_prefix(chunk_text, open_fence)
                else f"{chunk_text}\n```"
            )
        else:
            chunk = chunk_text

        chunks.append(chunk)

    return chunks


def _find_split_point(window: str) -> int:
    """
    Find the best character index to split at within the given window.

    Scans backwards from the end of the window to find the highest-priority
    natural boundary. Falls back to a hard cut at len(window) if none found.

    Args:
        window (str): The text slice we are allowed to consume (len <= limit)

    Returns:
        int: Index at which to split (exclusive — text[:split_at] is the chunk)
    """

    # Priority 1: closing code fence on its own line
    idx = window.rfind("\n```")
    if idx != -1:
        return idx + 4  # include the closing ```

    # Priority 2: paragraph boundary
    idx = window.rfind("\n\n")
    if idx != -1:
        return idx + 2

    # Priority 3: line boundary
    idx = window.rfind("\n")
    if idx != -1:
        return idx + 1

    # Priority 4: sentence boundary
    for terminator in (". ", "! ", "? "):
        idx = window.rfind(terminator)
        if idx != -1:
            return idx + len(terminator)

    # Priority 5: word boundary
    idx = window.rfind(" ")
    if idx != -1:
        return idx + 1

    # Priority 6: hard cut (no natural boundary found)
    return len(window)


def _get_unclosed_fence(chunk: str, currently_open: str | None) -> str | None:
    """
    Scan a chunk line-by-line to determine whether we end inside a code block.

    Args:
        chunk (str): The chunk text to scan
        currently_open (str | None): Any fence that was already open before this chunk

    Returns:
        str | None: The opening fence line (e.g. "```python") if a block is
                    still open at the end of the chunk, otherwise None.
    """

    open_fence = currently_open

    for line in chunk.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if open_fence is None:
                # Opening a new fence — capture the whole line (may have a lang tag)
                open_fence = stripped
            else:
                # Closing the open fence
                open_fence = None

    return open_fence


def _needs_fence_prefix(chunk_text: str, open_fence: str) -> bool:
    """
    Check whether the chunk text already starts with the fence opener.

    Avoids double-prefixing when the split happened exactly at a fence boundary.

    Args:
        chunk_text (str): The chunk content
        open_fence (str): The fence opening line (e.g. "```python")

    Returns:
        bool: True if the prefix should be added
    """

    return not chunk_text.lstrip().startswith(open_fence)


def parse_telegram_update(update: Dict) -> Optional[Dict[str, str]]:
    """
    Parse a Telegram webhook update into a standardized format.

    Extracts relevant fields from Telegram's JSON update format for
    easier handling in the application.

    Args:
        update (dict): Raw Telegram webhook update JSON

    Returns:
        dict | None: Parsed update with keys:
            - update_id: Unique update ID
            - chat_id: Chat identifier (stringified)
            - text: Message text (stripped)
            - username: User's Telegram username
            - first_name: User's first name
            - date: Message timestamp
        Returns None if update format is invalid

    Example:
        >>> telegram_update = {...}  # Raw Telegram webhook data
        >>> parsed = parse_telegram_update(telegram_update)
        >>> if parsed:
        ...     print(f"Message from {parsed['first_name']}: {parsed['text']}")
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


def generate_pdf(html_content: str) -> bytes:
    """
    Convert HTML content to PDF bytes using WeasyPrint.

    Supports inline CSS styling for custom formatted reports.
    Converts to PDF in-memory and returns bytes for file operations.

    Args:
        html_content (str): HTML string to convert (can include inline CSS)

    Returns:
        bytes: PDF file content as bytes

    Raises:
        Exception: If PDF generation fails (logged as error)

    Example:
        >>> html = "<html><body><h1>Report</h1></body></html>"
        >>> pdf_bytes = generate_pdf(html)
        >>> with open("report.pdf", "wb") as f:
        ...     f.write(pdf_bytes)
    """

    pdf_buffer = BytesIO()

    try:
        HTML(string=html_content).write_pdf(target=pdf_buffer)
    except Exception as e:
        logger.exception(f"WeasyPrint PDF generation failed: {e}")
        raise

    pdf_buffer.seek(0)
    return pdf_buffer.read()


def convert_messages_to_ollama_format(
    messages, system_prompt: Optional[str] = None, summary: Optional[str] = None
) -> List[Dict[str, str]]:
    """
    Convert Message model instances to Ollama API message format.

    Args:
        messages: Queryset or list of Message instances (role U/A only)
        system_prompt (str | None): Runtime system prompt — always first
        summary (str | None): Persisted conversation summary — slots in
            immediately after system_prompt if provided

    Returns:
        list[dict]: Ollama-formatted message list

    History structure:
        [
            {"role": "system", "content": system_prompt},   # if provided
            {"role": "system", "content": "Summary: ..."},  # if summary provided
            {"role": "user",      "content": "..."},         # recent messages
            {"role": "assistant", "content": "..."},
            ...
        ]
    """

    role_mapping = {
        "U": "user",
        "A": "assistant",
    }

    ollama_messages = []

    if system_prompt:
        ollama_messages.append({"role": "system", "content": system_prompt})

    if summary:
        ollama_messages.append(
            {
                "role": "system",
                "content": f"Summary of earlier conversation:\n{summary}",
            }
        )

    for message in messages:
        # Skip system messages — summary is handled above explicitly
        if message.role not in role_mapping:
            continue
        ollama_role = role_mapping[message.role]
        ollama_messages.append({"role": ollama_role, "content": message.content})

    return ollama_messages


def extract_html(text: str) -> Optional[str]:
    """
    Extracts HTML content from LLM output.
    Handles cases:
    - HTML inside ```html ... ``` code blocks
    - HTML inside ``` ... ``` code blocks (no language tag)
    - Raw HTML with no code block
    - Mixed content with markdown + HTML
    - Blank / no HTML content

    Returns the extracted HTML string, or None if no HTML found.
    """

    if not text or not text.strip():
        return None

    # 1. Try ```html ... ``` block first (most explicit)
    match = re.search(r"```html\s*([\s\S]*?)```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # 2. Try generic ``` ... ``` block containing HTML
    for block_match in re.finditer(r"```\w*\s*([\s\S]*?)```", text):
        block_content = block_match.group(1).strip()
        if re.search(r"<(!DOCTYPE\s+html|html[\s>])", block_content, re.IGNORECASE):
            return block_content

    # 3. Try raw HTML in the text (no code block)
    # Look for <!DOCTYPE html> or <html> as the broadest starting anchor
    match = re.search(r"(<!DOCTYPE\s+html[\s\S]*?</html>)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    match = re.search(r"(<html[\s\S]*?</html>)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # 4. Partial HTML fallback: grab from the first < to the last >
    # Only do this if there are actual HTML-looking tags
    if re.search(r"<[a-zA-Z][^>]*>", text):
        first = text.index("<")
        last = text.rindex(">") + 1
        return text[first:last].strip()

    return None


def get_fernet():
    """Derive a valid Fernet key from Django's SECRET_KEY."""

    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    encoded_key = base64.urlsafe_b64encode(key)  # Fernet expects base64
    return Fernet(encoded_key)


def encrypt(plaintext: str) -> str:
    """Encrypt the given plaintext"""

    f = get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt the given ciphertext"""

    f = get_fernet()
    return f.decrypt(ciphertext.encode()).decode()


def get_token_hash(token: str) -> str:
    """Generate hex digest for the given token"""

    return hashlib.sha256(token.encode()).hexdigest()
