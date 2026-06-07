"""
PDF Generator MCP Server Module

Key Features:
    - HTML to PDF conversion using weasyprint
    - Secure token handling with decryption
    - Direct Telegram document delivery
    - Comprehensive error handling and logging
    - UUID validation for bot identification

Example Usage:
    The server exposes the `generate_and_send_report` tool which can be invoked
    to create and send a PDF report to a specific bot's associated Telegram user.
"""

import logging

from mcp.server.fastmcp import FastMCP
from sqlalchemy import column, select, table

from pdf_generator.db import get_session
from pdf_generator.helpers import (
    _parse_uuid,
    decrypt_token,
    extract_html,
    generate_pdf,
    send_document,
)

logger = logging.getLogger(__name__)
mcp = FastMCP(name="pdf-generator")


# Lightweight SQLAlchemy table/column references for database queries
# Represents the "app_bot" table with essential columns for bot data retrieval
bots = table(
    "app_bot",
    column("id"),
    column("telegram_bot_token"),
    column("telegram_chat_id"),
)


@mcp.tool()
async def generate_and_send_report(bot_id: str, contents: str) -> str:
    """
    Generate a PDF report from HTML content and send it via Telegram.

    This is the primary entry point for the PDF generator service. It orchestrates
    the complete workflow: HTML validation, PDF generation, bot credential retrieval,
    token decryption, and Telegram delivery.

    Args:
        bot_id (str): UUID identifier of the bot to receive the report.
        contents (str): HTML markup to be converted to PDF. Must contain valid HTML
                       structure; plain text will be rejected.

    Returns:
        str: Success message on completion, or error description on failure.
            - On success: "PDF generated and sent to the user successfully ✅"
            - On error: Error message describing what went wrong

    Process Flow:
        1. Validates bot_id as a valid UUID format
        2. Extracts and validates HTML content
        3. Generates PDF from HTML using weasyprint
        4. Queries database for bot credentials and Telegram chat ID
        5. Decrypts the bot's Telegram token
        6. Sends the PDF document to the user via Telegram
        7. Returns status message

    Raises:
        Logs exceptions via logger but does not raise; returns error string instead
    """

    logger.info(
        {
            "tool": "generate_and_send_report",
            "params": {"bot_id": bot_id, "contents_length": len(contents)},
        }
    )

    bot_uuid = _parse_uuid(bot_id, "bot_id")
    if isinstance(bot_uuid, str):
        logger.error("Invalid 'bot_uuid' format")
        return bot_uuid

    html_contents = extract_html(contents)
    if not html_contents:
        logger.error("Provided contents does not contain a valid HTML")
        return contents

    pdf_bytes = generate_pdf(html_contents)
    logger.info(
        {"tool": "generate_and_send_report", "msg": "Report generated successfully"}
    )

    try:
        async with get_session() as session:
            stmt = select(bots).where(bots.c.id == bot_uuid)
            result = await session.execute(stmt)
            row = result.fetchone()

            if row is None:
                logger.error("Bot record with the given 'bot_id' does not exists")
                return bot_uuid

            bot_data = row._asdict()
            token = decrypt_token(bot_data["telegram_bot_token"])

            send_document(
                chat_id=bot_data["telegram_chat_id"], token=token, file_bytes=pdf_bytes
            )

        return "PDF generated and sent to the user successfully ✅"
    except Exception as e:
        logger.error(str(e), exc_info=True)
        return f"Error caught: {str(e)}"
