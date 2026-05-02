# Architecture & Data Models v1.0

## 1. What We're Building

A self-hosted, Django-based AI agent platform where users can create and configure AI-powered "apps" (e.g. a Journaling Agent, a Research Assistant, a Daily Briefing bot) through an admin panel. Each app runs on a schedule, communicates with users via Telegram, processes replies using a local LLM (Ollama), and can generate rich PDF reports on demand.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Django (Core)                        │
│  Admin Panel  │  REST API  │  Models  │  Business Logic  │
└───────────────────────────┬─────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
   ┌──────▼──────┐  ┌───────▼──────┐  ┌──────▼──────┐
   │   Celery     │  │   Ollama     │  │  Telegram    │
   │   Workers    │  │  (Local LLM) │  │   Bot API    │
   │  + Beat DB   │  │             │  │              │
   └──────┬──────┘  └─────────────┘  └──────┬──────┘
          │                                  │
   ┌──────▼──────┐                   ┌───────▼──────┐
   │    Redis     │                  │  Telegram    │
   │   (Broker)   │                  │  Polling     │
   └─────────────┘                   └─────────────┘
```

### Request Flows

#### Outbound (Scheduled App Run)
```
Celery Beat (every minute)
  → Master poller task checks all Bot objects
  → Bot.next_run_at <= now? → Trigger bot_runner task(bot_id)
  → bot_runner builds context from Message history
  → Calls Ollama → gets response
  → Sends message via Telegram API (TelegramClient.send_message)
  → Stores Message to DB
  → Updates Bot.next_run_at
```

#### Inbound (User Telegram Message)
```
User sends message via Telegram
  → Celery task polls Telegram API (getUpdates)
  → Extracts message text and chat_id
  → Matches chat_id to Bot + User
  → Stores inbound Message to DB
  → Checks intent: is this a report request?
    → YES: LLM generates HTML → WeasyPrint → PDF → Telegram sendDocument
    → NO: Normal reply processing → Ollama → Telegram sendMessage response
  → Updates message status to SENT/DELIVERED
```

#### Report Generation
```
Inbound message detected as report/summary request
  → LLM confirms intent
  → LLM generates full HTML report with inline CSS
  → WeasyPrint converts HTML → PDF (in memory)
  → Telegram sendDocument sends PDF as file attachment
  → Telegram sendMessage confirmation sent: "Your report has been generated and sent above"
```

---

## 3. Django App Structure

```
/platform
  /app                   ← Bot model, MCPServer, Message, Log, Ollama
  /user                  ← Extended user model, permissions
  /core (future)         ← shared utilities, base classes
  /agents (future)       ← Agent logic, Ollama client, intent detection
  /messaging (future)    ← Telegram send/receive abstraction
  /conversations (future) ← Conversation history models
  /reports (future)      ← PDF generation, HTML templates
  /scheduler (future)    ← Celery tasks, master poller, bot runner
  manage.py
  celery.py
  settings.py
```

---

## 4. Data Models

### 4.1 `user` app

#### `User` (extends AbstractUser)
```python
class User(AbstractUser):
    email           = models.EmailField(unique=True)
    timezone        = models.CharField(max_length=50, default='UTC')
    created_at      = models.DateTimeField(auto_now_add=True)
```

---

### 4.2 `app` app

#### `Bot`
The central model. Every agent/bot the user creates is a Bot instance.

```python
class Bot(models.Model):
    created_by      = models.ForeignKey(User, on_delete=models.CASCADE)
    name            = models.CharField(max_length=200)
    description     = models.TextField(null=True, blank=True)
    is_active       = models.BooleanField(default=True)

    # Scheduling
    interval_mins   = models.PositiveIntegerField(null=True, blank=True, help_text="Runs every X minutes")
    cron_expression = models.CharField(max_length=100, null=True, blank=True, help_text="Scheduling in cron format")
    next_run_at     = models.DateTimeField(null=True, blank=True)
    last_run_at     = models.DateTimeField(null=True, blank=True)

    # LLM config
    ollama_model    = models.CharField(max_length=50, null=True, blank=True)
    system_prompt   = MartorField(null=True, blank=True)  # Markdown support

    # Telegram Communication
    telegram_bot_token = models.CharField(max_length=255, unique=True)

    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)
```

#### `MCPServer`
MCP servers connected to a bot for extended functionality.

```python
class MCPServer(models.Model):
    bot         = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='mcp_servers')
    name        = models.CharField(max_length=100)
    transport   = models.CharField(max_length=1, choices=MCPTransportType.get_values())
    command     = models.CharField(max_length=10, null=True, blank=True, help_text="Command: python, npx, uv")
    endpoint    = models.URLField(null=True, blank=True, help_text="Remote MCP server endpoint URL")
    args        = ArrayField(base_field=models.CharField(max_length=500), default=list, null=True, blank=True)
    secrets     = models.JSONField(default=dict, null=True, blank=True, help_text="Env vars or HTTP headers")
    is_active   = models.BooleanField(default=True)
