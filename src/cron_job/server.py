"""MCP Server configuration for Cron Job management"""

from mcp.server.fastmcp import FastMCP


server = FastMCP(
    name="cron-job-manager",
    instructions="Async MCP server to list, create, update, and delete cron jobs in the database.",
)
