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
          → Builds tools from bot's active MCPServers + default servers (time, cron_job, pdf_generator)
          → Runs Ollama tool_calling_loop with tool calling format
          → LLM can call any available MCP tools including pdf_generator
          → Returns: response string (intent detection is now implicit in tool usage)
      → Stores response to DB (role=ASSISTANT)
      → Sends response via TelegramClient.send_message()
      → If user requested PDF generation via tool call: response already includes PDF context
```

---

## 3. Django App Structure

```
WhimsyBots/
  src/
    whimsybots/          ← Django project settings, Celery config, URLs
      settings.py        ← Django settings, database, Celery configuration
      celery.py          ← Celery app and task routing setup
      urls.py            ← URL routing and webhook endpoints
      views.py           ← Django views (webhook handlers)
      wsgi.py            ← WSGI application entry point
    
    app/                 ← Core application
      models.py          ← All data models (Bot, Message, CronJob, Log, Ollama, MCPServer)
      tasks.py           ← Celery tasks (polling, message processing, summaries, embeddings)
      choices.py         ← Enum-based choices (message roles, transports)
      fields.py          ← Custom Django fields (encrypted, vector)
      validators.py      ← Custom validators (cron, transport)
      utils.py           ← Utility functions (message splitting, conversions, etc.)
      admin.py           ← Django admin configuration
      forms.py           ← Django forms
      apps.py            ← Django app configuration
      tests.py           ← Unit tests (test suite)
      migrations/
    
    managers/
        telegram_client.py ← Telegram client manager
        ollama_config.py   ← Ollama config manager
    
    services/
        bot_processor.py           ← Main bot message processing with tool calling
        context_assembler.py       ← Message history and context assembly
        conversation_summary.py    ← Conversation summarization service
        embedding.py              ← Message embedding generation
        observed_patterns.py      ← Pattern observation and regeneration
        log_formatter.py           ← Structured logging utility
        rate_limiter.py            ← Rate limiting for external APIs
        skills_registry.py         ← MCP server tool descriptions/capabilities
        telegram_update_handler.py ← Incoming Telegram update handler
        token_budget.py            ← Token allocation and context window management
        tool_calling_coordinator.py ← Ollama tool calling loop
        tool_executor.py           ← MCP tool execution

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
    
    pdf_generator/         ← Standalone PDF Generator MCP Server (FastMCP) - NEW
      __init__.py
      __main__.py          ← Entry point (python -m pdf_generator)
      server.py            ← MCP server definition with PDF generation tools
      db.py                ← Async database session management
      helpers.py           ← Utility functions for HTML parsing, PDF rendering
    
    static/                ← Static files (admin, martor, plugins)
  
  manage.py
  prompts.py             ← LLM prompt templates and system prompts
  strings.py             ← Error messages and user-facing strings
  requirements.txt
  docker-compose.yml
  Dockerfile
  Makefile
  gunicorn.conf.py       ← Gunicorn WSGI server configuration
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
    content         = models.TextField()
    content_embedding = VectorField(null=True, blank=True)  # For similarity search & context retrieval
```

**Note:** Intent field has been removed. Intent classification is now implicit in tool selection during message processing.

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
Main message processing pipeline with tool calling loop.

```python
@celery.task(bind=True, max_retries=3)
def process_inbound_message(bot_id: str, msg_id: str):
    # 1. Validate Ollama configuration
    # 2. Fetch bot and message from database
    # 3. Initialize BotMessageProcessor
    # 4. BotMessageProcessor.process_message():
    #    - Builds tools from bot's MCPServers + default servers (time, cron_job, pdf_generator)
    #    - Sends responsive typing indicator during processing
    #    - Runs Ollama tool_calling_loop with all available tools
    #    - LLM decides which tools to call (if any)
    #    - Returns final response string
    # 5. Send response via Telegram with intelligent message splitting (>4096 chars)
    # 6. Queue manage_conversation_summary task if context window approaching limit
    # 7. Conditionally regenerate observed patterns if threshold met
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

    def send_typing_action(self) -> dict:
        # Shows "typing..." indicator
    
    def set_webhook(self, url: str, allowed_updates: list) -> dict:
        # Registers webhook with Telegram
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
  → BotMessageProcessor processes with tools (time, cron_job, pdf_generator + custom)
  → LLM calls tools as needed (implicit intent through tool usage)
  → Response sent via TelegramClient.send_message() (intelligent splitting for >4096 chars)
  → Response saved to DB (role=ASSISTANT)
  → If context window near limit: manage_conversation_summary task queued
  → If context needs embeddings: generate_embedding task queued
  → If pattern update needed: regenerate_observed_patterns task queued
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

