"""Entry point for the Cron Job MCP server"""

import asyncio

from cron_job.server import server


if __name__ == "__main__":
    asyncio.run(server.run())
