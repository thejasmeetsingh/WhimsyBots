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

#### Outbound (Scheduled Cron Job)
```
Celery Beat (every minute)
  → cron_job_poller finds active CronJobs with next_run_at <= now
  → Queue process_cron_job task for each due job
  → process_cron_job task:
      → Fetches CronJob with name and description
      → BotMessageProcessor.process_cron_job(name, description):
          → Builds tools from bot's MCPServers + default servers (time, cron_job)
          → Runs Ollama tool_calling_loop with CRON_JOB_PROMPT
          → LLM executes task with tool access
      → Response sent to user via TelegramClient.send_message()
      → Updates CronJob.next_run_at and last_run_at
```

#### Inbound (User Telegram Message)
```
User sends message via Telegram
  → Webhook receives update at /webhook/{bot_token}/
  → telegram_msg_handler task queued
  → TelegramUpdateHandler.handle_update():
      → Stores message to DB (role=USER)
      → Sends typing indicator
      → Queue process_inbound_message task
  → process_inbound_message task:
      → BotMessageProcessor.process_message():
          → Builds tools from bot's active MCPServers + default servers (time, cron_job)
          → Constructs system_prompt with bot_id and timezone
          → Runs Ollama tool_calling_loop with StructuredOutput JSON schema format
          → LLM returns: {intent: J|R|Q|CJ|O, response: string}
      → Stores response to DB (role=ASSISTANT, intent=classified)
      → Sends response via TelegramClient.send_message()
      → If intent=='R': Queue generate_report task
```

#### Report Generation (Async)
```
Intent detected as REPORT (intent=='R')
  → generate_report task queued
  → ReportGeneratorService.generate_report():
      → Fetches bot's conversation history
      → Prompts LLM with REPORT_GENERATION_PROMPT to create HTML
      → Extracts HTML from response (handles markdown code fences)
      → WeasyPrint converts HTML → PDF (in memory)
      → Sends PDF via TelegramClient.send_document()
      → Sends confirmation message via TelegramClient.send_message()
      → Stores assistant message to DB
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
    
    cron_job/              ← Standalone Cron Job MCP Server (FastMCP)
      __init__.py
      __main__.py          ← Entry point (python -m cron_job)
      server.py            ← MCP server definition with tools
      db.py                ← Async database session management
      models.py            ← SQLAlchemy models (mirrors app.models.CronJob)
      helpers.py           ← Utility functions for cron parsing, validation
    
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
    api_key         = EncryptedCharField(null=True, blank=True)  # Encrypted
    temperature     = models.FloatField(default=0.7)
    num_ctx         = models.PositiveIntegerField(default=4096)
    keep_alive      = models.CharField(max_length=10, default="10m")  # Model keep-alive duration
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
    telegram_bot_token  = EncryptedCharField()  # Encrypted
    telegram_bot_token_hash = models.CharField(max_length=64, unique=True)  # For token lookup
    telegram_chat_id    = models.CharField(max_length=255, null=True)  # Auto-populated on first message
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
    secrets     = EncryptedJSONField(default=dict, null=True, blank=True)  # Encrypted environment variables/headers
    is_active   = models.BooleanField(default=True)
```

#### `Message`
Every message in a conversation — inbound and outbound.

```python
class Message(BaseModel):
    bot             = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='messages')
    role            = models.CharField(max_length=1, choices=MessageRole.get_values())  # 'S', 'U', 'A'
    intent          = models.CharField(max_length=2, choices=MessageIntentType.get_values(), null=True, blank=True)  # Classified by LLM
    content         = models.TextField()
```

#### `CronJob`
Scheduled job definition for bot execution using cron expressions. Each job has a name and description that define what the bot should do when executed.

```python
class CronJob(BaseModel):
    bot             = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='bot_cron_jobs')
    name            = models.CharField(max_length=100)  # Short job label
    description     = models.TextField()  # Detailed description of job purpose
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
    # 3. Queue process_cron_job task for each due job
    # 4. Exponential backoff retry on failure
```

