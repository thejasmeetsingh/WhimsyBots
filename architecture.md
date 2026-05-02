# Architecture & Data Models v2.0

## 1. What We're Building

A self-hosted, Django-based AI agent platform where users can create and configure AI-powered "apps" (e.g. a Journaling Agent, a Research Assistant, a Daily Briefing bot) through an admin panel. Each app runs on a schedule, communicates with users via Telegram, processes replies using a local LLM (Ollama), can generate rich PDF reports on demand, and integrates with external tools via the Model Context Protocol (MCP).

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
WhimsyBots/
  src/
    whimsybots/          ← Django project settings, Celery config, URLs
      settings.py
      celery.py
      urls.py
      views.py
    
    app/                 ← Core application
      models.py          ← All data models (Bot, Message, CronJob, Log, Ollama, MCPServer)
      tasks.py           ← Celery tasks (polling, processing, report generation)
      choices.py         ← Enum-based choices (roles, intents, transports)
      validators.py      ← Custom validators (cron, transport)
      utils.py           ← Utility functions (PDF gen, message splitting, etc.)
      admin.py           ← Django admin configuration
      forms.py           ← Django forms
      config/
        celery_config.py ← Celery configuration constants
      managers/
        telegram_client.py ← Telegram client manager
        ollama_config.py   ← Ollama config manager
      services/
        bot_processor.py         ← Main bot message processing with tool calling
        report_generator.py      ← PDF report generation service
        telegram_update_handler.py ← Incoming Telegram update handler
        tool_calling_coordinator.py ← Ollama tool calling loop
        tool_executor.py         ← MCP tool execution
      migrations/
    
    clients/               ← External API clients
      telegram.py          ← Telegram Bot API client
      ollama.py            ← Ollama LLM client
      mcp.py               ← Model Context Protocol client
    
    static/                ← Static files (admin, martor, plugins)
  
  manage.py
  requirements.txt
  docker-compose.yml
  Dockerfile
  Makefile
```

---

## 4. Data Models

All models inherit from `BaseModel` which provides:
- `id`: UUID primary key (auto-generated, indexed)
- `created_at`: Timestamp of creation
- `updated_at`: Timestamp of last update

### 4.1 Core Models

#### `Ollama`
Global Ollama LLM configuration. Only one instance should exist (enforced via admin).

```python
class Ollama(BaseModel):
    endpoint        = models.URLField(default="http://localhost:11434")
    default_model   = models.CharField(max_length=50, null=True, blank=True)
    api_key         = models.CharField(max_length=100, null=True, blank=True)
    temperature     = models.FloatField(default=0.7)
    num_ctx         = models.PositiveIntegerField(default=4096)
    num_predict     = models.PositiveIntegerField(null=True, blank=True)
```

#### `Bot`
The central model. Every agent/bot the user creates is a Bot instance.

```python
class Bot(BaseModel):
    created_by          = models.ForeignKey(User, on_delete=models.CASCADE)
    name                = models.CharField()
    description         = models.TextField(null=True, blank=True)
    is_active           = models.BooleanField(default=True)
    
    # LLM Configuration
    ollama_model        = models.CharField(max_length=50, null=True, blank=True)
    system_prompt       = MartorField(null=True, blank=True)  # Markdown support
    
    # Telegram Communication
    telegram_bot_token  = models.CharField(max_length=255, unique=True)
    telegram_chat_id    = models.CharField(max_length=255, unique=True, null=True)
```

#### `MCPServer`
Model Context Protocol servers connected to a bot for extended functionality.

```python
class MCPServer(BaseModel):
    bot         = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='mcp_servers')
    name        = models.CharField(max_length=100)
    transport   = models.CharField(max_length=1, choices=MCPTransportType.get_values())  # 'L' or 'R'
    command     = models.CharField(max_length=10, null=True, blank=True)  # For LOCAL: python, npx, uv
    endpoint    = models.URLField(null=True, blank=True)  # For REMOTE: HTTPS URL
    args        = ArrayField(base_field=models.CharField(max_length=500), default=list)
    secrets     = models.JSONField(default=dict, null=True, blank=True)
    is_active   = models.BooleanField(default=True)