```



---

### 4.3 `app` app (continued)

#### `Message`
Every message in a conversation — inbound and outbound.

```python
class Message(models.Model):

    class Role(models.TextChoices):
        SYSTEM    = 'system'
        USER      = 'user'           # inbound from the human
        ASSISTANT = 'assistant'      # outbound from the LLM

    class Channel(models.TextChoices):
        TELEGRAM = 'telegram'

    class IntentType(models.TextChoices):
        JOURNAL_ENTRY    = 'journal_entry'
        REPORT_REQUEST   = 'report_request'
        COMMAND          = 'command'
        QUESTION         = 'question'
        OTHER            = 'other'

    class Status(models.TextChoices):
        PENDING   = 'pending'
        SENT      = 'sent'
        DELIVERED = 'delivered'
        FAILED    = 'failed'
        RECEIVED  = 'received'

    bot             = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='messages')
    role            = models.CharField(choices=Role.get_values())
    intent          = models.CharField(choices=IntentType.get_values())
    content         = MartorField()  # Markdown support
    channel         = models.CharField(choices=Channel.get_values())
    status          = models.CharField(choices=Status.get_values(), default=Status.PENDING.value[0])
    is_report_request = models.BooleanField(default=False)  # flagged by intent detection
    created_at      = models.DateTimeField(auto_now_add=True)
```

---

### 4.4 `app` app (continued)

#### `Ollama`
Ollama LLM configuration.

```python
class Ollama(models.Model):
    endpoint        = models.URLField(default="http://localhost:11434")
    default_model   = models.CharField(max_length=50, null=True, blank=True)
    api_key         = models.CharField(max_length=100, null=True, blank=True)
    temperature     = models.FloatField(default=0.7, help_text="Controls randomness in generation")
    num_ctx         = models.PositiveIntegerField(default=4096, help_text="Context length in tokens")
    num_predict     = models.IntegerField(default=-1, help_text="Max tokens to generate (-1 = infinite)")
```

#### `Log`
Audit trail for bot executions.

```python
class Log(models.Model):
    bot             = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='bot_logs')
    is_success      = models.BooleanField(default=True)
    error           = models.TextField(null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
```

---

## 5. Celery Architecture

### 5.1 Master Poller Task
Runs every minute via Celery Beat.

```python
@shared_task
def cron_job_poller():
    now = timezone.now()
    due_bots = Bot.objects.filter(
        is_active=True,
        next_run_at__lte=now
    ).select_related('created_by')

    for bot in due_bots:
        bot_runner.delay(bot.id)
        bot.next_run_at = calculate_next_run(bot)
        bot.last_run_at = now
        bot.save(update_fields=['next_run_at', 'last_run_at'])
```

### 5.2 Bot Runner Task
One task per bot execution. Handles the full outbound cycle.

```python
@shared_task
def bot_runner(bot_id):
    bot = Bot.objects.get(id=bot_id)
    telegram_client = TelegramClient(token=bot.telegram_bot_token, chat_id=None)

    # 1. Load bot + message history
    # 2. Build LLM context (system prompt + recent messages)
    # 3. Call Ollama → get response
    # 4. Send Telegram message via TelegramClient.send_message()
    # 5. Store outbound Message to DB
    # 6. Log to Log DB
```

### 5.3 Telegram Message Consumer
Polls Telegram API for incoming messages.

```python
@shared_task
def telegram_consumer():
    # Runs every 30 seconds via Celery Beat
    bots = Bot.objects.filter(is_active=True)
    
    for bot in bots:
        telegram_client = TelegramClient(token=bot.telegram_bot_token)
        updates = telegram_client.get_updates()
        
        for update in updates:
            process_telegram_message.delay(bot.id, update)

@shared_task
def process_telegram_message(bot_id, update):
    # 1. Extract message text and chat_id from Telegram update
    # 2. Get bot and create/update Message record
    # 3. Store inbound Message to DB
    # 4. Run intent detection via Ollama
    # 5. If report intent → report_generator.delay(bot_id, message_id)
    # 6. Else → generate reply via Ollama → send Telegram message → store to DB
```

### 5.4 Report Generator Task

```python
@shared_task
def report_generator(bot_id, triggered_by_message_id):
    # 1. Load full message history for the bot
    # 2. Call Ollama: generate HTML report
    # 3. WeasyPrint: HTML → PDF bytes
    # 4. Send PDF via Telegram sendDocument()
    # 5. Store Message records to DB for PDF sent + confirmation
    # 6. Send Telegram message confirmation
```

---

## 6. Telegram Integration

### 6.1 Telegram Client Setup
The `TelegramClient` class wraps the Telegram Bot API for easy integration:

```python
# app/telegram.py
class TelegramClient:
    def __init__(self, token: str, chat_id: str | None = None):
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, text: str, parse_mode: str = "Markdown") -> dict:
        # Sends text message via Telegram API
        # Automatically chunks long messages
        pass
    
    def send_document(self, file_bytes: bytes, filename: str, caption: str = "") -> dict:
        # Sends file (PDF, etc.) via Telegram API
        pass
    
    def get_updates(self, offset: int = 0, timeout: int = 20) -> list:
        # Polls Telegram for new messages
        pass