**Schedule:** Every minute (`crontab(minute="*")`)

### 5.3 Message Processing Tasks (Default Queue)

#### `telegram_msg_handler`
Handles incoming Telegram webhook updates by fetching the bot and calling TelegramUpdateHandler.

```python
@celery.task(bind=True, max_retries=3)
def telegram_msg_handler(bot_token: str, update: dict):
    # 1. Fetch bot by token_hash (lookup via get_token_hash())
    # 2. Call TelegramUpdateHandler.handle_update(bot, update)
    # 3. Stores message and queues process_inbound_message
    # 4. Handles Telegram rate limit errors with retry + exponential backoff
    # 5. Logs comprehensive task information via LogFormatter
```

#### `process_inbound_message`
Main message processing pipeline with embedded intent detection via structured output.

```python
@celery.task(bind=True, max_retries=3)
def process_inbound_message(bot_id: str, msg_id: str):
    # 1. Validate Ollama configuration
    # 2. Initialize BotMessageProcessor
    # 3. BotMessageProcessor.process_message():
    #    - Builds tools from bot's MCPServers + default servers (time, cron_job)
    #    - Sends responsive typing indicators during processing
    #    - Runs Ollama tool_calling_loop with StructuredOutput JSON schema
    #    - Returns {intent: J|R|Q|CJ|O, response: string}
    # 4. Store intent in Message.intent field
    # 5. Send response via Telegram with intelligent message splitting (>4096 chars)
    # 6. If intent=='R': queue generate_report task
    # 7. If context window approaching limit: queue manage_conversation_summary task
    # 8. Handles Telegram rate limit errors with retry + exponential backoff
    # 9. Retry on failure with exponential backoff
```

#### `process_cron_job`
Processes a scheduled cron job execution.

```python
@celery.task(bind=True, max_retries=3)
def process_cron_job(job_id: str):
    # 1. Fetch CronJob by ID with name and description
    # 2. Validate Ollama configuration
    # 3. Initialize BotMessageProcessor
    # 4. BotMessageProcessor.process_cron_job(name, description):
    #    - Builds tools from bot's MCPServers + default time server
    #    - Runs Ollama tool_calling_loop with CRON_JOB_PROMPT
    #    - LLM executes the task
    # 5. Send response via Telegram
    # 6. Update CronJob.next_run_at (via croniter) and last_run_at
    # 7. Retry on failure with exponential backoff
```

#### `generate_report`
Generates and sends a PDF report in response to a REPORT intent.

```python
@celery.task(bind=True, max_retries=3)
def generate_report(bot_id: str):
    # 1. Initialize ReportGeneratorService
    # 2. Generate HTML report via Ollama with REPORT_GENERATION_PROMPT
    # 3. Extract HTML from LLM response (handles markdown wrappers)
    # 4. Convert HTML to PDF via WeasyPrint
    # 5. Send PDF via TelegramClient.send_document() with summary message
    # 6. Send confirmation message with intelligent message splitting
    # 7. Store bot's response message to DB
    # 8. Handles Telegram rate limit errors with retry + exponential backoff
    # 9. Retry on failure with exponential backoff
```

#### `manage_conversation_summary`
Manages conversation context window by summarizing old messages when context approaches limit.

```python
@celery.task(bind=True, max_retries=3)
def manage_conversation_summary(bot_id: str):
    # 1. Fetch recent conversation messages for bot
    # 2. Check if context window is approaching limit
    # 3. Initialize ConversationSummaryService
    # 4. Generate summary of old messages via Ollama
    # 5. Replace old messages with summary message
    # 6. Maintain recent messages for immediate context
    # 7. Retry on failure with exponential backoff
```

#### `setup_bot_webhook`
Sets up Telegram webhook for a bot during activation.

