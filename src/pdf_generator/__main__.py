"""Entry point for the PDF generator MCP server."""

import asyncio
import logging

from pdf_generator.server import mcp

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logging.info("Starting PDF generator MCP server...")
    asyncio.run(mcp.run_stdio_async())
