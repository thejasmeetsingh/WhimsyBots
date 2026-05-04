"""MCP Server configuration for Cron Job management"""

from mcp.server.fastmcp import FastMCP


server = FastMCP(
    name="cron-job-manager",
    description="Async MCP server to list, create, update, and delete cron jobs in the database.",
    tools=[
        {
            "name": "list_cron_jobs",
            "description": "List cron jobs for a specific bot.",
            "parameters": {
                "bot_id": {
                    "type": "string",
                    "description": "UUID of the bot whose cron jobs to retrieve.",
                    "required": True,
                },
                "is_active": {
                    "type": "boolean",
                    "description": "Filter by active status. true = active only, false = inactive only, omit = all.",
                    "required": False,
                },
            },
        },
        {
            "name": "create_cron_job",
            "description": "Create a new cron job for a bot.",
            "parameters": {
                "bot_id": {
                    "type": "string",
                    "description": "UUID of the bot this job belongs to.",
                    "required": True,
                },
                "name": {
                    "type": "string",
                    "description": "Human-readable label for the job (max 100 chars).",
                    "required": True,
                },
                "cron_expression": {
                    "type": "string",
                    "description": "Standard 5-field cron expression (e.g. '0 9 * * 1').",
                    "required": True,
                },
            },
        },
        {
            "name": "update_cron_job",
            "description": "Partially update an existing cron job. Only provided fields are updated.",
            "parameters": {
                "id": {
                    "type": "string",
                    "description": "UUID of the cron job to update.",
                    "required": True,
                },
                "bot_id": {
                    "type": "string",
                    "description": "UUID of the owning bot, used to scope the lookup.",
                    "required": True,
                },
                "name": {
                    "type": "string",
                    "description": "New name for the job.",
                    "required": False,
                },
                "cron_expression": {
                    "type": "string",
                    "description": "New standard 5-field cron expression.",
                    "required": False,
                },
                "is_active": {
                    "type": "boolean",
                    "description": "Enable or disable the job.",
                    "required": False,
                },
            },
        },
        {
            "name": "delete_cron_job",
            "description": "Delete a cron job scoped to a specific bot.",
            "parameters": {
                "id": {
                    "type": "string",
                    "description": "UUID of the cron job to delete.",
                    "required": True,
                },
                "bot_id": {
                    "type": "string",
                    "description": "UUID of the owning bot, used to scope the deletion.",
                    "required": True,
                },
            },
        },
    ],
)