## 7. Tool Calling with MCP Integration

Tool calling is now the primary mechanism for intent classification. Instead of explicit intent detection, the LLM determines what action to take by calling available MCP tools:

### 7.1 Tool-Based Intent

Instead of returning structured `{intent, response}`, the LLM now:
1. Analyzes the user message
2. Decides which tools to call (if any)
3. Executes tools to accomplish the user's goal
4. Provides natural language response

**Examples:**
- User: "Can you generate a PDF report?" → LLM calls `pdf_generator.generate_pdf()` tool
- User: "Schedule a daily report at 9 AM" → LLM calls `cron_job.create_cron_job()` tool
- User: "What time is it?" → LLM calls `time.get_current_time()` tool
- User: "Just chat with me" → LLM provides response without calling tools

### 7.2 Available Tools

Every message processing includes:
- **time** MCP server: Get current time and timezone info
- **cron_job** MCP server: Manage scheduled tasks
- **pdf_generator** MCP server: Generate PDF reports (NEW)
- **Custom MCPServers**: Any bot-specific servers configured in admin

### 7.3 Tool Execution Flow

```
process_inbound_message task:
  → BotMessageProcessor.process_message():
      → Build tools from all active MCPServers (custom + defaults)
      → Ollama receives system prompt + context + tools
      → run_tool_calling_loop():
          → LLM responds with potential tool calls
          → For each tool call:
              → ToolExecutor.execute_tool_call_sync()
              → Tool result appended to history
              → Loop continues if LLM wants more tools
          → Loop ends when LLM has final response
      → Return final LLM response
  → Response sent to user
```

---

## 8. Model Context Protocol (MCP) Integration - REMOVED OLD SECTION

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

Every bot automatically includes three default MCP servers without explicit configuration:

#### Time Server
- **Purpose:** Provides current time and timezone information for LLM context
- **Implementation:** `mcp_server_time` (PyPI package)
- **Transport:** LOCAL (stdio-based)
- **Command:** `python -m mcp_server_time`
- **When Used:** Added to all message processing flows
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

#### PDF Generator Server
- **Purpose:** Generates PDF reports from HTML content with advanced formatting options
- **Implementation:** Standalone module in `src/pdf_generator/` with FastMCP
- **Transport:** LOCAL (stdio-based)
- **Command:** `python -m pdf_generator`
- **When Used:** Added to all message processing flows (NEW - always available, not a separate async task)
- **Tools Available:**
  - `generate_pdf(html_content, options?)` — Convert HTML to PDF with CSS styling
- **Key Change:** PDF generation is now inline during message processing. When a user asks for a PDF, the LLM directly calls the tool instead of queuing an async task
- **Example Use Case:** User: \"Generate a PDF report\" → LLM calls `pdf_generator.generate_pdf()` with formatted HTML → Response includes PDF link/attachment

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