```python
@celery.task(bind=True, max_retries=3)
def setup_bot_webhook(bot_id: str):
    # 1. Fetch bot configuration
    # 2. Construct webhook URL: {WEBHOOK_BASE_URL}/webhook/{bot_token}/
    # 3. Register webhook with Telegram API
    # 4. Retry on failure with exponential backoff
```

### 5.4 Retry Strategy

All tasks use exponential backoff retry:
- **Max retries:** 3
- **Backoff:** 60s × 2^(retry_count) → 60s, 120s, 240s

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
        # 4. Send typing action (responsive to LLM processing time)
        # 5. Queue process_inbound_message task
        # 6. Handle Telegram rate limits gracefully
```

### 6.5 Message Flow

#### Inbound (User → Bot)
```
Telegram User Message
  → Webhook/Polling
  → TelegramUpdateHandler.handle_update()
  → Message saved to DB (role=USER)
  → Typing indicator sent (responsive)
  → process_inbound_message task queued
  → BotMessageProcessor processes with tools
  → Response sent via TelegramClient.send_message() (intelligent splitting for >4096 chars)
  → Response saved to DB (role=ASSISTANT) with embedded intent classification
  → If REPORT intent: generate_report task queued
  → If context window near limit: manage_conversation_summary task queued
  → If Telegram rate limited: retry with exponential backoff
```

#### Outbound (Scheduled Cron Job)
```
Celery Beat (every minute)
  → cron_job_poller checks all active CronJobs
  → CronJob.next_run_at <= now? → Queue process_cron_job task(job_id)
  → process_cron_job task:
      → Fetch CronJob with name and description
      → BotMessageProcessor.process_cron_job(name, description):
          → Builds tools from active MCPServers + default time server
          → Runs tool_calling_loop with CRON_JOB_PROMPT
          → LLM executes task with access to tools
      → Response sent via Telegram
      → Updates CronJob.next_run_at and last_run_at
```

---

## 7. Intent Detection & Structured Output

Intent detection is **embedded** into `BotMessageProcessor.process_message()` and returns via a structured JSON schema. This ensures intent is always available immediately without needing a separate classification task.

### 7.1 StructuredOutput Model

The LLM is instructed to respond in a specific JSON format using Ollama's `format` parameter:

```python
class StructuredOutput(BaseModel):
    intent: Literal["J", "R", "Q", "CJ", "O"]  # Intent code
    response: str  # Natural language response to user
```

When calling Ollama, we pass `format=StructuredOutput.model_json_schema()` ensuring the LLM responds with valid JSON matching this schema.

### 7.2 Intent Types

```python
class MessageIntentType(BaseChoices):
    JOURNAL  = ('J', 'Journal Entry')     # User writing/reflection
    REPORT   = ('R', 'Report Request')    # User wants summary/PDF/compilation
    QUESTION = ('Q', 'Question/Query')    # User asking something
    CRON_JOB = ('CJ', 'Manage Cron Jobs') # Scheduling commands (cron_job MCP server)
    OTHER    = ('O', 'Other')             # Everything else
