import base64
import hashlib
import io
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Optional

import requests
from cryptography.fernet import Fernet
from weasyprint import HTML

logger = logging.getLogger(__name__)


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

    pdf_buffer = io.BytesIO()

    try:
        HTML(string=html_content).write_pdf(target=pdf_buffer)
    except Exception as e:
        logger.exception(f"WeasyPrint PDF generation failed: {e}")
        raise

    pdf_buffer.seek(0)
    return pdf_buffer.read()


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


def send_document(chat_id: str, token: str, file_bytes: bytes) -> dict:
    """
    Send a document (file) to the chat.
    Sends binary file content as a Telegram document.

    Args:
        chat_id (str): Telegram ChatID
        token (str): Telegram bot token
        file_bytes (bytes): File content as bytes

    Returns:
        dict: API response with sent document details
            - message_id: Unique message identifier
            - document: Document information (file_id, file_size, etc.)
            - caption: Caption text sent

    Example - Send PDF report:
        >>> pdf_bytes = generate_pdf(html_content)
        >>> client.send_document(
        ...     file_bytes=pdf_bytes,
        ...     filename="report-2024-04.pdf",
        ...     caption="📄 Your monthly report"
        ... )
    """

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    filename = f"report-{chat_id}-{datetime.now().isoformat()}.pdf"
    caption = "📄 Your report is ready!"

    response = requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption},
        files={"document": (filename, file_bytes, "application/octet-stream")},
    )

    if response.status_code != 200:
        raise Exception(
            f"Telegram 'sendDocument' API error: Status code - {response.status_code}"
        )

    data = response.json()
    if not data.get("ok"):
        raise Exception(f"Failed to send document: {data}")

    return data["result"]


def decrypt_token(ciphertext: str) -> str:
    """Derive a valid Fernet key and decrypt the given ciphertext"""

    secret_key: Optional[str] = os.getenv("SECRET_KEY")
    if not secret_key:
        raise EnvironmentError("SECRET_KEY is required for decrypting the token")

    key = hashlib.sha256(secret_key.encode()).digest()
    encoded_key = base64.urlsafe_b64encode(key)  # Fernet expects base64
    fernet = Fernet(encoded_key)

    return fernet.decrypt(ciphertext.encode()).decode()


def _parse_uuid(value: str, label: str) -> uuid.UUID | str:
    """
    Return a UUID object or an error string.

    Args:
        value (str): The string to parse as a UUID.
        label (str): The name of the field (for error reporting).

    Returns:
        uuid.UUID: Parsed UUID if successful.
        str: Markdown error message if parsing fails.
    """

    try:
        return uuid.UUID(value)
    except ValueError:
        return f"## Error\n\n`{label}` is not a valid UUID: `{value}`"
