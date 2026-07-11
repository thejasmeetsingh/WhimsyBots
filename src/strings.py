"""User-facing and log-facing string templates used across the app.

Constants are kept here (rather than inlined) so messages stay
consistent across log lines, Telegram replies, and operator dashboards.
Each entry uses '.format(...)' placeholders rather than f-strings so
callers can substitute values at the call site.
"""

NO_OLLAMA = "No Ollama configuration found"
OBJ_NOT_FOUND = "{obj_type} not found with id: '{obj_id}'"
TOOL_EXECUTION_FAILED = "Tool execution failed"
INVALID_TOOL = "Invalid tool '{tool}'"
INTENT_CLASSIFICATION_FAILED = "Failed to classify message intent"
REPORT_GENERATING = "📊 Generating your report, one moment..."
REPORT_READY = "📄 Your report is ready!"
UNIQUE_TELEGRAM_TOKEN_ERROR = "A bot with this token already exists"
NO_BOT_RESPONSE = "No response returned by the bot '{bot_name}'"
SUMMARY_UNAVAILABLE = "Summary is not available, Please ignore."
INVALID_BOT_TOKEN = "Bot with token '{bot_token}' not found."
WEBHOOK_SETUP_SUCCESS = "Webhook setup successful for bot '{bot_name}'"
BOT_CRON_JOB_SUCCESS = (
    "Cron job processing completed for bot '{bot_name}'\nOllama Response Duration: {duration}ms"
)
BOT_MSG_SUCCESS = (
    "Message processed successfully for bot '{bot_name}'\nOllama Response Duration: {duration}ms"
)
TELEGRAM_RATE_LIMIT_ERROR = (
    "Telegram rate limit error in '{func_name}' for bot '{bot_name}'\n\nERROR: {error}"
)
GENERAL_TASK_ERROR = "Error caught in '{func_name}' for bot '{bot_name}'\n\nERROR: {error}"
SUMMARY_PROCESS_SUCCESS = "Summary management completed for {bots_len} bot(s)"
GENERATE_EMBEDDING_FAILED = "Embedding failed for {obj_type} with ID: '{obj_id}'\n\nERROR: {error}"
UPDATE_OBSERVED_PATTERNS_SUCCESS = "Patterns updated successfully for bot '{bot_name}'"
CRON_ACTION_SUCCESS = "## ✅ Cron Job {action} Successfully!"
CRON_DOES_NOT_EXISTS = "No Cron Job exists with the provided id: `{id}`"
PDF_GENERATION_SEND_SUCCESS = "PDF generated and sent to the user successfully ✅"
EMPTY_WEB_SEARCH = "No results found for query: `{query}`"
WEBPAGE_CONTENT_ERROR = (
    "Fetched {url} successfully, but couldn't extract readable article content "
    "(page may be JS-rendered, a PDF, or non-article content)."
)