```

#### `Message`
Every message in a conversation — inbound and outbound.

```python
class Message(BaseModel):
    bot             = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='messages')
    role            = models.CharField(max_length=1, choices=MessageRole.get_values())  # 'S', 'U', 'A'
    intent          = models.CharField(max_length=2, choices=MessageIntentType.get_values(), null=True, blank=True)
    content         = models.TextField()
```

#### `CronJob`
Scheduled job definition for bot execution using cron expressions.

```python
class CronJob(BaseModel):
    bot             = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='bot_cron_jobs')
    name            = models.CharField(max_length=100)
    cron_expression = models.CharField(max_length=100, validators=[validate_cron_expression])
    next_run_at     = models.DateTimeField()
    last_run_at     = models.DateTimeField(null=True, blank=True)
    is_active       = models.BooleanField(default=True)
```

#### `Log`
Audit trail for bot executions.

```python
class Log(BaseModel):
    bot             = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='bot_logs')
    is_success      = models.BooleanField(default=True)
    description     = models.TextField(null=True, blank=True)
```

### 4.2 Choice Enumerations

```python
class MessageRole(BaseChoices):
    SYSTEM    = ('S', 'System')
    USER      = ('U', 'User')
    ASSISTANT = ('A', 'Assistant')

class MessageIntentType(BaseChoices):
    JOURNAL  = ('J', 'Journal Entry')
    REPORT   = ('R', 'Report Request')
    QUESTION = ('Q', 'Question/Query')
    CRON_JOB = ('CJ', 'Manage Cron Jobs')
    OTHER    = ('O', 'Other')

class MCPTransportType(BaseChoices):
    LOCAL  = ('L', 'Local')   # Stdio-based (command + args)
    REMOTE = ('R', 'Remote')  # HTTP-based (URL + headers)
```

---

## 5. Celery Architecture

### 5.1 Queue Structure

The system uses a multi-queue architecture for optimal task routing:

```
CELERY_QUEUES = {
    "beat":    {"exchange": "beat", "routing_key": "beat"},     # Scheduled tasks
    "default": {"exchange": "default", "routing_key": "default"} # All other tasks
}
```

### 5.2 Scheduled Tasks (Beat Queue)

#### `cron_job_poller`
Runs every minute via Celery Beat. Checks for due cron jobs and queues them for execution.

```python
@celery.task(bind=True, max_retries=3)
def cron_job_poller():
    # 1. Check Ollama configuration exists
    # 2. Find active CronJobs with next_run_at <= now
    # 3. Queue process_inbound_message for each due job
    # 4. Update next_run_at and last_run_at
    # 5. Bulk update cron jobs
```

**Schedule:** Every minute (`crontab(minute="*")`)

### 5.3 Message Processing Tasks (Default Queue)

#### `telegram_msg_handler`
Handles incoming Telegram updates (webhook or polling).

```python
@celery.task(bind=True, max_retries=3)
def telegram_msg_handler(bot_token: str, update: dict):
    # 1. Fetch bot by token
    # 2. Call TelegramUpdateHandler.handle_update()
    # 3. Retry on failure
```

#### `process_inbound_message`
Main message processing pipeline with tool calling.

```python
@celery.task(bind=True, max_retries=3)
def process_inbound_message(bot_id: str, msg_id: str):
    # 1. Validate Ollama configuration
    # 2. Initialize BotMessageProcessor
    # 3. Process message with tool calling loop
    # 4. Queue classify_intent task
    # 5. Send response to user
```

#### `classify_intent`
Classifies message intent and triggers report generation if needed.

```python
@celery.task(bind=True, max_retries=3)
def classify_intent(bot_id: str, msg_id: str, intent: str):
    # 1. Update Message.intent field
    # 2. If intent == REPORT: queue generate_report task
