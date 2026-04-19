import asyncio
import logging

from django.utils import timezone
from weasyprint import HTML

from app.mcp import mcp_client
from app.models import Bot, Ollama, Message
from app.choices import MCPTransportType, MessageIntentType, MessageRole
from app.ollama import OllamaClient
from app.telegram import TelegramClient
from app.utils import calculate_next_run_at, parse_telegram_update, convert_messages_to_ollama_format, generate_pdf
from whimsybots.celery import task as celery


logger = logging.getLogger(__name__)


def handle_inbound_update(bot_id: str, update: dict):
    parsed = parse_telegram_update(update)
    if not parsed:
        return
    
    chat_id = parsed["chat_id"]
    text = parsed["text"]

    bot = Bot.objects.get(bot_id)

    if not bot.telegram_chat_id:
        bot.telegram_chat_id = str(chat_id)
        bot.save(update_fields=["telegram_chat_id"])

    message = Message.objects.create(
        bot=bot,
        role=MessageRole.USER.value[0],
        content=text,
    )

    TelegramClient(bot.telegram_bot_token, chat_id).send_typing_action()

    # Process inbound message
    process_inbound_message.apply_async(kwargs={"bot_id": bot_id, "msg_id": str(message.id)})


@celery.task()
def master_poller():
    current_dt = timezone.now()

    ollama = Ollama.objects.first()
    if not ollama:
        return "No ollama config found"
    
    due_bots = Bot.objects.filter(is_active=True, telegram_chat_id__isnull=False, next_run_at__lte=current_dt)

    logger.info(f"Found {due_bots.count()} bots - That are due for their execution")

    for bot in due_bots:
        process_inbound_message.apply_async(kwargs={"bot_id": str(bot.id)}, eta=bot.next_run_at)
        bot.next_run_at = calculate_next_run_at(bot.interval_mins, bot.cron_expression)
        bot.last_run_at = current_dt
    
    Bot.objects.bulk_update(due_bots, fields=["next_run_at", "last_run_at"])

    return "Processed due bots successfully"


@celery.task()
def process_inbound_message(bot_id, msg_id):
    ollama = Ollama.objects.first()
    if not ollama:
        return "No ollama config found"

    bot = Bot.objects.prefetch_related("messages", "mcp_servers").get(bot_id)

    ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
    telegram_client = TelegramClient(bot.telegram_bot_token, bot.telegram_chat_id)

    tools = []

    for svr in bot.mcp_servers.filter(is_active=True):
        if svr.transport == MCPTransportType.LOCAL.value[0]:
            config = {"command": svr.command, "args": svr.args, "env": svr.secrets}
        else:
            config = {"url": svr.endpoint, "headers": svr.secrets}
        
        _tools = asyncio.run(mcp_client(svr.transport, config))

        for _tool in _tools:
            tools.append({
                "tool": _tool,
                "config": config
            })

    model = bot.ollama_model if bot.ollama_model else ollama.default_model

    telegram_client.send_typing_action()
    
    if msg_id:
        message = Message.objects.get(msg_id)

        INTENT_PROMPT = """
        Classify this message into one of these intents:
        - J: user is writing a journal entry or responding to a prompt
        - R: user wants a summary, report, or overview
        - Q: user is asking a specific question
        - O: anything else

        Message: "{message}"
        Reply with ONLY the intent label, nothing else.
        """

        response = ollama_client.chat(
            model=model,
            messages=[{
                "role": MessageRole.USER.value[1].lower(),
                "content": INTENT_PROMPT.format(message=message.content)
            }],
            format={
                "type": "string",
                "properties": {"intent": {"type": "string"}},
                "required": ["intent"]
            },
        )

        intent = response["message"]
        message.intent = intent
        message.save(update_fields=["intent"])
    
        if intent == MessageIntentType.REPORT.value[0]:
            telegram_client.send_message("📊 Generating your report, one moment...")
            generate_report.apply_async(kwargs={"bot_id": bot_id})
            return

    history = convert_messages_to_ollama_format(bot.messages.all(), system_prompt=bot.system_prompt)

    while True:
        response = ollama_client.chat(
            model=model,
            messages=history,
            tools=list(map(lambda x: x["tool"], tools)),
            options={
                "temperature": ollama.temperature,
                "num_ctx": ollama.num_ctx,
                "num_predict": ollama.num_predict
            }
        )

        if not response["tools"]:
            break

        for tool_call in response["tools"]:
            for tool in tools:
                if tool_call["name"] == tool["tool"]["function"]["name"] and tool_call["description"] == tool["tool"]["function"]["description"]:
                    tool_response = asyncio.run(mcp_client(
                        MCPTransportType.REMOTE.value[0] if tool["tool"]["config"].get("url") else MCPTransportType.LOCAL.value[0],
                        tool["tool"]["config"]
                    ))

                    result = ""
                    
                    if not tool_response["isError"]:
                        for content in tool_response["content"]:
                            result += content["text"]
                            result += "\n\n"
                    else:
                        result = "Tool didn't executed successfully"
                    
                    history.append({
                        "role": "tool",
                        "content": result,
                        "tool_calls": [tool["tool"]["function"]]
                    })
                    break

    reply = response["message"]

    telegram_client.send_message(reply)

    Message.objects.create(
        bot=bot,
        role=MessageRole.ASSISTANT.value[0],
        content=reply
    )

    return "Processed inbound message successfully"


@celery.task()
def generate_report(bot_id):
    ollama = Ollama.objects.first()
    if not ollama:
        return "No ollama config found"
    
    bot = Bot.objects.prefetch_related("messages").get(bot_id)

    ollama_client = OllamaClient(ollama.endpoint, api_key=ollama.api_key)
    telegram_client = TelegramClient(bot.telegram_bot_token, bot.telegram_chat_id)

    telegram_client.send_typing_action()

    model = bot.ollama_model if bot.ollama_model else ollama.default_model
    
    system_prompt = """
    You are a report generator. Based on the conversation history,
    generate a well-structured HTML report with inline CSS styling.
    Include sections for summary, key insights, charts, graphs if needed and any patterns observed.
    """

    history = convert_messages_to_ollama_format(messages=bot.messages.all(), system_prompt=system_prompt)

    response = ollama_client.chat(
        model=model,
        messages=history,
        options={
            "temperature": ollama.temperature,
            "num_ctx": ollama.num_ctx,
            "num_predict": ollama.num_predict
        }
    )

    pdf_bytes = generate_pdf(response["message"])

    current_dt = timezone.now()

    filename = f"report-{bot.name}-{current_dt}.pdf"

    telegram_client.send_document(
        file_bytes=pdf_bytes,
        filename=filename,
        caption=f"📄 Your {bot.name} report is ready!"
    )

    return f"Report generated successfully for bot: {bot.name} and sent to the user"