```

### 7.3 Classification Flow

```
process_inbound_message task:
  → BotMessageProcessor.process_message():
      → Ollama receives system prompt with DEFAULT_SYSTEM_PROMPT
      → System prompt includes instructions to respond as JSON with intent & response
      → Ollama tool_calling_loop runs with format=StructuredOutput.model_json_schema()
      → LLM returns: {\"intent\": \"J\", \"response\": \"...\"}
      → Response parsed and validated by _parse_llm_response()
      → Returns StructuredOutput object (intent + response)
  → Intent stored in Message.intent field
  → Response sent to user
  → If intent=='R': generate_report task queued
```

### 7.4 Report Generation

When intent==\"R\" (REPORT):

```python
# generate_report task workflow:
# 1. Fetch bot's conversation Message history
# 2. Fetch active MCPServers for bot
# 3. ReportGeneratorService.generate_report():
#    - Build tools from MCPServers
#    - Run Ollama tool_calling_loop with REPORT_GENERATION_PROMPT
#    - LLM generates HTML with inline CSS
#    - extract_html() parses HTML from response (handles markdown fences)
# 4. WeasyPrint converts HTML → PDF (in memory)
# 5. TelegramClient.send_document() sends PDF
# 6. TelegramClient.send_message() sends confirmation
# 7. Store assistant message to DB
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

### 8.7 Default MCP Servers

Every bot automatically includes two default MCP servers without explicit configuration:

#### Time Server
- **Purpose:** Provides current time and timezone information for LLM context
- **Implementation:** `mcp_server_time` (PyPI package)
- **Transport:** LOCAL (stdio-based)
- **Command:** `python -m mcp_server_time`
- **When Used:** Added to all bots by default in `BotMessageProcessor.get_default_mcp_servers()`
- **Tools Available:** `get_current_time`, timezone-aware helpers

#### Cron Job Manager Server
- **Purpose:** Allows LLM to view, create, update, and delete cron jobs
- **Implementation:** Standalone module in `src/cron_job/` with FastMCP
- **Transport:** LOCAL (stdio-based)
- **Command:** `python -m cron_job`
- **Database:** Shares main PostgreSQL database (via environment secrets)
- **When Used:** Added to message processing and cron job execution flows
- **Tools Available:**
  - `list_cron_jobs(bot_id, is_active=None)` — List cron jobs
  - `create_cron_job(bot_id, name, description, cron_expression)` — Create new job
  - `update_cron_job(id, bot_id, name?, description?, cron_expression?, is_active?)` — Update existing job
  - `delete_cron_job(id, bot_id)` — Delete a job
- **Example Use Case:** User asks \"Schedule a daily report at 9 AM\" → LLM calls `create_cron_job` with name=\"Daily Report\", cron_expression=\"0 9 * * *\"

### 8.8 Server Instantiation

```python
# BotMessageProcessor.get_default_mcp_servers() creates temporary MCPServer objects
# These are NOT persisted to database, but used for a single message processing cycle

cron_job_mcp = MCPServer(
    name="cron_job",
    transport=MCPTransportType.LOCAL.value[0],
    command="python",
    args=["-m", "cron_job"],
    secrets={
        "DB_NAME": settings.DB_NAME,
        "DB_USER": settings.DB_USER,
        "DB_PASSWORD": settings.DB_PASSWORD,
        "DB_HOST": settings.DB_HOST,
    },
)

time_mcp = MCPServer(
    name="time",
    transport=MCPTransportType.LOCAL.value[0],
    command="python",
    args=["-m", "mcp_server_time"],
)

# Then mixed with bot's active MCPServers for tool discovery
mcp_servers = list(MCPServer.objects.filter(bot_id=bot.id, is_active=True))
default_servers = get_default_mcp_servers()
mcp_servers.extend(list(default_servers.values()))
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
  - Inline editor on Bot detail page
  - Shows name, description, cron_expression, next_run_at, last_run_at
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
| Async DB client | asyncpg | PostgreSQL async driver for cron_job MCP server |
| **Data Storage** |
| Database | PostgreSQL | Primary data store |
| Data validation | Pydantic v2 | Type validation and structured JSON schemas |
| **AI/LLM** |
| LLM runtime | Ollama | Local LLM server |
| LLM client | ollama Python package | Official client library |
| Tool protocol | Model Context Protocol (MCP) | External tool integration |
| MCP framework | FastMCP | Lightweight Python MCP server framework |
| Cron utilities | croniter | Cron expression parsing and next-run calculation |
| **Communication** |
| Messaging | Telegram Bot API | User communication channel |
| HTTP client | httpx | Async HTTP client (for MCP and APIs) |
| Legacy HTTP | requests | For Telegram API calls |
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
| Data models | ✅ | All models with UUID primary keys + CronJob description field |
| Django admin | ✅ | Customized with inlines, filters |
| Ollama client | ✅ | Full chat + tool calling with JSON schema format support + keep_alive support |
| Ollama keep-alive | ✅ | keep_alive field on Ollama model with validation |
| Telegram client | ✅ | send_message, send_document, typing, webhook + rate limit handling + intelligent message splitting |
| Telegram token encryption | ✅ | telegram_bot_token encrypted + token_hash for lookup |
| MCP client | ✅ | Local + remote transport support with stdio and HTTP |
| Celery tasks | ✅ | All core tasks: telegram_msg_handler, process_inbound_message, process_cron_job, generate_report, manage_conversation_summary, setup_bot_webhook |
| Tool calling loop | ✅ | Async tool execution with Ollama via tool_calling_coordinator |
| StructuredOutput | ✅ | Pydantic-based JSON schema for intent + response |
| Embedded intent detection | ✅ | Intent classification integrated into process_message with JSON schema format |
| Report generation | ✅ | HTML → PDF → Telegram (async via generate_report task) with summary support |
| Cron job processing | ✅ | Dedicated process_cron_job task with CronJob.description support |
| Default MCP servers | ✅ | Time server + Cron Job Manager (FastMCP) injected automatically |
| Cron Job MCP Server | ✅ | Standalone module with list/create/update/delete tools (FastMCP) |
| Multi-queue Celery | ✅ | beat + default queues |
| Comprehensive logging | ✅ | JSON logging configured with LogFormatter for descriptions |
| Async database | ✅ | asyncpg for cron_job MCP server database access |
| Encryption for secrets | ✅ | EncryptedCharField for api_key, EncryptedJSONField for MCPServer.secrets |
| Telegram rate limiting | ✅ | Rate limit retry handling with exponential backoff in tasks |
| Message splitting | ✅ | Intelligent message splitting for responses > 4096 chars |
| Typing indicators | ✅ | Responsive typing indicators during processing |
| Conversation context management | ✅ | ConversationSummaryService with manage_conversation_summary task |
| Celery Flower monitoring | ✅ | Monitoring service in docker-compose |

---

## 13. Key Design Decisions

### 13.1 UUID Primary Keys
- **Why:** Security through obscurity, distributed system friendly
- **Trade-off:** Slightly larger indexes, less human-readable

### 13.2 Async Tool Calling
- **Why:** MCP client requires async/await for stdio/HTTP connections
- **Implementation:** `asyncio.run()` in synchronous Celery tasks

### 13.3 Embedded Intent Detection with StructuredOutput
- **Why:** Fast response time, intent always available, reduced task overhead
- **Flow:** Process → Detect Intent (JSON schema format) → Respond → Act (if needed)
- **Benefit:** Single LLM call returns both intent and response; no intermediate tasks
- **Implementation:** Ollama `format` parameter with Pydantic schema

### 13.4 Default MCP Servers
- **Why:** Provide core functionality (time, cron management) without manual setup
- **Instantiation:** Temporary in-memory MCPServer objects created per message
- **Benefit:** Always available, extensible with custom MCPServers

### 13.5 Multi-Queue Celery
- **Why:** Isolate beat scheduling from worker processing
- **Benefit:** Prevents worker overload from affecting scheduler

### 13.6 Service Layer Pattern
- **Why:** Clean separation of concerns, testability
- **Structure:** Tasks → Services → Clients → External APIs

### 13.7 Tool Calling Abstraction
- **Why:** Unified interface for MCP tools regardless of transport
- **Benefit:** Easy to add new MCP servers without code changes

### 13.8 CronJob with Description
- **Why:** LLM needs context about what each scheduled job does
- **Usage:** Description passed to `process_cron_job` in CRON_JOB_PROMPT
- **Benefit:** LLM can execute task semantically correct without hardcoded logic