```

#### `generate_report`
Generates PDF report from conversation history.

```python
@celery.task(bind=True, max_retries=3)
def generate_report(bot_id: str):
    # 1. Initialize ReportGeneratorService
    # 2. Generate HTML report via LLM
    # 3. Convert HTML to PDF
    # 4. Send PDF via Telegram send_document()
```

#### `setup_bot_webhook`
Sets up Telegram webhook for a bot (used during bot activation).

```python
@celery.task(bind=True, max_retries=3)
def setup_bot_webhook(bot_id: str):
    # 1. Fetch bot configuration
    # 2. Construct webhook URL
    # 3. Register webhook with Telegram API
```

### 5.4 Retry Strategy

All tasks use exponential backoff retry:
- **Max retries:** 3
- **Backoff:** 60s, 120s, 240s (60 × 2^retry_count)

---

## 6. Telegram Integration

### 6.1 Communication Modes

**Webhook (Recommended):** Telegram pushes updates to `/webhook/{bot_token}/` endpoint

### 6.2 Telegram Client

Located in `clients/telegram.py`:

```python
class TelegramClient:
    def __init__(self, token: str, chat_id: str = None):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
    
    def send_message(self, text: str, parse_mode: str = "Markdown") -> dict:
        # Auto-splits messages > 4096 chars
        # Supports Markdown, HTML, or plain text
    
    def send_document(self, file_bytes: bytes, filename: str, caption: str = "") -> dict:
        # Sends PDF or other files
    
    def send_typing_action(self) -> dict:
        # Shows "typing..." indicator
    
    def set_webhook(self, url: str, allowed_updates: list) -> dict:
        # Registers webhook with Telegram
    
    def get_updates(self, offset: int = 0, timeout: int = 20) -> list:
        # Polls for new messages (polling mode)
```

### 6.3 Telegram Client Manager

Abstraction layer for creating clients:

```python
class TelegramClientManager:
    @staticmethod
    def create_client(bot) -> TelegramClient:
        return TelegramClient(bot.telegram_bot_token, bot.telegram_chat_id)
```

### 6.4 Update Handler

Service for processing incoming updates:

```python
class TelegramUpdateHandler:
    @staticmethod
    def handle_update(bot: Bot, update: dict):
        # 1. Parse update (extract message_id, chat_id, text)
        # 2. Update bot.telegram_chat_id if not set
        # 3. Create Message record (role=USER)
        # 4. Send typing action
        # 5. Queue process_inbound_message task
```

### 6.5 Message Flow

#### Inbound (User → Bot)
```
Telegram User Message
  → Webhook/Polling
  → TelegramUpdateHandler.handle_update()
  → Message saved to DB (role=USER)
  → Typing indicator sent
  → process_inbound_message task queued
  → BotMessageProcessor processes with tools
  → Response sent via TelegramClient.send_message()
  → Response saved to DB (role=ASSISTANT)
  → classify_intent task queued
  → If REPORT intent: generate_report task queued
```

#### Outbound (Scheduled Bot Message)
```
Celery Beat (every minute)
  → cron_job_poller finds due CronJobs
  → process_inbound_message queued (msg_id=None)
  → BotMessageProcessor builds context
  → Tool calling loop runs
  → Response sent via TelegramClient
```

---

## 7. Intent Detection

Intent detection happens **after** the initial message processing, in a separate Celery task (`classify_intent`). This allows the bot to respond quickly while intent-based actions (like report generation) run asynchronously.

### 7.1 Intent Types

```python
class MessageIntentType(BaseChoices):
    JOURNAL  = ('J', 'Journal Entry')     # User writing/reflection
    REPORT   = ('R', 'Report Request')    # User wants summary/PDF
    QUESTION = ('Q', 'Question/Query')    # User asking something
    CRON_JOB = ('CJ', 'Manage Cron Jobs') # Scheduling commands
    OTHER    = ('O', 'Other')             # Everything else
```

### 7.2 Detection Flow

```
Message Processing Complete
  → classify_intent task receives intent from processor
  → Updates Message.intent field
  → If intent == REPORT:
      → generate_report task queued
      → PDF generated and sent
  → Task completes
