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


# Lightweight table/column references
bots = table(
    "app_bot",
    column("id"),
    column("telegram_bot_token"),
    column("telegram_chat_id"),
)


@mcp.tool()
async def generate_and_send_report(bot_id: str, contents: str) -> str:
    """
    Generates a PDF report and send it to the user.

    PDF report is generated using the weasyprint library.
    The contents of the PDF must be a valid HTML.

    Args:
        bot_id (str): UUID of the bot
        contents (str): PDF contents in HTML

    Returns:
        str: Depicting success or an error message.
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
        return f"Error caught: {str(e)}"
