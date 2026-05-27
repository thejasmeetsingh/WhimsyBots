"""Entry point for the Cron Job MCP server"""

import asyncio
import logging

from cron_job.server import mcp

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logging.info("Starting CronJob MCP server...")
    asyncio.run(mcp.run_stdio_async())