```

### 7.3 Report Generation

When a REPORT intent is detected:

```python
# Report generation prompt (in CeleryConfig)
REPORT_GENERATION_PROMPT = """
Generate a comprehensive HTML report with inline CSS from the conversation history.
Include proper formatting, sections, and styling.
Return ONLY the HTML content.
"""

# ReportGeneratorService workflow:
# 1. Fetch conversation history
# 2. Build MCP tools (if any)
# 3. Run tool calling loop with REPORT_GENERATION_PROMPT
# 4. Extract HTML from response
# 5. Convert to PDF via WeasyPrint
# 6. Send via Telegram send_document()
# 7. Save assistant message to DB
```

---

## 8. Model Context Protocol (MCP) Integration

The system integrates with external tools and services via MCP, supporting both local and remote servers.

### 8.1 Architecture

```
Bot
  └── MCPServer (1:many)
        └── MCPClient
              └── Tools (discovered dynamically)
```

### 8.2 MCP Client

Located in `clients/mcp.py`:

```python
class MCPClient:
    def __init__(self, transport_type: str, config: dict):
        # transport_type: 'L' (LOCAL) or 'R' (REMOTE)
        # config: command/args/env (local) or url/headers (remote)
    
    async def list_tools(self) -> list[dict]:
        # Discovers available tools from MCP server
        # Returns tools in Ollama function-calling format
    
    async def execute_tool(self, tool_name: str, args: dict) -> any:
        # Executes a tool with given arguments
        # Returns tool result
    
    async def cleanup(self):
        # Properly closes connections
```

### 8.3 Tool Building

```python
class MCPToolsBuilder:
    @staticmethod
    async def build_tools_from_servers(mcp_servers) -> List[MCPToolConfig]:
        # Iterates through active MCP servers
        # Connects to each server
        # Discovers tools
        # Returns list of MCPToolConfig
```

### 8.4 Tool Execution

```python
class ToolExecutor:
    def __init__(self, tools: List[MCPToolConfig]):
        # Pre-configured with available tools
    
    def execute_tool_call_sync(self, tool_call: dict) -> any:
        # Executes a single tool call
        # Handles both LOCAL and REMOTE transports
        # Returns result for LLM context
```

### 8.5 Transport Types

#### LOCAL (Stdio-based)
- **Use case:** Running MCP servers locally (Python scripts, npm packages)
- **Config:** `command`, `args`, `env`
- **Example:** `python -m mcp_server_memory`

#### REMOTE (HTTP-based)
- **Use case:** Remote MCP servers over HTTPS
- **Config:** `url`, `headers`
- **Example:** `https://api.example.com/mcp`

### 8.6 Tool Calling Flow

```
BotMessageProcessor.process_message()
  → MCPToolsBuilder.build_tools_from_servers()
  → ToolExecutor initialized with tools
  → run_tool_calling_loop():
      → Ollama chat with tools
      → If tools called:
          → ToolExecutor.execute_tool_call_sync()
          → Append tool result to history
          → Loop continues
      → If no tools: return final response
```

---

## 9. Django Admin Configuration

The admin panel is the primary UI for bot configuration and monitoring.

### 9.1 Key Customizations

- **Bot admin:**
  - Inline `MCPServer` editor on Bot detail page
  - Displays Telegram bot token, scheduling info, Ollama model config
  - Filter by active/inactive, creator
  - UUID-based IDs displayed

- **CronJob admin:**
  - Inline editor on Bot detail page (or separate)
  - Shows next_run_at, last_run_at, cron expression
  - Filter by active/inactive, bot

- **Message admin:**
  - Read-only message thread viewer (like a chat log)
  - Filterable by bot, intent, role
  - Shows content preview

- **Log admin:**
  - Filterable by bot, success/failure, date
  - Useful for debugging bot executions

- **MCPServer admin:**
  - Transport type selector (local/remote)
  - Secrets masked with `***` in list view
  - Validation for transport-specific fields

- **Ollama admin:**
  - Single instance enforcement (only one config allowed)
  - Test connection button (future)

### 9.2 Admin Permissions