```

### 6.2 Outbound Messages — Telegram sendMessage
```python
# Usage in bot_runner task:
telegram_client = TelegramClient(token=bot.telegram_bot_token, chat_id=user_chat_id)
result = telegram_client.send_message(text=response_text)
```

### 6.3 Inbound Messages — Telegram getUpdates Polling
Celery task polls Telegram API every 30 seconds:
```python
# Usage in telegram_consumer task:
telegram_client = TelegramClient(token=bot.telegram_bot_token)
updates = telegram_client.get_updates(offset=last_known_offset)
# Process each update...
```

### 6.4 Reports — Telegram sendDocument
```python
# Usage in report_generator task:
telegram_client = TelegramClient(token=bot.telegram_bot_token, chat_id=user_chat_id)
result = telegram_client.send_document(file_bytes=pdf_bytes, filename="report.pdf", caption="Your report")
```

---

## 7. Intent Detection

Before processing any inbound SMS, Ollama classifies the user's intent:

```python
INTENT_PROMPT = """
Classify this message into one of these intents:
- journal_entry: user is writing a journal entry or responding to a prompt
- report_request: user wants a summary, report, or overview
- command: user is giving a command (!pause, !goals, etc.)
- question: user is asking a specific question
- other: anything else

Message: "{message}"
Reply with ONLY the intent label, nothing else.
"""
```

This runs as a fast, low-token Ollama call before the main processing logic.

---

## 8. Django Admin Configuration

The admin panel is the primary UI. Key customizations:

- **Bot admin**: inline `MCPServer` editor on the Bot detail page
- **Bot admin**: displays Telegram bot token, scheduling info, Ollama model config
- **Message admin**: read-only message thread viewer (like a chat log), filterable by bot, intent, status, channel
- **Log admin**: filterable by bot, success/failure, date — useful for debugging
- **MCPServer admin**: transport type selector (local/remote), secrets masked with `***` in list view

### Admin Permissions
- Regular staff → can only see bots they created (`created_by`)

---

## 9. Settings Structure

```python
# settings.py (key additions)

OLLAMA_BASE_URL = env('OLLAMA_BASE_URL', default='http://localhost:11434')
OLLAMA_DEFAULT_MODEL = env('OLLAMA_DEFAULT_MODEL', default='auto')

CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Telegram (bot tokens are stored in Bot model, not settings)
# Each bot has its own telegram_bot_token field

# Optional: Encryption for MCPServer secrets
ENCRYPTION_KEY = env('ENCRYPTION_KEY', default='')  # for encrypting MCPServer secrets
```

---

## 10. Tech Stack Summary

| Concern | Technology |
|---------|-----------|
| Web framework | Django 5.x |
| Admin UI | Django Admin (customized) |
| Task queue | Celery 5.x |
| Beat scheduler | django-celery-beat (DB scheduler) |
| Message broker | Redis |
| Database | PostgreSQL (primary) |
| Outbound messaging | Telegram Bot API |
| Inbound messaging | Telegram getUpdates polling |
| LLM | Ollama (local) |
| PDF generation | WeasyPrint |
| Markdown editor | Martor |
| Secret encryption | cryptography (optional) |
| Environment config | django-environ |
| MCP Protocol | Model Context Protocol (extensible) |

---

## 11. Build Phases (High Level)

| Phase | Deliverable |
|-------|-------------|
| 1 | Django project setup, all models, admin registration, migrations |
| 2 | Ollama client + basic LLM call working |
| 3 | Telegram Bot setup + TelegramClient (send_message, send_document, get_updates) |
| 4 | Master poller + bot runner Celery tasks |
| 5 | Telegram message polling via telegram_consumer Celery task |
| 6 | Intent detection + full conversation loop |
| 7 | Report generation (HTML → PDF → Telegram sendDocument) |
| 8 | Django admin polish + MCPServer configuration |
| 9 | First real bot built on top: Journaling Bot |
| 10 | Hardening, error handling, logging |