pdf_generator = MCPServer(
    name="pdf_generator",
    transport=MCPTransportType.LOCAL.value[0],
    command="python",
    args=["-m", "pdf_generator"],
    secrets={
        "DB_NAME": settings.DB_NAME,
        "DB_USER": settings.DB_USER,
        "DB_PASSWORD": settings.DB_PASSWORD,
        "DB_HOST": settings.DB_HOST,
        "SECRET_KEY": settings.SECRET_KEY,
    },
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
  - Task management: `manage_conversation_summary` with countdown delay for optimized scheduling

- **CronJob admin:**
  - Inline editor on Bot detail page
  - Shows name, description, cron_expression, next_run_at, last_run_at
  - Filter by active/inactive, bot
  - Uses `BaseReadOnlyUserFilteredAdmin` with explicit `has_change_permission` and `has_delete_permission` methods for better control

- **Message admin:**
  - Read-only message thread viewer (like a chat log)
  - Filterable by bot, role, created date
  - Shows content preview and embedding status

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

### 9.2 Response Validation in Admin Interface

- **Task Monitoring:** Added response validation with logging in:
  - `process_cron_job`: Validates non-empty responses before sending to Telegram
  - `process_inbound_message`: Validates responses and logs via LogFormatter for debugging
  - Uses `NO_BOT_RESPONSE` error string for null/empty response handling

### 9.3 Admin Permissions

- **Superusers:** Full access to all bots and configurations
- **Regular staff:** Can only see bots they created (`created_by`)
- **Filtering:** By creator, active status, date ranges

### 9.4 Martor Integration

Markdown editor enabled for:
- `Bot.system_prompt`
- Any other long-form text fields

Features:
- Emoji support
- Syntax highlighting
- Preview mode
- Bootstrap theme

---

## 9.5 Error Handling in Telegram Client

Enhanced error handling with fallback mechanisms:
- **Error Messages:** Include response text from Telegram API for better debugging
- **Markdown Parsing:** Fallback to plain text if markdown parsing fails
  - Catches parse entity errors and retries message as plain text
  - Ensures message delivery even if formatting fails
- **Benefit:** Improved reliability and easier troubleshooting of Telegram integration issues

---

## 10. Token Budgeting System

### 10.1 Token Allocation Ratios

The system allocates tokens across different components:

```python
ALLOCATION_RATIOS = {
    "system_prompt": 0.05,      # 5% (reduced from 10%)
    "tools": 0.15,              # 15% for tool definitions
    "context": 0.50,            # 50% for conversation context
    "response": 0.25,           # 25% for LLM output buffer
    "summary": 0.05,            # 5% for conversation summaries (NEW)
}
```

### 10.2 Character Budget for Summaries

- **Summary Allocation:** 5% of total token budget converted to character budget
- **SUMMARY_UNAVAILABLE Constant:** Used for consistent fallback messaging when summary generation fails
- **Constraint:** Character limit enforced in `SUMMARY_PROMPT` for consistent summarization
- **Integration:** `ConversationSummaryService` uses `summary_chars` from TokenBudget for proper text fitting

### 10.3 Token Measurement Improvements

- **Method Rename:** `_estimate_tool_def_tokens` → `_measure_tool_def_tokens` for accuracy
- **Tool Measurement Constants:**
  - `TOOL_CHARS_PER_TOKEN`: More accurate character-to-token conversion
  - `TOOL_SAFETY_BUFFER_TOKENS`: Safety margin for tool definitions
- **Benefit:** Improved token estimation accuracy prevents context window overflow

---

## 11. Settings Structure

### 11.1 Environment Variables

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

### 11.2 Celery Configuration

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

### 11.3 Logging Configuration

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

### 11.4 Static Files

```python
STATIC_URL = "/static/"
STATICFILES_DIRS = [os.path.join(BASE_DIR, "whimsybots/static")]
STATIC_ROOT = os.path.join(BASE_DIR, "static")
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
```

### 11.5 Martor Configuration

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

## 12. Tech Stack Summary

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

## 13. Deployment Architecture

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

### 13.4 MCP Servers in Docker

With the addition of PDF Generator as MCP Server:

```yaml
services:
  web:           # Django + Gunicorn
  celery:        # Celery worker (default queue)
  beat:          # Celery beat scheduler
  redis:         # Message broker
  postgres:      # Database
  ollama:        # Optional: Local LLM (can be external)
  pdf_generator: # NEW: PDF Generator MCP Server (optional, can run on same process)
```

---

## 14. Build Status

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
| Celery tasks | ✅ | Core tasks: telegram_msg_handler, process_inbound_message, process_cron_job, manage_conversation_summary, setup_bot_webhook, generate_embedding, regenerate_observed_patterns |
| Tool calling loop | ✅ | Async tool execution with Ollama via tool_calling_coordinator |
| Report generation | ✅ | MCP Server-based PDF generation called directly by LLM (no separate async task) |
| Cron job processing | ✅ | Dedicated process_cron_job task with CronJob.description support |
| Default MCP servers | ✅ | Time, Cron Job Manager, PDF Generator (FastMCP) - always available, not persisted |
| Cron Job MCP Server | ✅ | Standalone module with list/create/update/delete tools (FastMCP) |
| Multi-queue Celery | ✅ | beat + default queues |
| Comprehensive logging | ✅ | JSON logging configured with LogFormatter for descriptions |
| Async database | ✅ | asyncpg for cron_job MCP server database access |
| Encryption for secrets | ✅ | EncryptedCharField for api_key, EncryptedJSONField for MCPServer.secrets |
| Telegram rate limiting | ✅ | Rate limit retry handling with exponential backoff in tasks |
| Message splitting | ✅ | Intelligent message splitting for responses > 4096 chars |
| Typing indicators | ✅ | Responsive typing indicators during processing |
| Conversation context management | ✅ | ConversationSummaryService with manage_conversation_summary task and countdown delay |
| Celery Flower monitoring | ✅ | Monitoring service in docker-compose with persistent volume |
| Token budgeting with summaries | ✅ | 5% allocation for conversation summaries with character limits |
| PDF Generator MCP Server | ✅ | Standalone MCP server for PDF generation with FastMCP framework |
| Enhanced Telegram error handling | ✅ | Response text in errors, markdown parsing fallback to plain text |
| Response validation | ✅ | Null/empty response checks in process_cron_job and process_inbound_message tasks |
| Code organization | ✅ | Alphabetically organized imports across all modules for consistency |

---

## 15. Key Design Decisions

### 15.1 UUID Primary Keys
- **Why:** Security through obscurity, distributed system friendly
- **Trade-off:** Slightly larger indexes, less human-readable

### 15.2 Async Tool Calling
- **Why:** MCP client requires async/await for stdio/HTTP connections
- **Implementation:** `asyncio.run()` in synchronous Celery tasks

### 15.3 Tool-Based Intent
- **Old Design:** StructuredOutput with explicit intent classification in LLM response
- **Current Design:** Intent is implicit in which tools the LLM calls
- **Why Changed:** 
  - Tool-based intent is more flexible and powerful
  - Aligns with MCP design philosophy (actions via tools, not metadata)
  - Simpler to maintain (no intent enum sync needed)
  - LLM naturally chooses best tool for the task
- **Benefit:** Better intent coverage through tool combinations (e.g., create_cron_job + send_notification)

### 15.4 Default MCP Servers
- **Why:** Provide core functionality (time, cron management, pdf generator) without manual setup
- **Instantiation:** Temporary in-memory MCPServer objects created per message
- **Benefit:** Always available, extensible with custom MCPServers

### 15.5 Multi-Queue Celery
- **Why:** Isolate beat scheduling from worker processing
- **Benefit:** Prevents worker overload from affecting scheduler

### 15.6 Service Layer Pattern
- **Why:** Clean separation of concerns, testability
- **Structure:** Tasks → Services → Clients → External APIs

### 15.7 Tool Calling Abstraction
- **Why:** Unified interface for MCP tools regardless of transport
- **Benefit:** Easy to add new MCP servers without code changes

### 15.8 CronJob with Description
- **Why:** LLM needs context about what each scheduled job does
- **Usage:** Description passed to `process_cron_job` in CRON_JOB_PROMPT with bot_id for context
- **Benefit:** LLM can execute task semantically correct without hardcoded logic

### 15.9 PDF Generation via MCP Server (Inline Tool Calling)
- **Why:** Inline PDF generation eliminates async task overhead and provides instant feedback
- **Old Design:** Separate async `generate_report` task triggered by intent detection
- **Current Design:** 
  - PDF generator is a default MCP server available in all flows
  - LLM calls `pdf_generator.generate_pdf()` directly when needed
  - Results included in same response (no task queueing)
- **Benefits:**
  - Faster user experience (no task queue delays)
  - Simpler architecture (no report-specific task)
  - Better modularity (PDF server runs independently)
  - More flexible (LLM can call PDF tool any time, not just on REPORT intent)

### 15.10 Token Budget with Summary Allocation
- **Why:** Prevent context window overflow when accumulating summaries
- **Implementation:** Dynamic character budget allocation based on token constraints
- **Benefit:** Consistent conversation length management with automatic summarization

### 15.11 Selective MCP Server Inclusion
- **Why:** Different flows need different tools (e.g., reports don't need cron_job management)
- **Implementation:** Conditional server inclusion based on processing context
- **Benefit:** Reduced tool noise, faster LLM processing, clearer user intent

### 15.12 Removal of Async Task-Based Report Generation (DESIGN SHIFT)
- **Why:** Inline tool calling is faster and simpler than task queuing
- **Trade-off:**
  - **Removed:** Separate async task, no background processing
  - **Gained:** Instant results, simpler code, fewer moving parts
- **Implementation:** Report generation now happens via MCP tool call during message processing
- **When to Use:** For operations that can complete synchronously (like PDF generation)
- **Limitation:** Very long-running PDFs might block message response (mitigated by LLM timeout controls)
