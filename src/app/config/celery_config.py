"""Celery configuration and constants"""


class CeleryConfig:
    """Configuration and constants for Celery tasks"""

    # Error messages
    ERROR_MESSAGES = {
        "NO_OLLAMA": "No Ollama configuration found",
        "BOT_NOT_FOUND": "Bot not found with id: {bot_id}",
        "MESSAGE_NOT_FOUND": "Message not found with id: {msg_id}",
        "TOOL_EXECUTION_FAILED": "Tool execution failed",
        "INTENT_CLASSIFICATION_FAILED": "Failed to classify message intent",
    }

    # Telegram messages
    TELEGRAM_MESSAGES = {
        "REPORT_GENERATING": "📊 Generating your report, one moment...",
        "REPORT_READY": "📄 Your {bot_name} report is ready!",
    }

    # Prompts
    INTENT_CLASSIFICATION_PROMPT = """
Classify this message into one of these intents:
- J: user is writing a journal entry or responding to a prompt
- R: user wants a summary, report, or overview
- Q: user is asking a specific question
- O: anything else

Message: "{message}"
Reply with ONLY the intent label, nothing else.
"""

    REPORT_GENERATION_PROMPT = """
You are a report generator. Based on the conversation history,
generate a well-structured HTML report with inline CSS styling.
Include sections for summary, key insights, charts, graphs if needed and any patterns observed.
"""