- **Superusers:** Full access to all bots and configurations
- **Regular staff:** Can only see bots they created (`created_by`)
- **Filtering:** By creator, active status, date ranges

### 9.3 Martor Integration

Markdown editor enabled for:
- `Bot.system_prompt`
- Any other long-form text fields

Features:
- Emoji support
- Syntax highlighting
- Preview mode
- Bootstrap theme

---

## 10. Settings Structure

### 10.1 Environment Variables

```python
# Required
SECRET_KEY              = os.getenv("SECRET_KEY")
DB_NAME                 = os.getenv("DB_NAME")
DB_USER                 = os.getenv("DB_USER")
DB_PASSWORD             = os.getenv("DB_PASSWORD")
DB_HOST                 = os.getenv("DB_HOST")
CELERY_BROKER_URL       = os.getenv("CELERY_BROKER_URL")      # redis://...
CELERY_RESULT_BACKEND   = os.getenv("CELERY_RESULT_BACKEND")   # redis://...

# Optional
WEBHOOK_BASE_URL        = os.getenv("WEBHOOK_BASE_URL", "https://localhost:8000")
```

### 10.2 Celery Configuration

```python
CELERY_QUEUES = {
    "beat":    {"exchange": "beat", "routing_key": "beat"},
    "default": {"exchange": "default", "routing_key": "default"},
}

CELERY_TASK_ROUTES = {
    "app.tasks.cron_job_poller": {"queue": "beat"},
}

CELERY_BEAT_SCHEDULE = {
    "cron-job-poller": {
        "task": "app.tasks.cron_job_poller",
        "schedule": crontab(minute="*"),
        "options": {"queue": "beat"},
    },
}
```

### 10.3 Logging Configuration

JSON structured logging with custom formatter:

```python
LOGGING_CONFIG = None
logging.config.dictConfig({
    "version": 1,
    "formatters": {
        "default": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s - %(module)s.%(funcName)s - %(name)s - %(levelname)s - %(message)s"
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"": {"level": "INFO", "handlers": ["console"]}}
})
```

### 10.4 Static Files

```python
STATIC_URL = "/static/"
STATICFILES_DIRS = [os.path.join(BASE_DIR, "whimsybots/static")]
STATIC_ROOT = os.path.join(BASE_DIR, "static")
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
```

### 10.5 Martor Configuration

```python
MARTOR_THEME = "bootstrap"
MARTOR_ENABLE_CONFIGS = {
    "emoji": "true",
    "imgur": "false",
    "mention": "false",
    "jquery": "true",
    "living": "false",
    "spellcheck": "false",
    "hljs": "true",
}
```

---

## 11. Tech Stack Summary

| Concern | Technology | Notes |
|---------|-----------|-------|
| **Core Framework** |
| Web framework | Django 5.x | Python web framework |
| Admin UI | Django Admin | Customized with inlines, filters |
| Markdown editor | Martor | Bootstrap theme, emoji, syntax highlighting |
| **Task Queue** |
| Task queue | Celery 5.x | Async task processing |
| Beat scheduler | django-celery-beat | Database-backed scheduler |
| Message broker | Redis | Broker + result backend |
| **Data Storage** |
| Database | PostgreSQL | Primary data store |
| **AI/LLM** |
| LLM runtime | Ollama | Local LLM server |
| LLM client | ollama Python package | Official client library |
| Tool protocol | Model Context Protocol (MCP) | External tool integration |
| **Communication** |
| Messaging | Telegram Bot API | User communication channel |
| HTTP client | requests | For Telegram API calls |
| **Report Generation** |
| PDF generation | WeasyPrint | HTML to PDF conversion |
| **Infrastructure** |
| Containerization | Docker | Containerized deployment |
| Process manager | Gunicorn | WSGI HTTP server |
| Static files | WhiteNoise | Production static file serving |
| Logging | python-json-logger | Structured JSON logging |
| **Development** |
| Environment | python-dotenv | Environment variable management |
| Package manager | pip | Python dependencies |

---

## 12. Deployment Architecture

