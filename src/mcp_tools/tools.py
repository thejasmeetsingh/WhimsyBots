"""MCP tool registry and OpenAI-compatible tool schemas.

This module centralizes the tool definitions exposed to the model. It maps
function names to their callables in 'mcp_tools' and declares the JSON
schemas used to advertise each tool to the LLM.

Tool Groups:
- 'CRON_JOB_TOOLS' — List, create, update, and delete bot cron jobs.
- 'WEB_SEARCH_TOOLS' — DuckDuckGo search and page content extraction.
- 'PDF_GENERATOR_TOOLS' — HTML-to-PDF generation with Telegram delivery.
"""

from typing import Callable

from mcp_tools import cron_job, pdf_generator, web_search

LIST_CRONS = "list_cron_jobs"
CREATE_CRON = "create_cron_job"
UPDATE_CRON = "update_cron_job"
DELETE_CRON = "delete_cron_job"
WEB_SEARCH = "web_search"
FETCH_AND_EXTRACT = "fetch_and_extract"
GENERATE_PDF = "generate_and_send_report"

FUNCTION_NAME_TO_CALLABLE_MAP: dict[str, Callable] = {
    LIST_CRONS: cron_job.list,
    CREATE_CRON: cron_job.create,
    UPDATE_CRON: cron_job.update,
    DELETE_CRON: cron_job.delete,
    WEB_SEARCH: web_search.web_search,
    FETCH_AND_EXTRACT: web_search.fetch_and_extract,
    GENERATE_PDF: pdf_generator.generate_and_send_report,
}

CRON_JOB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": LIST_CRONS,
            "description": "Retrieves all cron jobs associated with a bot, optionally filtered by active status. Before performing any 'update' or 'delete' operations, Fetch the cron job list to get the accurate cron job `id`",
            "parameters": {
                "type": "object",
                "properties": {
                    "is_active": {
                        "type": "boolean",
                        "description": "Filter by active status:"
                        "- True → Only active jobs."
                        "- False → Only inactive jobs",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": CREATE_CRON,
            "description": "Create a cron job",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Human-readable label for the job (max 100 chars) Examples: 'Daily Report', 'Weekly Newsletter', 'Hourly Check'. Ensure the it contain no mention of the schedule or that its a cron job.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed description of what the job does (max 5000 chars) Examples: 'Generates and sends daily performance report', 'Fetches latest data from API'. Ensure the it contain no mention of the schedule or that its a cron job.",
                    },
                    "cron_expression": {
                        "type": "string",
                        "description": "Use standard 5-field cron expressions (minute hour day month weekday) Examples:"
                        "- daily 9 AM → '0 9 * * *'"
                        "- weekdays → '0 9 * * 1-5'"
                        "- weekly Sunday → '0 10 * * 0'",
                    },
                },
                "required": ["name", "description", "cron_expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": UPDATE_CRON,
            "description": "Updates one or more fields of an existing cron job. Only the provided fields will be updated; other fields remain unchanged.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "ID of the cron job to update in UUID format",
                    },
                    "name": {
                        "type": "string",
                        "description": "(Optional) Human-readable label for the job (max 100 chars) Examples: 'Daily Report', 'Weekly Newsletter', 'Hourly Check'. Ensure the it contain no mention of the schedule or that its a cron job.",
                    },
                    "description": {
                        "type": "string",
                        "description": "(Optional) Detailed description of what the job does (max 5000 chars) Examples: 'Generates and sends daily performance report', 'Fetches latest data from API'. Ensure the it contain no mention of the schedule or that its a cron job.",
                    },
                    "cron_expression": {
                        "type": "string",
                        "description": "(Optional) Use standard 5-field cron expressions (minute hour day month weekday) Examples:"
                        "- daily 9 AM → '0 9 * * *'"
                        "- weekdays → '0 9 * * 1-5'"
                        "- weekly Sunday → '0 10 * * 0'",
                    },
                    "is_active": {
                        "type": "boolean",
                        "description": "(Optional) Enable or disable the job",
                    },
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": DELETE_CRON,
            "description": "Permanently removes a cron job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "ID of the cron job to update in UUID format",
                    },
                },
                "required": ["id"],
            },
        },
    },
]

WEB_SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": WEB_SEARCH,
            "description": "Search the web via DuckDuckGo, Does NOT fetch full page content — use 'fetch_and_extract' on a specific URL from these results if you need more detail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": FETCH_AND_EXTRACT,
            "description": "Fetch a specific URL and extract its main readable content as markdown. Use this after 'web_search' when a result's snippet looks worth reading in full",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to extract content from",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

PDF_GENERATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": GENERATE_PDF,
            "description": "Generate a PDF report from HTML content and send it via Telegram.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contents": {
                        "type": "string",
                        "description": "HTML markup to be converted to PDF. Must contain valid HTML structure; plain text will be rejected."
                        "- No markdown and no text outside the HTML."
                        "- All CSS must be inline or in a single `<style>` block in `<head>`. No external stylesheets."
                        "- No JavaScript — WeasyPrint does not execute scripts."
                        "- No flexbox or CSS Grid — use block elements and tables for layout."
                        "- No emojis — they will not render."
                        "- Use inline SVG for charts and graphs — no JS charting libraries."
                        "- A4 width (210mm), minimum 15mm margins on all sides.",
                    },
                },
                "required": ["contents"],
            },
        },
    },
]
