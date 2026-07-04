"""Helper utilities for PDF Generator."""

import io
import logging
import re
from typing import Optional

from weasyprint import HTML

logger = logging.getLogger(__name__)


def generate_pdf(html_content: str) -> bytes:
    """Convert HTML content to PDF bytes using WeasyPrint.

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
    """Extract HTML content from LLM output.

    Handles cases:
    - HTML inside ```html ... ``` code blocks
    - HTML inside ``` ... ``` code blocks (no language tag)
    - Raw HTML with no code block
    - Mixed content with markdown + HTML
    - Blank / no HTML content.

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
