"""
Utility functions for WhimsyBots application.

This module provides helper functions for:
- Scheduling and time calculations
- Message formatting and parsing
- PDF generation from HTML content
- Admin interface helpers
- Message format conversions
"""

import logging
from io import BytesIO
from typing import Optional, Dict, List

from weasyprint import HTML
from croniter import croniter
from django.utils import timezone


logger = logging.getLogger(__name__)


def calculate_next_run_at(interval_mins: Optional[int], cron_expression: Optional[str]) -> timezone.datetime:
    """
    Calculate the next scheduled run time for a bot.
    
    Supports two scheduling modes:
    1. Interval-based: Run every X minutes
    2. Cron-based: Run according to cron expression
    
    Args:
        interval_mins (int | None): Minutes between runs (takes precedence if both provided)
        cron_expression (str | None): Standard cron expression for scheduling
        
    Returns:
        datetime: Next run timestamp
        
    Example:
        >>> next_run = calculate_next_run_at(interval_mins=30, cron_expression=None)
        >>> next_run = calculate_next_run_at(interval_mins=None, cron_expression="0 9 * * *")
    """

    current_dt = timezone.now()

    if interval_mins:
        return current_dt + timezone.timedelta(minutes=interval_mins)

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
    Split a long message into chunks respecting a size limit.
    
    Useful for Telegram API which has a message length limit (default 4096 chars).
    Splits at the boundary without breaking mid-character.
    
    Args:
        text (str): Text to split
        limit (int): Maximum characters per chunk (default: 4096 for Telegram)
        
    Returns:
        list[str]: List of text chunks, each <= limit characters
        
    Example:
        >>> very_long_text = "x" * 10000
        >>> chunks = split_message(very_long_text)
        >>> len(chunks)
        3
        >>> all(len(chunk) <= 4096 for chunk in chunks)
        True
    """

    if len(text) <= limit:
        return [text]

    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]

    return chunks


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
    messages,
    system_prompt: Optional[str] = None
) -> List[Dict[str, str]]:
    """
    Convert Message model instances to Ollama API message format.
    
    Transforms the internal message representation to the format expected
    by the Ollama chat API, including optional system prompt.
    
    Args:
        messages: Queryset or list of Message model instances
        system_prompt (str | None): Optional system prompt prepended to messages
        
    Returns:
        list[dict]: List of message dicts with 'role' and 'content' keys
        
    Message format:
        {
            "role": "system" | "user" | "assistant",
            "content": "message text"
        }
        
    Example:
        >>> from app.models import Message, Bot
        >>> bot = Bot.objects.first()
        >>> messages = bot.messages.all()
        >>> ollama_format = convert_messages_to_ollama_format(
        ...     messages,
        ...     system_prompt="You are a helpful assistant."
        ... )
        >>> # Now can be passed to OllamaClient.chat(messages=ollama_format)
        
    Note:
        - "U" role becomes "user"
        - "A" role becomes "assistant"
        - System prompt is always first (if provided)
    """

    # Mapping from Message model role codes to Ollama role names
    role_mapping = {
        "U": "user",
        "A": "assistant",
    }

    ollama_messages = []

    # Add system prompt first if provided
    if system_prompt:
        ollama_messages.append({
            "role": "system",
            "content": system_prompt
        })

    # Add all messages in conversation order
    for message in messages:
        ollama_role = role_mapping.get(message.role, message.role.lower())
        ollama_messages.append({
            "role": ollama_role,
            "content": message.content
        })

    return ollama_messages
