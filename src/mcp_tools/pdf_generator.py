"""PDF Generator MCP Server Module.

Which primarily does:
    - HTML to PDF conversion using WeasyPrint.
    - Direct Telegram document delivery.

Example Usage:
    # User asks: "Can you provide me a detail document regarding 'X'"
    # LLM calls: generate_and_send_report("...")
"""

from clients.telegram import TelegramClient
from strings import PDF_GENERATION_SEND_SUCCESS
from utils.pdf import (
    extract_html,
    generate_pdf,
)


def generate_and_send_report(client: TelegramClient, contents: str) -> str:
    """Generate a PDF report from HTML content and send it via Telegram.

    This is the primary entry point for the PDF generator service. It orchestrates
    the complete workflow: HTML validation, PDF generation and Telegram delivery.

    Args:
        client (TelegramClient): TelegramClient instance.
        contents (str): HTML markup to be converted to PDF. Must contain valid HTML
                       structure; plain text will be rejected.

    Returns:
        str: Success message on completion, or error description on failure.

    Process Flow:
        1. Validates bot_id as a valid UUID format
        2. Extracts and validates HTML content
        3. Generates PDF from HTML using weasyprint
        4. Sends the PDF document to the user via Telegram
        5. Returns status message

    Raises:
        Logs exceptions via logger but does not raise; returns error string instead
    """
    html_contents = extract_html(contents)
    if not html_contents:
        return contents

    pdf_bytes = generate_pdf(html_contents)

    try:
        client.send_document(file_bytes=pdf_bytes)
        return PDF_GENERATION_SEND_SUCCESS
    except Exception as e:
        return f"## Error caught while sending PDF: {str(e)}"