### 12.1 Components

```
┌─────────────────────────────────────────────────────────┐
│                    Load Balancer                         │
│              (nginx / cloud LB / traefik)               │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
   ┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼─────┐
   │   Django    │ │  Celery   │ │   Celery  │
   │   (Gunicorn)│ │  Worker   │ │   Beat    │
   │   :8000     │ │  (default)│ │  (beat)   │
   └──────┬──────┘ └─────┬─────┘ └─────┬─────┘
          │              │             │
   ┌──────▼──────────────▼─────────────▼──────┐
   │              Redis Broker                 │
   │         (messages + results)             │
   └──────┬───────────────────────────────────┘
          │
   ┌──────▼──────┐
   │ PostgreSQL   │
   │  Database    │
   └─────────────┘
```

### 12.2 External Services

```
┌──────────────────┐
│   Ollama Server  │  ← Local or remote LLM
│  (localhost:11434)│
└──────────────────┘

┌──────────────────┐
│  Telegram API    │  ← Cloud-based messaging
│ api.telegram.org │
└──────────────────┘

┌──────────────────┐
│  MCP Servers     │  ← External tools (local/remote)
│  (various)       │
└──────────────────┘
```

### 12.3 Docker Services

```yaml
services:
  web:       # Django + Gunicorn
  celery:    # Celery worker (default queue)
  beat:      # Celery beat scheduler
  redis:     # Message broker
  postgres:  # Database
  ollama:    # Optional: Local LLM (can be external)
```

---

## 12. Build Status

### ✅ Completed

| Component | Status | Notes |
|-----------|--------|-------|
| Django project structure | ✅ | Full setup with whimsybots config |
| Data models | ✅ | All models with UUID primary keys |
| Django admin | ✅ | Customized with inlines, filters |
| Ollama client | ✅ | Full chat + tool calling support |
| Telegram client | ✅ | send_message, send_document, typing, webhook |
| MCP client | ✅ | Local + remote transport support |
| Celery tasks | ✅ | All core tasks implemented |
| Tool calling loop | ✅ | Async tool execution with Ollama |
| Report generation | ✅ | HTML → PDF → Telegram |
| Intent detection | ✅ | Async classification pipeline |
| Multi-queue Celery | ✅ | beat + default queues |
| Structured logging | ✅ | JSON logging configured |

### 🚧 In Progress / Future

| Component | Status | Notes |
|-----------|--------|-------|
| Telegram polling fallback | 🔄 | Webhook primary, polling backup |
| User model customization | ⏳ | Currently using Django default User |
| Encryption for secrets | ⏳ | MCPServer.secrets currently plaintext |
| REST API | ⏳ | Future: DRF or Django Ninja |
| Web UI (beyond admin) | ⏳ | Future: User-facing dashboard |
| First production bot | ⏳ | Journaling Bot template |
| Monitoring/observability | ⏳ | Metrics, tracing, alerting |
| Rate limiting | ⏳ | Telegram API rate limit handling |
| Message chunking optimization | ⏳ | Smart splitting for long responses |

---

## 13. Key Design Decisions

### 13.1 UUID Primary Keys
- **Why:** Security through obscurity, distributed system friendly
- **Trade-off:** Slightly larger indexes, less human-readable

### 13.2 Async Tool Calling
- **Why:** MCP client requires async/await for stdio/HTTP connections
- **Implementation:** `asyncio.run()` in synchronous Celery tasks

### 13.3 Separate Intent Classification
- **Why:** Fast response time, async report generation
- **Flow:** Process → Respond → Classify → Act (if needed)

### 13.4 Multi-Queue Celery
- **Why:** Isolate beat scheduling from worker processing
- **Benefit:** Prevents worker overload from affecting scheduler

### 13.5 Service Layer Pattern
- **Why:** Clean separation of concerns, testability
- **Structure:** Tasks → Services → Clients → External APIs

### 13.6 Tool Calling Abstraction
- **Why:** Unified interface for MCP tools regardless of transport
- **Benefit:** Easy to add new MCP servers without code changes
