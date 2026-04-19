import logging
from io import BytesIO

from weasyprint import HTML
from croniter import croniter
from django.utils import timezone


logger = logging.getLogger(__name__)


def calculate_next_run_at(interval_mins, cron_expression):
    current_dt = timezone.now()

    if interval_mins:
        return current_dt + timezone.timedelta(minutes=interval_mins)

    return croniter(cron_expression, current_dt).get_next(timezone.datetime)


def get_admin_link(model, value, lookup, obj):
    if not obj or not value:
        return value    
    return f"<a href='/admin/app/{model}/?{lookup}={str(obj.id)}' target='_blank'>{value}</a>"


def split_message(text: str, limit: int = 4096) -> list[str]:
    if len(text) <= limit:
        return [text]
    
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    
    return chunks


def parse_telegram_update(update: dict) -> dict | None:
    msg = update.get("message")
    if not msg:
        return None
    
    return {
        "update_id":  update["update_id"],
        "chat_id":    str(msg["chat"]["id"]),
        "text":       msg.get("text", "").strip(),
        "username":   msg.get("from", {}).get("username", ""),
        "first_name": msg.get("from", {}).get("first_name", ""),
        "date":       msg.get("date"),
    }


def generate_pdf(html_content: str) -> bytes:
    pdf_buffer = BytesIO()

    try:
        HTML(string=html_content).write_pdf(target=pdf_buffer)
    except Exception as e:
        logger.exception(f"WeasyPrint PDF generation failed: {e}")
        raise

    pdf_buffer.seek(0)
    return pdf_buffer.read()


def convert_messages_to_ollama_format(messages, system_prompt=None):
    """
    Convert a list or queryset of Message model objects to Ollama-supported message format.
    
    Args:
        messages: List or queryset of Message model objects
        system_prompt: Optional system prompt
        
    Returns:
        List of dictionaries with 'role' and 'content' keys in Ollama format.
        Example: [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi there!"}]
    """

    # Mapping from Message model role codes to Ollama role names
    role_mapping = {
        "U": "user",
        "A": "assistant",
    }
    
    ollama_messages = []

    if system_prompt:
        ollama_messages.append({
            "role": "system",
            "content": system_prompt
        })
    
    for message in messages:
        ollama_role = role_mapping.get(message.role, message.role.lower())
        ollama_messages.append({
            "role": ollama_role,
            "content": message.content
        })
    
    return ollama_messages
