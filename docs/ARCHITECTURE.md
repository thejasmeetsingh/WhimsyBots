# Architecture & Data Models v3.0

> **Documentation Map**
> - [README](../README.md) — Project overview, features, and quick start
> - [DEVELOPMENT](DEVELOPMENT.md) — Contributor onboarding, project layout, coding conventions
> - [TROUBLESHOOTING](TROUBLESHOOTING.md) — Common issues and resolutions

---

## 1. What We're Building

A self-hosted, Django-based AI agent platform where users can create and configure AI-powered "apps" (e.g. a Journaling Agent, a Research Assistant, a Daily Briefing bot) through an admin panel. Each app runs on a schedule, communicates with users via Telegram, processes replies using a local LLM (Ollama), can generate rich PDF reports on demand, and integrates with external tools via the Model Context Protocol (MCP).

The system now also supports **semantic memory** via pgvector — past user messages, MCP server tool descriptions, and cron job schedules are all embedded and used to retrieve contextually relevant information at inference time.

---

## 2. High-Level Architecture

```text
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
   └──────┬──────┘  └─────┬───────┘  └──────┬──────┘
          │               │                  │
   ┌──────▼──────┐ ┌──────▼──────┐   ┌───────▼──────┐
   │    Redis     │ │ pgvector     │   │  Telegram    │
   │   (Broker)   │ │ (Embeddings) │   │  Polling     │
   └─────────────┘ └─────────────┘   └─────────────┘
```

### Request Flows

#### Outbound (Scheduled Cron Job)
```text
Celery Beat (every minute)
  → cron_job_poller finds active CronJobs with next_run_at <= now
  → Refreshes stale schedule_embedding for each job (semantic ranking)
  → Queue process_cron_job task for each due job (ETA = next_run_at)
  → process_cron_job task:
      → Fetches CronJob instance (name, description, schedule_embedding)
      → BotMessageProcessor.process_cron_job(cron_job):
          → _get_tools_config() re-ranks bot MCPServers by schedule_embedding
            cosine distance, excludes "cron_job" server, adds default servers
          → Trims to context.budget.recommended_tool_count
          → Runs Ollama tool_calling_loop with CRON_JOB_PROMPT
          → LLM executes task with tool access
      → Response sent to user via TelegramClient.send_message()
      → Updates CronJob.next_run_at and last_run_at
```

#### Inbound (User Telegram Message)
```text
User sends message via Telegram
  → Webhook receives update at /webhook/{bot_token}/
  → telegram_msg_handler task queued
  → TelegramUpdateHandler.handle_update():
      → Stores message to DB (role=USER)
      → Sends typing indicator
      → Queue generate_embedding task (saves content_embedding, then
        enqueues process_inbound_message — centralizes the dispatch)
  → process_inbound_message task:
      → BotMessageProcessor.process_message():
          → _get_tools_config() re-ranks bot MCPServers by message
            content_embedding (cosine distance), adds default servers
            (time, cron_job, pdf_generator)
          → ContextAssembler.assemble() builds system prompt:
              [system prompt + observed patterns + relevant memories] +
              [top-k relevant memories from pgvector] + history
          → Trims to context.budget.recommended_tool_count
          → Runs Ollama tool_calling_loop with tool calling format
          → LLM can call any available MCP tools including pdf_generator
          → Returns: response string (intent detection is implicit in
            tool usage)
      → Stores response to DB (role=ASSISTANT)
      → Sends response via TelegramClient.send_message()
```

---

## 3. Django App Structure

```text
WhimsyBots/
  src/
    whimsybots/              ← Django project settings, Celery config, URLs
      settings/
        __init__.py
        base.py              ← Production settings (DJANGO_SETTINGS_MODULE default)
        test.py              ← Test settings — SQLite + fakeredis + pgvector shims
      celery.py              ← Celery app and task routing setup
      urls.py                ← URL routing and webhook endpoints
      views.py               ← Telegram webhook view
      wsgi.py                ← WSGI application entry point

    app/                     ← Core application
      models.py              ← All data models (Bot, Message, CronJob, Log, Ollama, MCPServer)
      tasks.py               ← Celery tasks (orchestration only — helpers in utils.tasks)
      choices.py             ← Enum-based choices (message roles, transports)
      fields.py              ← Custom Django fields (encrypted)
      validators.py          ← Custom validators (cron, transport, keep_alive)
      forms.py               ← Django forms
      admin.py               ← Django admin configuration
      apps.py                ← Django app configuration
      migrations/
        0001_enable_pgvector.py
        0002_initial.py      ← Includes new vector fields

    managers/
        telegram_client.py   ← Telegram client manager
        ollama_config.py     ← Ollama config manager

    services/                ← Business logic services
        bot_processor.py             ← BotMessageProcessor (process_message, process_cron_job)
        context_assembler.py         ← Builds system prompt + history with budget
        conversation_summary.py      ← Conversation summarization service
        embedding.py                 ← EmbeddingService (messages, cron schedules, MCP tools)
        observed_patterns.py         ← Pattern observation and regeneration
        rate_limiter.py              ← Rate limiting for external APIs
        telegram_update_handler.py   ← Incoming Telegram update handler
        token_budget.py              ← TokenBudgetService — priority-tier context budgeting
        tool_calling_coordinator.py  ← Ollama tool calling loop
        tool_executor.py             ← MCP tool execution (ToolExecutor, MCPToolsBuilder)

    clients/                 ← External API clients
      telegram.py            ← Telegram Bot API client
      ollama.py              ← Ollama LLM client (chat + embeddings)
      mcp.py                 ← Model Context Protocol client

    cron_job/                ← Standalone Cron Job MCP Server (FastMCP)
      __init__.py
      __main__.py            ← Entry point (python -m cron_job)
      server.py              ← MCP server definition with tools
      db.py                  ← Async database session management
      models.py              ← SQLAlchemy models (mirrors app.models.CronJob)
      helpers.py             ← Cron parsing, validation

    pdf_generator/           ← Standalone PDF Generator MCP Server (FastMCP)
      __init__.py
      __main__.py            ← Entry point (python -m pdf_generator)
      server.py              ← MCP server definition with PDF tools
      db.py                  ← Async database session management
      helpers.py             ← HTML parsing, PDF rendering

    utils/                   ← Cross-cutting helpers (extracted from app/utils.py)
      __init__.py
      crypto.py              ← get_token_hash, get_fernet, encryption helpers
      formatting.py          ← convert_messages_to_ollama_format, get_admin_link
      scheduling.py          ← calculate_next_run_at (croniter wrapper)
      tasks.py               ← Shared Celery helpers (get_ollama_cfg, create_log,
                               get_bot_obj / get_cron_obj / get_msg_obj,
                               generate_message_embedding, generate_cron_job_embedding,
                               generate_mcp_embedding, log_task_failure, etc.)
      telegram.py            ← Telegram response sanitization helpers
      text.py                ← Message splitting (>4096 char), sanitization

    static/                  ← Static files (admin, martor, plugins)

  tests/                     ← Comprehensive pytest suite (NEW)
    conftest.py              ← Root pytest fixtures (DB, fakeredis, etc.)
    test_app/                ← tests for app/ (choices, fields, validators, models, admin, forms, tasks)
    test_clients/            ← tests for clients/ (telegram, ollama, mcp)
    test_cron_job/           ← tests for cron_job/ (server, db, helpers)
    test_managers/           ← tests for managers/ (telegram_client, ollama_config)
    test_pdf_generator/      ← tests for pdf_generator/ (server, helpers)
    test_services/           ← tests for services/ (bot_processor, context_assembler,
                              embedding, observed_patterns, telegram_update_handler,
                              tool_calling_coordinator, tool_executor,
                              conversation_summary, rate_limiter, token_budget)
    test_utils/              ← tests for utils/ (crypto, formatting, scheduling, telegram, text, tasks)
    test_whimsybots/         ← tests for project-level modules (views)

  manage.py
  conftest.py                ← Top-level pytest config (renamed from configtest.py)
  pytest.ini                 ← pytest settings (DJANGO_SETTINGS_MODULE = whimsybots.settings.test)
  prompts.py                 ← LLM prompt templates — DEFAULT_SYSTEM_PROMPT now
                              includes inlined "Skills" section, CRON_JOB_PROMPT
  strings.py                 ← Error messages and user-facing strings
  requirements.txt           ← Python deps (incl. pytest, pytest-django, pytest-mock,
                              pytest-asyncio, fakeredis, pgvector)
  docker-compose.yml
  Dockerfile
  Makefile                   ← Added test, test-verbose, test-coverage, test-specific
                              targets; env, dev-setup, install-deps, build-test
  gunicorn.conf.py           ← Gunicorn WSGI server configuration

.github/
  workflows/
    master.yml               ← CI: runs pytest on PR/push to master (NEW)
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
    endpoint            = models.URLField(default="http://localhost:11434")
    api_key             = EncryptedCharField(null=True, blank=True)             # Encrypted
    temperature         = models.FloatField(default=0.7)
    num_ctx             = models.PositiveIntegerField(default=4096,
                                                       validators=[MinValueValidator(4096)])
    keep_alive          = models.CharField(max_length=10, default="10m")        # Validated
    num_predict         = models.PositiveIntegerField(null=True, blank=True)
    # Note: default_model was removed in an earlier iteration — bots now carry their own ollama_model.
```

#### `Bot`
The central model. Every agent/bot the user creates is a Bot instance.

```python
class Bot(BaseModel):
    created_by              = models.ForeignKey(User, on_delete=models.CASCADE)
    name                    = models.CharField()
    description             = models.TextField(null=True, blank=True)
    is_active               = models.BooleanField(default=True)

    # LLM Configuration
    ollama_model            = models.CharField(max_length=50)
    embedding_model         = models.CharField(max_length=50)            # NEW — per-bot embedding model
    embedding_dimensions    = models.PositiveIntegerField(default=768)   # NEW — must match embedding model
    system_prompt           = MartorField(null=True, blank=True)          # Markdown support

    # Personalization
    observed_patterns       = models.TextField(null=True, blank=True)    # Async-generated user profile

    # Telegram Communication
    telegram_bot_token      = EncryptedCharField()                       # Encrypted
    telegram_bot_token_hash = models.CharField(max_length=64, unique=True)  # For token lookup
    telegram_chat_id        = models.CharField(max_length=255, null=True)   # Auto-populated on first message

    def save(self, *args, **kwargs):
        # Computes telegram_bot_token_hash deterministically (utils.crypto.get_token_hash)
        # so lookups never touch the encrypted plaintext.
        ...
```

#### `MCPServer`
Model Context Protocol servers connected to a bot for extended functionality.

```python
class MCPServer(BaseModel):
    bot                      = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='mcp_servers')
    name                     = models.CharField(max_length=100)
    transport                = models.CharField(max_length=1, choices=MCPTransportType.get_values())  # 'L' or 'R'
    command                  = models.CharField(max_length=10, null=True, blank=True)  # For LOCAL: python, npx, uv
    endpoint                 = models.URLField(null=True, blank=True)               # For REMOTE: HTTPS URL
    args                     = ArrayField(base_field=models.CharField(max_length=500), default=list)
    secrets                  = EncryptedJSONField(default=dict, null=True, blank=True)  # Encrypted env vars / headers
    is_active                = models.BooleanField(default=True)
    tools_description_embedding = VectorField(null=True, blank=True)               # NEW — semantic tool ranking
```

The `tools_description_embedding` is generated from concatenated tool descriptions (`name: description`) via `EmbeddingService.save_mcp_embedding()` and used by `BotMessageProcessor._get_tools_config()` to re-rank MCP servers by cosine similarity to the current message/schedule embedding.

#### `Message`
Every message in a conversation — inbound and outbound.

```python
class Message(BaseModel):
    bot                 = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='messages')
    role                = models.CharField(max_length=1, choices=MessageRole.get_values())  # 'S', 'U', 'A'
    content             = models.TextField()
    content_embedding   = VectorField(null=True, blank=True)  # For similarity search & context retrieval
```

**Note:** Intent field has been removed. Intent classification is now implicit in tool selection during message processing.

#### `CronJob`
Scheduled job definition for bot execution using cron expressions. Each job has a name and description that define what the bot should do when executed.

```python
class CronJob(BaseModel):
    bot                          = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='bot_cron_jobs')
    name                         = models.CharField(max_length=100)        # Short job label
    description                  = models.TextField()                       # Detailed description
    cron_expression              = models.CharField(max_length=100,
                                                    validators=[validate_cron_expression])
    next_run_at                  = models.DateTimeField()
    last_run_at                  = models.DateTimeField(null=True, blank=True)
    is_active                    = models.BooleanField(default=True)
    schedule_embedding           = VectorField(null=True, blank=True)       # NEW — semantic intent ranking
    schedule_embedding_updated_at = models.DateTimeField(null=True, blank=True)  # NEW — staleness tracker
```

The `schedule_embedding` is generated from `f"{name}. {description}"` and refreshed whenever `cron_job_poller` notices it is missing or older than the job's `updated_at`. It allows the bot processor to rank MCP tools by relevance to the schedule's intent.

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
    LOCAL  = ('L', 'Local')    # Stdio-based (command + args)
    REMOTE = ('R', 'Remote')   # HTTP-based (URL + headers)
```

---

## 5. Celery Architecture

### 5.1 Queue Structure

The system uses a multi-queue architecture for optimal task routing:

```python
CELERY_QUEUES = {
    "beat":    {"exchange": "beat", "routing_key": "beat"},      # Scheduled tasks
    "default": {"exchange": "default", "routing_key": "default"} # All other tasks
}
```

### 5.2 Shared Utilities (`utils/tasks.py`)

All Celery helpers were extracted into `utils.tasks` so `app/tasks.py` can stay focused on orchestration. The shared module exposes:

- **Constants:** `MAX_RETRIES`, `RETRY_BASE_SECONDS`, `DEFAULT_QUEUE`, `TELEGRAM_ALLOWED_UPDATES`
- **Object lookups:** `get_ollama_cfg()`, `get_bot_obj()`, `get_cron_obj()`, `get_msg_obj()`
- **Logging:** `create_log()`, `log_task_failure()`, `build_error_description()`
- **Embedding dispatch:** `generate_message_embedding()`, `generate_cron_job_embedding()`, `generate_mcp_embedding()`

### 5.3 Scheduled Tasks (Beat Queue)

#### `cron_job_poller`
Runs every minute via Celery Beat. Checks for due cron jobs, refreshes stale embeddings, and queues jobs for execution.

```python
@celery.task(bind=True, max_retries=MAX_RETRIES)
def cron_job_poller(self) -> None:
    # 1. Check Ollama configuration exists
    # 2. Find active CronJobs with next_run_at <= now (and bot is active, has chat_id)
    # 3. For each due job:
    #    a. _refresh_cron_job_embedding_if_stale() — regenerate schedule_embedding
    #       if missing or older than the job's updated_at
    #    b. _dispatch_cron_job() — apply_async(eta=next_run_at)
    # 4. Exponential backoff retry on failure
```

**Schedule:** Every minute (`crontab(minute="*")`)

### 5.4 Message Processing Tasks (Default Queue)

#### `telegram_msg_handler`
Handles incoming Telegram webhook updates by fetching the bot and calling `TelegramUpdateHandler`.

```python
@celery.task(bind=True, max_retries=MAX_RETRIES)
def telegram_msg_handler(self, bot_token: str, update: dict) -> None:
    # 1. Lookup bot by telegram_bot_token_hash (utils.crypto.get_token_hash)
    # 2. TelegramUpdateHandler.handle_update(bot, update)
    #    - Persists message (role=USER)
    #    - Sends typing action
    #    - Enqueues generate_embedding (which then enqueues process_inbound_message)
    # 3. Handles Telegram rate limit errors with retry + exponential backoff
    # 4. Logs comprehensive task information
```

#### `generate_embedding` (NEW — centralizes dispatch)
Generates the message embedding and then enqueues the next pipeline step.

```python
@celery.task(bind=True, max_retries=MAX_RETRIES)
def generate_embedding(self, message_id: str) -> None:
    # 1. Fetch message + Ollama cfg
    # 2. EmbeddingService.save_message_embedding(message)
    # 3. Enqueue process_inbound_message with the freshly-saved content_embedding
    #    so semantic ranking can use it.
    # 4. Retry with exponential backoff on failure
```

> **Why centralize?** The handler no longer imports `process_inbound_message` — it only triggers embedding, which then triggers processing. This removes a circular coupling and makes the embedding pipeline reusable for cron embeddings too.

#### `process_inbound_message`
Main message processing pipeline with tool calling loop.

```python
@celery.task(bind=True, max_retries=MAX_RETRIES)
def process_inbound_message(self, bot_id: str, msg_id: str) -> None:
    # 1. Validate Ollama configuration
    # 2. Fetch bot and message from database
    # 3. Initialize BotMessageProcessor
    # 4. BotMessageProcessor.process_message():
    #    - _get_tools_config(query_vector=message.content_embedding)
    #      → bot MCPServers re-ranked by tools_description_embedding distance,
    #        default servers appended, all filtered
    #    - ContextAssembler.assemble() builds system prompt + history
    #    - tools trimmed to context.budget.recommended_tool_count
    #    - Sends responsive typing indicator during processing
    #    - Runs Ollama tool_calling_loop with all available tools
    #    - LLM decides which tools to call (if any)
    #    - Returns final response string
    # 5. Send response via Telegram with intelligent message splitting (>4096 chars)
    # 6. Queue manage_conversation_summary task if context window approaching limit
    # 7. Conditionally regenerate observed patterns if threshold met
    # 8. Handles Telegram rate limit errors with retry + exponential backoff
```

#### `process_cron_job`
Processes a scheduled cron job execution. Now takes a `CronJob` instance (not `name`/`description`) so it can use the stored embedding.

```python
@celery.task(bind=True, max_retries=MAX_RETRIES)
def process_cron_job(self, job_id: str) -> None:
    # 1. Fetch CronJob by ID
    # 2. Validate Ollama configuration
    # 3. BotMessageProcessor.process_cron_job(cron_job):
    #    - _get_tools_config(
    #          query_vector=cron_job.schedule_embedding,
    #          servers_to_exclude={"cron_job"}
    #      )
    #    - Runs tool_calling_loop with CRON_JOB_PROMPT
    #    - LLM executes the task
    # 4. Send response via Telegram
    # 5. Update CronJob.next_run_at (via croniter) and last_run_at
```

#### `manage_conversation_summary`
Manages conversation context window by summarizing old messages when context approaches limit.

```python
@celery.task(bind=True, max_retries=MAX_RETRIES)
def manage_conversation_summary(self, bot_id: str) -> None:
    # 1. Fetch recent conversation messages for bot
    # 2. Check if context window is approaching limit
    # 3. Initialize ConversationSummaryService
    # 4. Generate summary of old messages via Ollama
    # 5. Replace old messages with summary message
    # 6. Maintain recent messages for immediate context
```

#### `setup_bot_webhook`
Sets up Telegram webhook for a bot during activation.

```python
@celery.task(bind=True, max_retries=MAX_RETRIES)
def setup_bot_webhook(self, bot_id: str) -> None:
    # 1. Fetch bot configuration
    # 2. Construct webhook URL: {WEBHOOK_BASE_URL}/webhook/{bot_token}/
    # 3. Register webhook with Telegram API
```

### 5.5 Retry Strategy

All tasks use exponential backoff retry:

- **Max retries:** `MAX_RETRIES` (3)
- **Backoff:** `RETRY_BASE_SECONDS * 2**retries` → 60s, 120s, 240s
- **Telegram rate limits:** respected via `retry_after` from API

---

## 6. Telegram Integration

### 6.1 Communication Modes

**Webhook (Recommended):** Telegram pushes updates to `/webhook/{bot_token}/` endpoint

### 6.2 Webhook View

The webhook view lives in [`src/whimsybots/views.py`](src/whimsybots/views.py) and is registered via [`src/whimsybots/urls.py`](src/whimsybots/urls.py). It validates the request payload, extracts the bot token from the URL, and queues `telegram_msg_handler` for async processing.

```python
class TelegramWebhook(View):
    def post(self, request, *args, **kwargs):
        # 1. Validate token + payload
        # 2. telegram_msg_handler.apply_async(queue="default", kwargs={...})
        # 3. Return 200 immediately (Telegram requires response within 23s)
```

### 6.3 Telegram Client

Located in [`src/clients/telegram.py`](src/clients/telegram.py):

```python
class TelegramClient:
    def __init__(self, token: str, chat_id: str = None):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    def send_message(self, text: str, parse_mode: str = "Markdown") -> dict:
        # Auto-splits messages > 4096 chars (utils.text / utils.telegram)
        # Supports Markdown, HTML, or plain text
        # Falls back to plain text on Markdown parse errors

    def send_typing_action(self) -> dict:
        # Shows "typing..." indicator

    def set_webhook(self, url: str, allowed_updates: list) -> dict:
        # Registers webhook with Telegram
```

### 6.4 Telegram Client Manager

Abstraction layer for creating clients:

```python
class TelegramClientManager:
    @staticmethod
    def create_client(bot) -> TelegramClient:
        return TelegramClient(bot.telegram_bot_token, bot.telegram_chat_id)
```

### 6.5 Update Handler

Service for processing incoming updates:

```python
class TelegramUpdateHandler:
    @staticmethod
    def handle_update(bot: Bot, update: dict):
        # 1. Parse update (extract message_id, chat_id, text)
        # 2. Update bot.telegram_chat_id if not set
        # 3. Create Message record (role=USER)
        # 4. Send typing action (responsive to LLM processing time)
        # 5. Queue generate_embedding task (which then queues process_inbound_message)
        # 6. Handle Telegram rate limits gracefully
```

### 6.6 Message Flow

#### Inbound (User → Bot)
```text
Telegram User Message
  → Webhook → TelegramWebhook view
  → telegram_msg_handler task
  → TelegramUpdateHandler.handle_update()
  → Message saved to DB (role=USER)
  → Typing indicator sent (responsive)
  → generate_embedding task queued
      → EmbeddingService.save_message_embedding(message)
      → process_inbound_message task queued (with fresh content_embedding)
  → BotMessageProcessor.process_message():
      → _get_tools_config re-ranks MCP servers by content_embedding similarity
      → ContextAssembler builds system prompt + history within budget
      → tools trimmed to budget.recommended_tool_count
      → LLM calls tools as needed (implicit intent through tool usage)
  → Response sent via TelegramClient.send_message() (intelligent splitting)
  → Response saved to DB (role=ASSISTANT)
  → If context window near limit: manage_conversation_summary task queued
  → If pattern update needed: regenerate_observed_patterns task queued
  → If Telegram rate limited: retry with exponential backoff
```

#### Outbound (Scheduled Cron Job)
```text
Celery Beat (every minute)
  → cron_job_poller checks all active CronJobs
  → For each due job:
      → _refresh_cron_job_embedding_if_stale() — regenerates schedule_embedding
        if missing or older than updated_at
      → process_cron_job.apply_async(eta=next_run_at)
  → process_cron_job task:
      → Fetch CronJob (carries schedule_embedding for tool ranking)
      → BotMessageProcessor.process_cron_job(cron_job):
          → _get_tools_config(query_vector=schedule_embedding,
                              servers_to_exclude={"cron_job"})
          → Runs tool_calling_loop with CRON_JOB_PROMPT
          → LLM executes task with access to tools
      → Response sent via Telegram
      → Updates CronJob.next_run_at and last_run_at
```

---

## 7. Tool Calling with MCP Integration

Tool calling is the primary mechanism for intent classification. Instead of explicit intent detection, the LLM determines what action to take by calling available MCP tools.

### 7.1 Tool-Based Intent

Instead of returning structured `{intent, response}`, the LLM now:

1. Analyzes the user message (or cron schedule)
2. Decides which tools to call (if any)
3. Executes tools to accomplish the user's goal
4. Provides natural language response

**Examples:**

- User: "Can you generate a PDF report?" → LLM calls `pdf_generator.generate_pdf()` tool
- User: "Schedule a daily report at 9 AM" → LLM calls `cron_job.create_cron_job()` tool
- User: "What time is it?" → LLM calls `time.get_current_time()` tool
- User: "Just chat with me" → LLM provides response without calling tools

### 7.2 Semantic Tool Ranking (NEW)

`BotMessageProcessor._get_tools_config()` is a single helper used by both `process_message` and `process_cron_job`. It accepts an optional `query_vector` and ranks the bot's active `MCPServer` records by **cosine distance** of their `tools_description_embedding` to the query vector:

```python
def _get_tools_config(
    self,
    query_vector: Optional[list[float]] = None,
    servers_to_exclude: Optional[set[str]] = None,
) -> list[MCPToolConfig]:
    mcp_servers = MCPServer.objects.filter(bot_id=self.bot.id, is_active=True)

    if query_vector is not None:
        mcp_servers = mcp_servers.annotate(
            distance=CosineDistance("tools_description_embedding", query_vector)
        ).order_by("distance")

    # Append default servers (time, cron_job, pdf_generator)
    # Filter out excluded servers (e.g. {"cron_job"} for cron-driven runs)
    # Discover tools via MCPToolsBuilder
    ...
```

After ranking, the list is trimmed to `context.budget.recommended_tool_count` so the LLM is only offered tools that fit in the configured `num_ctx`.

### 7.3 Available Tools

Every message processing includes:

- **time** MCP server — Get current time and timezone info
- **cron_job** MCP server — Manage scheduled tasks (excluded from cron job runs)
- **pdf_generator** MCP server — Generate PDF reports
- **Custom MCPServers** — Any bot-specific servers configured in admin

### 7.4 Tool Execution Flow

```text
process_inbound_message / process_cron_job task:
  → BotMessageProcessor.process_message() / process_cron_job():
      → _get_tools_config(query_vector=...) → re-ranks, appends defaults
      → ContextAssembler.assemble() → builds system prompt + history within budget
      → Trim tools to context.budget.recommended_tool_count
      → Ollama receives system prompt + history + tools
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

## 8. Model Context Protocol (MCP) Integration

The system integrates with external tools and services via MCP, supporting both local and remote servers.

### 8.1 Architecture

```text
Bot
  └── MCPServer (1:many)
        └── MCPClient
              └── Tools (discovered dynamically)
```

### 8.2 MCP Client

Located in [`src/clients/mcp.py`](src/clients/mcp.py):

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

### 8.6 Default MCP Servers

Every bot automatically includes three default MCP servers without explicit configuration:

#### Time Server

- **Purpose:** Provides current time and timezone information for LLM context
- **Implementation:** `mcp_server_time` (PyPI package)
- **Transport:** LOCAL (stdio-based)
- **Command:** `python -m mcp_server_time`
- **When Used:** Added to all message processing flows

#### Cron Job Manager Server

- **Purpose:** Allows LLM to view, create, update, and delete cron jobs
- **Implementation:** Standalone module in [`src/cron_job/`](src/cron_job/) with FastMCP
- **Transport:** LOCAL (stdio-based)
- **Command:** `python -m cron_job`
- **Database:** Shares main PostgreSQL database (via environment secrets)
- **When Used:** Added to message processing flows; **excluded** from cron job execution to avoid loops
- **Tools Available:**
  - `list_cron_jobs(bot_id, is_active=None)`
  - `create_cron_job(bot_id, name, description, cron_expression)`
  - `update_cron_job(id, bot_id, name?, description?, cron_expression?, is_active?)`
  - `delete_cron_job(id, bot_id)`

#### PDF Generator Server

- **Purpose:** Generates PDF reports from HTML content with advanced formatting options
- **Implementation:** Standalone module in [`src/pdf_generator/`](src/pdf_generator/) with FastMCP
- **Transport:** LOCAL (stdio-based)
- **Command:** `python -m pdf_generator`
- **When Used:** Added to all message processing flows
- **Tools Available:**
  - `generate_pdf(html_content, options?)` — Convert HTML to PDF with CSS styling

### 8.7 Server Instantiation

Default servers are constructed via `MCPServer.get_default_mcp_servers()` (static method), which returns a dict of **temporary, in-memory** `MCPServer` instances that are not persisted to the database but reused for the duration of a single message-processing cycle.

```python
@staticmethod
def get_default_mcp_servers() -> dict[str, "MCPServer"]:
    return {
        "cron_job": MCPServer(
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
        ),
        "time": MCPServer(
            name="time",
            transport=MCPTransportType.LOCAL.value[0],
            command="python",
            args=["-m", "mcp_server_time"],
        ),
        "pdf_generator": MCPServer(
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
        ),
    }
```

---

## 9. Token Budgeting System (REWRITTEN)

The token budget system treats the context window like OS RAM. Each tier has a fixed ratio, and content is truncated in priority order when the budget is exceeded.

### 9.1 Allocation Tiers (NEW)

```python
ALLOCATION_RATIOS = {
    "history":         0.30,  # Tier 1: recency context
    "embeddings":      0.25,  # Tier 1: semantic memory (equal to history)
    "tool_responses":  0.25,  # Tier 2: live data from MCP tools
    "system_prompt":   0.10,  # Tier 3: user-authored prompt + inline skills
    "patterns":        0.05,  # Tier 3: behavioral profile
    "summary":         0.05,  # Tier 3: past conversation summary
}
# Total = 1.0
```

The previous separate `SKILLS_RESERVED_TOKENS` carve-out (5%) was removed when skills were inlined into the system prompt (see [§ 10](#10-prompts--skills-block-inline)).

### 9.2 Truncation Priority

From most protected to truncated first:

1. **Patterns** — capped at 4096 chars during generation; budget acts as safety net
2. **System prompt** — truncate from bottom (preserves opening intent)
3. **History** — drop oldest messages first
4. **Embeddings** — drop lowest-similarity results first
5. **Tool responses** — truncated most aggressively (verbose, LLM-generated)

### 9.3 Token Measurement Constants

```python
CHARS_PER_TOKEN: float            = 3.5   # General prose estimate
TOOL_CHARS_PER_TOKEN: float       = 2.5   # JSON is denser than prose
TOOL_SAFETY_BUFFER_TOKENS: int    = 50    # Tokenizer variance safety margin
FIXED_OVERHEAD_TOKENS: int        = 200   # StructuredOutput + base instructions
DEFAULT_OUTPUT_RESERVATION_TOKENS: int = 512  # Used when num_predict is unset
TOOL_DEF_AVG_TOKENS: int          = 150   # Avg MCP tool definition size
```

### 9.4 `recommended_tool_count` (NEW)

`TokenBudgetService.compute()` returns a `TokenBudget` Pydantic model with a `recommended_tool_count` field — the maximum number of MCP tool definitions that can safely fit in the remaining headroom after every other reservation. `BotMessageProcessor` uses this to cap the tools actually passed to the tool-calling loop, ensuring partial tool schemas never enter the truncation pool (which would cause Ollama validation errors).

```python
class TokenBudget(BaseModel):
    num_ctx: int
    output_reservation: int
    tool_def_tokens: int             # Measured (not estimated)
    fixed_overhead_tokens: int
    usable_tokens: int

    recommended_tool_count: int      # NEW — cap for tools_config[:N]

    history_tokens: int
    embedding_chars: int
    tool_response_chars: int
    system_prompt_chars: int
    patterns_chars: int
    summary_chars: int

    allocation_breakdown: dict[str, int]
```

### 9.5 Fitting Strategies

- **Messages:** Walk backwards from newest → oldest until budget exhausted
- **Embeddings:** Walk from highest similarity → lowest until char budget exhausted
- **Tool responses:** Hard truncate to `tool_response_chars` with a `[...response truncated]` suffix

### 9.6 Logged Diagnostics

Every budget computation logs a structured JSON summary including `num_ctx`, `usable_tokens`, each allocation in chars/tokens, and `recommended_tool_count`.

---

## 10. Prompts & Skills Block (Inline)

The standalone `TIME_MCP_SKILL`, `CRON_JOB_SKILL`, `REPORT_GENERATION_SKILL` constants and `SkillsRegistry` were removed. Their content is now folded directly into `DEFAULT_SYSTEM_PROMPT` under a "Skills" section, formatted at assembly time by `ContextAssembler` via `str.format`:

```python
# prompts.py
DEFAULT_SYSTEM_PROMPT = """
{system_prompt}

---

## Skills
- Use the `time` tool when the user asks about dates, schedules, or relative time.
- Use the `cron_job` tool to list / create / update / delete scheduled jobs for this bot.
- Use the `pdf_generator` tool to convert rich HTML into a PDF report.
...

---

## Conversation Summary (Older Context)
{summary}

Bot ID: {bot_id}
Timezone: {timezone}
"""
```

This change has two benefits:

1. **Skills participate in normal system-prompt truncation** when context is tight, rather than occupying a fixed 5% reservation
2. **One fewer module** to maintain — the old `services/skills_registry.py` was deleted

`ContextAssembler.assemble()` calls `.format(system_prompt=..., summary=..., bot_id=..., timezone=settings.TIME_ZONE)`.

---

## 11. Context Assembly Pipeline

`ContextAssembler` is the single place where all context sources are gathered, truncated, and assembled in priority order.

### 11.1 Assembly Order (top → bottom in system prompt)

1. **Bot system prompt** — user-authored, truncated from bottom if needed (includes inline skills block)
2. **Observed patterns** — async-generated user behavior profile, protected
3. **Relevant memories** — top-k embedding results, lowest-similarity dropped first

Conversation history is passed separately as the messages array, with oldest messages dropped first when budget is tight.

### 11.2 Source Pipeline

```text
ContextAssembler.assemble(tool_definitions):
  1. Fetch recent messages (cap at RECENT_MESSAGES_CAP = 200)
  2. Retrieve summary (role=SYSTEM) or fall back to SUMMARY_UNAVAILABLE
  3. TokenBudgetService.compute(ollama, tool_definitions) → TokenBudget
  4. Truncate system prompt (DEFAULT_SYSTEM_PROMPT.format(...))
  5. _fit_patterns(budget) — bot.observed_patterns protected
  6. _fit_memories(budget) — if message has content_embedding, query pgvector
     for top-k user messages, drop lowest similarity until char budget exhausted
  7. Assemble final system prompt from fitted sections
  8. _fit_history(budget, conversations, system_prompt) — drop oldest first
  9. Return AssembledContext(history=..., budget=...)
```

---

## 12. Embedding & Semantic Memory

### 12.1 EmbeddingService

[`src/services/embedding.py`](src/services/embedding.py) provides a single service for all embedding operations:

| Method | Purpose |
| --- | --- |
| `save_message_embedding(message)` | Embeds a user message into `Message.content_embedding` |
| `save_cron_job_embedding(cron_job)` | Embeds `f"{name}. {description}"` into `CronJob.schedule_embedding` + updates `schedule_embedding_updated_at` |
| `save_mcp_embedding(mcp_server)` | Embeds concatenated tool descriptions into `MCPServer.tools_description_embedding` |
| `get_relevant_memories(query_vector, current_message_id, top_k)` | Queries pgvector for top-k similar USER messages using `CosineDistance` |

### 12.2 Embedding Model

Configured **per Bot** (not globally on Ollama):

```python
class Bot(BaseModel):
    embedding_model      = models.CharField(max_length=50)  # e.g. "nomic-embed-text"
    embedding_dimensions = models.PositiveIntegerField(default=768)  # Must match model
```

Dimensions are validated at query time: if `len(query_vector) != bot.embedding_dimensions`, the query is rejected with a warning.

### 12.3 Embedding Generation Flow

```text
Inbound message:
  telegram_msg_handler
    → TelegramUpdateHandler.handle_update() saves message (role=USER)
    → Enqueues generate_embedding(msg_id)
      → EmbeddingService.save_message_embedding(message)
      → Enqueues process_inbound_message(bot_id, msg_id)

Cron job dispatch:
  cron_job_poller
    → _refresh_cron_job_embedding_if_stale(job, ollama)
      → If schedule_embedding is None OR older than job.updated_at:
        EmbeddingService(bot=job.bot, ollama=ollama).save_cron_job_embedding(job)
    → _dispatch_cron_job(job)
```

All three embedding paths funnel through helpers in `utils.tasks` (`generate_message_embedding`, `generate_cron_job_embedding`, `generate_mcp_embedding`).

---

## 13. Django Admin Configuration

The admin panel is the primary UI for bot configuration and monitoring.

### 13.1 Key Customizations

- **Bot admin:**
  - Inline `MCPServer` editor on Bot detail page
  - Displays Telegram bot token (masked), scheduling info, Ollama model config, embedding model + dimensions
  - Filter by active/inactive, creator
  - UUID-based IDs displayed
  - Task management: `manage_conversation_summary` with countdown delay for optimized scheduling

- **CronJob admin:**
  - Inline editor on Bot detail page
  - Shows name, description, cron_expression, next_run_at, last_run_at, embedding freshness
  - Filter by active/inactive, bot
  - Uses `BaseReadOnlyUserFilteredAdmin` with explicit `has_change_permission` and `has_delete_permission` methods

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

### 13.2 Response Validation

- **Task Monitoring:** Added response validation with logging in:
  - `process_cron_job`: Validates non-empty responses before sending to Telegram
  - `process_inbound_message`: Validates responses and logs via LogFormatter
  - Uses `NO_BOT_RESPONSE` error string for null/empty response handling

### 13.3 Admin Permissions

- **Superusers:** Full access to all bots and configurations
- **Regular staff:** Can only see bots they created (`created_by`)
- **Filtering:** By creator, active status, date ranges

### 13.4 Martor Integration

Markdown editor enabled for:

- `Bot.system_prompt`
- Any other long-form text fields

Features: Emoji support, syntax highlighting, preview mode, Bootstrap theme

### 13.5 Error Handling in Telegram Client

Enhanced error handling with fallback mechanisms:

- **Error Messages:** Include response text from Telegram API for better debugging
- **Markdown Parsing:** Fallback to plain text if markdown parsing fails
  - Catches parse entity errors and retries message as plain text
  - Ensures message delivery even if formatting fails

---

## 14. Settings Structure (SPLIT)

### 14.1 Module Layout

```text
src/whimsybots/settings/
  __init__.py
  base.py     ← Production settings (DJANGO_SETTINGS_MODULE default)
  test.py     ← CI / local test settings (DJANGO_SETTINGS_MODULE in CI)
```

The monolithic `settings.py` was deleted in favour of the package. `manage.py`, `wsgi.py`, and `Dockerfile` were updated to reference `whimsybots.settings.base`. CI sets `DJANGO_SETTINGS_MODULE=whimsybots.settings.test`.

### 14.2 `base.py` — Production

```python
# Required
SECRET_KEY              = os.getenv("SECRET_KEY")
DB_NAME                 = os.getenv("DB_NAME")
DB_USER                 = os.getenv("DB_USER")
DB_PASSWORD             = os.getenv("DB_PASSWORD")
DB_HOST                 = os.getenv("DB_HOST")
CELERY_BROKER_URL       = os.getenv("CELERY_BROKER_URL")       # redis://...
CELERY_RESULT_BACKEND   = os.getenv("CELERY_RESULT_BACKEND")   # redis://...

# Optional
WEBHOOK_BASE_URL        = os.getenv("WEBHOOK_BASE_URL", "https://localhost:8000")
```

Database: PostgreSQL with `pgvector` extension. Apps: `app`, `martor`, Django admin/auth/etc.

### 14.3 `test.py` — Testing

Inherits from `base` and overrides only what's needed:

```python
from whimsybots.settings.base import *  # noqa

# Provide a deterministic secret key for tests (base.py pulls SECRET_KEY
# from the environment which is unset in CI / local dev)
SECRET_KEY = "test-secret-key-for-unit-tests-only"

# SQLite for unit tests — fast, no Postgres required
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# SQLite shims:
#   - ArrayField (django.contrib.postgres) → stored as JSON
#   - VectorField (pgvector.django) → stored as JSON (entire module stubbed)

# Local-memory cache (no real Redis needed)
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
```

### 14.4 Celery Configuration

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

### 14.5 Logging Configuration

JSON structured logging with custom formatter:

```python
LOGGING_CONFIG = None
logging.config.dictConfig({
    "version": 1,
    "formatters": {
        "default": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s - %(module)s.%(funcName)s - %(name) - %(levelname) - %(message) - %(exc_text)s"
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"": {"level": "INFO", "handlers": ["console"]}}
})
```

### 14.6 Static Files

```python
STATIC_URL = "/static/"
STATICFILES_DIRS = [os.path.join(BASE_DIR, "whimsybots/static")]
STATIC_ROOT = os.path.join(BASE_DIR, "static")
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
```

### 14.7 Martor Configuration

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

## 15. Testing Infrastructure (NEW)

### 15.1 Stack

- **pytest 9.x** with `pytest-django`, `pytest-mock`, `pytest-asyncio`
- **SQLite** (in-memory) via `whimsybots.settings.test`
- **fakeredis** for cache/Celery broker shims
- **pgvector shim** — entire `pgvector.django` module stubbed via `unittest.mock.MagicMock` so `VectorField` imports succeed without `psycopg2` + native pgvector
- **ArrayField shim** — `django.contrib.postgres.fields.ArrayField` patched at import time to use `JSONField` on SQLite

### 15.2 Layout

```text
src/tests/
  conftest.py                       ← Root fixtures (DB, fakeredis, sample data)
  test_app/                         ← choices, fields, validators, models, admin, forms, tasks
  test_clients/                     ← telegram, ollama, mcp clients
  test_cron_job/                    ← cron_job MCP server (server, db, helpers)
  test_managers/                    ← telegram_client, ollama_config managers
  test_pdf_generator/               ← pdf_generator MCP server (server, helpers)
  test_services/                    ← All services (bot_processor, context_assembler,
                                     embedding, observed_patterns, telegram_update_handler,
                                     tool_calling_coordinator, tool_executor,
                                     conversation_summary, rate_limiter, token_budget)
  test_utils/                       ← crypto, formatting, scheduling, telegram, text, tasks
  test_whimsybots/                  ← views (Telegram webhook)
```

### 15.3 pytest Configuration

[`src/pytest.ini`](src/pytest.ini):

```ini
[pytest]
DJANGO_SETTINGS_MODULE = whimsybots.settings.test
testpaths = tests
asyncio_mode = auto
python_files = tests.py test_*.py *_test.py
python_classes = Test*
python_functions = test_*
```

### 15.4 Make Targets

```makefile
test            # pytest src/tests/
test-verbose    # pytest src/tests/ -v
test-coverage   # pytest src/tests/ --cov
test-specific   # pytest src/tests/<FILE>
```

### 15.5 Continuous Integration (NEW)

[`.github/workflows/master.yml`](.github/workflows/master.yml) runs the full suite on every push and PR to `master`:

- Python 3.12 on `ubuntu-latest`
- Caches pip dependencies keyed on `src/requirements.txt`
- Installs WeasyPrint system deps (cairo, pango, fonts, etc.)
- Runs `pytest -v --maxfail=1 --tb=short` with `DJANGO_SETTINGS_MODULE=whimsybots.settings.test`

---

## 16. Utils Subpackage (NEW)

Cross-cutting helpers were extracted from the old `src/app/utils.py` into a dedicated `src/utils/` subpackage so they can be imported from tasks, services, clients, and models without dragging app-level imports along.

| Module | Contents |
| --- | --- |
| [`crypto.py`](src/utils/crypto.py) | `get_token_hash`, `get_fernet`, encryption helpers used by `EncryptedCharField` / `EncryptedJSONField` |
| [`formatting.py`](src/utils/formatting.py) | `convert_messages_to_ollama_format`, `get_admin_link` |
| [`scheduling.py`](src/utils/scheduling.py) | `calculate_next_run_at` (croniter wrapper) |
| [`tasks.py`](src/utils/tasks.py) | All shared Celery helpers — constants (`MAX_RETRIES`, `RETRY_BASE_SECONDS`, `DEFAULT_QUEUE`, `TELEGRAM_ALLOWED_UPDATES`), object lookups (`get_ollama_cfg`, `get_bot_obj`, `get_cron_obj`, `get_msg_obj`), logging helpers (`create_log`, `log_task_failure`, `build_error_description`), embedding dispatch (`generate_message_embedding`, `generate_cron_job_embedding`, `generate_mcp_embedding`) |
| [`telegram.py`](src/utils/telegram.py) | Telegram response sanitization helpers |
| [`text.py`](src/utils/text.py) | Message splitting (>4096 char), text sanitization |

Import paths updated everywhere — `app/admin.py`, `app/fields.py`, `app/forms.py`, `app/models.py`, `app/tasks.py`, `clients/telegram.py`, `services/bot_processor.py`, `services/context_assembler.py`, `services/embedding.py`, `services/telegram_update_handler.py`, `strings.py`.

---

## 17. Tech Stack Summary

| Concern | Technology | Notes |
| --- | --- | --- |
| **Core Framework** | | |
| Web framework | Django 5.x | Settings split into `base.py` / `test.py` |
| Admin UI | Django Admin | Customized with inlines, filters |
| Markdown editor | Martor | Bootstrap theme, emoji, syntax highlighting |
| **Task Queue** | | |
| Task queue | Celery 5.x | Async task processing |
| Beat scheduler | django-celery-beat | Database-backed scheduler |
| Message broker | Redis | Broker + result backend |
| Async DB client | asyncpg | PostgreSQL async driver for cron_job MCP server |
| **Data Storage** | | |
| Database | PostgreSQL | Primary data store (production) |
| Test database | SQLite + pgvector shim | In-memory, no native deps |
| Vector search | pgvector | Cosine distance over messages / cron schedules / MCP tools |
| Data validation | Pydantic v2 | Type validation and structured JSON schemas |
| **AI/LLM** | | |
| LLM runtime | Ollama | Local LLM server |
| LLM client | ollama Python package | Chat + embeddings |
| Embedding models | Per-bot | `embedding_model` + `embedding_dimensions` on `Bot` |
| Tool protocol | Model Context Protocol (MCP) | External tool integration |
| MCP framework | FastMCP | Lightweight Python MCP server framework |
| Cron utilities | croniter | Cron expression parsing and next-run calculation |
| **Communication** | | |
| Messaging | Telegram Bot API | User communication channel |
| HTTP client | httpx | Async HTTP client (for MCP and APIs) |
| Legacy HTTP | requests | For Telegram API calls |
| **Report Generation** | | |
| PDF generation | WeasyPrint | HTML to PDF conversion |
| **Infrastructure** | | |
| Containerization | Docker | Containerized deployment |
| Process manager | Gunicorn | WSGI HTTP server |
| Static files | WhiteNoise | Production static file serving |
| Logging | python-json-logger | Structured JSON logging |
| **Testing & CI** | | |
| Test runner | pytest 9.x | + `pytest-django`, `pytest-mock`, `pytest-asyncio` |
| Cache shim | fakeredis | In-memory Redis for tests |
| CI | GitHub Actions | `.github/workflows/master.yml` runs pytest on push/PR |
| **Development** | | |
| Environment | python-dotenv | Environment variable management |
| Package manager | pip | Python dependencies |

---

## 18. Deployment Architecture

### 18.1 Components

```text
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
   │ + pgvector   │
   └─────────────┘
```

### 18.2 External Services

```text
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

### 18.3 Docker Services

```yaml
services:
  web:       # Django + Gunicorn
  celery:    # Celery worker (default queue)
  beat:      # Celery beat scheduler
  redis:     # Message broker
  postgres:  # Database (with pgvector)
  ollama:    # Optional: Local LLM (can be external)
```

MCP servers (time, cron_job, pdf_generator) run as stdio subprocesses spawned by the worker on demand — no separate Docker service is required.

---

## 19. Build Status

### ✅ Completed

| Component | Status | Notes |
| --- | --- | --- |
| Django project structure | ✅ | Settings split into `base.py` / `test.py` |
| Data models | ✅ | UUID primary keys + vector fields on `Message`, `MCPServer`, `CronJob` |
| Per-bot embedding config | ✅ | `embedding_model` + `embedding_dimensions` on `Bot` |
| Django admin | ✅ | Customized with inlines, filters |
| Ollama client | ✅ | Chat + tool calling + embeddings + keep_alive |
| Telegram client | ✅ | send_message, send_document, typing, webhook + rate limit handling + intelligent message splitting + markdown fallback |
| Telegram token encryption | ✅ | Encrypted token + deterministic hash for lookup |
| MCP client | ✅ | Local + remote transport support with stdio and HTTP |
| Celery tasks | ✅ | `telegram_msg_handler`, `generate_embedding`, `process_inbound_message`, `process_cron_job`, `manage_conversation_summary`, `setup_bot_webhook`, `regenerate_observed_patterns` |
| Tool calling loop | ✅ | Async tool execution with Ollama via `tool_calling_coordinator` |
| Semantic tool ranking | ✅ | `_get_tools_config()` re-ranks MCP servers by cosine distance to query vector |
| Cron schedule embeddings | ✅ | Auto-refreshed by `cron_job_poller` when stale |
| MCP tools description embeddings | ✅ | Generated by `EmbeddingService.save_mcp_embedding()` |
| Message embeddings | ✅ | Generated by `generate_embedding` task, used for relevant memory retrieval |
| Report generation | ✅ | MCP Server-based PDF generation called directly by LLM (no separate async task) |
| Cron job processing | ✅ | Dedicated `process_cron_job` task accepting `CronJob` instance |
| Default MCP servers | ✅ | Time, Cron Job Manager, PDF Generator (FastMCP) — always available, not persisted |
| Cron Job MCP Server | ✅ | Standalone module with list/create/update/delete tools |
| Multi-queue Celery | ✅ | beat + default queues |
| Comprehensive logging | ✅ | JSON logging with `LogFormatter` |
| Async database | ✅ | asyncpg for cron_job MCP server database access |
| Encryption for secrets | ✅ | EncryptedCharField + EncryptedJSONField |
| Telegram rate limiting | ✅ | Rate limit retry with exponential backoff in tasks |
| Message splitting | ✅ | Intelligent splitting for responses > 4096 chars |
| Typing indicators | ✅ | Responsive typing indicators during processing |
| Conversation context management | ✅ | `ConversationSummaryService` + `manage_conversation_summary` with countdown delay |
| Celery Flower monitoring | ✅ | Monitoring service in docker-compose with persistent volume |
| Token budgeting | ✅ | 6-tier priority system + `recommended_tool_count` + measured tool defs |
| PDF Generator MCP Server | ✅ | Standalone FastMCP server for PDF generation |
| Enhanced Telegram error handling | ✅ | Response text in errors, markdown fallback |
| Response validation | ✅ | Null/empty response checks in tasks |
| `utils` subpackage | ✅ | `crypto`, `formatting`, `scheduling`, `tasks`, `telegram`, `text` extracted from `app/utils.py` |
| Shared Celery helpers | ✅ | All cross-task helpers consolidated in `utils.tasks` |
| **Test suite** | ✅ | Full pytest suite across app / clients / cron_job / managers / pdf_generator / services / utils / whimsybots |
| **Test infrastructure** | ✅ | `pytest.ini`, top-level `conftest.py`, SQLite + fakeredis + pgvector shims |
| **Continuous integration** | ✅ | GitHub Actions workflow runs full pytest on push/PR to master |
| **Make targets for tests** | ✅ | `test`, `test-verbose`, `test-coverage`, `test-specific` |
| Code organization | ✅ | Alphabetically organized imports across all modules |

---

## 20. Key Design Decisions

### 20.1 UUID Primary Keys

- **Why:** Security through obscurity, distributed system friendly
- **Trade-off:** Slightly larger indexes, less human-readable

### 20.2 Async Tool Calling

- **Why:** MCP client requires async/await for stdio/HTTP connections
- **Implementation:** `asyncio.run()` in synchronous Celery tasks

### 20.3 Tool-Based Intent

- **Old Design:** StructuredOutput with explicit intent classification in LLM response
- **Current Design:** Intent is implicit in which tools the LLM calls
- **Why Changed:** More flexible, aligns with MCP philosophy, simpler to maintain
- **Benefit:** Better intent coverage through tool combinations

### 20.4 Default MCP Servers

- **Why:** Provide core functionality (time, cron management, pdf generator) without manual setup
- **Instantiation:** Temporary in-memory `MCPServer` objects created per message
- **Benefit:** Always available, extensible with custom MCPServers

### 20.5 Multi-Queue Celery

- **Why:** Isolate beat scheduling from worker processing
- **Benefit:** Prevents worker overload from affecting scheduler

### 20.6 Service Layer Pattern

- **Why:** Clean separation of concerns, testability
- **Structure:** Tasks → Services → Clients → External APIs
- **Helpers:** Shared Celery utilities live in `utils.tasks`

### 20.7 Tool Calling Abstraction

- **Why:** Unified interface for MCP tools regardless of transport
- **Benefit:** Easy to add new MCP servers without code changes

### 20.8 CronJob with Description

- **Why:** LLM needs context about what each scheduled job does
- **Usage:** Description + schedule embedding passed to `process_cron_job` in `CRON_JOB_PROMPT`
- **Benefit:** LLM can execute task semantically correct without hardcoded logic

### 20.9 PDF Generation via MCP Server (Inline Tool Calling)

- **Why:** Inline PDF generation eliminates async task overhead and provides instant feedback
- **Current Design:**
  - PDF generator is a default MCP server available in all flows
  - LLM calls `pdf_generator.generate_pdf()` directly when needed
- **Benefits:** Faster UX, simpler architecture, better modularity

### 20.10 Token Budget with Priority Tiers

- **Why:** Prevent context window overflow when accumulating summaries + memories + tool responses
- **Implementation:** 6-tier priority system with measured (not estimated) tool definition costs and `recommended_tool_count` cap
- **Benefit:** Consistent conversation length management with automatic summarization, no partial tool schemas

### 20.11 Skills Inlined into System Prompt

- **Why:** Static `SkillsRegistry` added maintenance overhead and occupied a fixed 5% reservation regardless of context pressure
- **Implementation:** Skills block folded into `DEFAULT_SYSTEM_PROMPT` template, formatted at assembly time with `{bot_id}` and `{timezone}`
- **Benefit:** Skills participate in normal truncation when context is tight; one fewer module to maintain

### 20.12 Semantic MCP Tool Ranking (NEW)

- **Why:** With many MCP servers attached, offering every tool to the LLM wastes context and degrades tool selection accuracy
- **Implementation:** `_get_tools_config(query_vector)` re-ranks bot MCPServers by cosine distance of `tools_description_embedding` to the query vector (user message embedding for inbound, `schedule_embedding` for cron runs)
- **Result:** Tools are ordered most-relevant-first, then trimmed to `recommended_tool_count`

### 20.13 Embedding Dispatch Centralization (NEW)

- **Why:** Previously `TelegramUpdateHandler` directly imported and enqueued `process_inbound_message` — a circular coupling that made the embedding pipeline non-reusable
- **Implementation:** `generate_embedding` task now enqueues `process_inbound_message` itself; handler only triggers embedding
- **Benefit:** Embedding pipeline is reusable for cron/MCP embeddings (sharing `utils.tasks.generate_*_embedding` helpers); handler has fewer imports

### 20.14 Settings Split (NEW)

- **Why:** Monolithic `settings.py` made it impossible to run unit tests without a real Postgres + Redis
- **Implementation:** `base.py` (production) and `test.py` (SQLite + fakeredis + pgvector shim) inherit cleanly via `from .base import *`
- **Benefit:** CI runs in seconds with no external services; production behaviour unchanged

### 20.15 Removal of Async Task-Based Report Generation

- **Why:** Inline tool calling is faster and simpler than task queuing
- **Trade-off:**
  - **Removed:** Separate async task, no background processing
  - **Gained:** Instant results, simpler code, fewer moving parts
- **Implementation:** Report generation happens via MCP tool call during message processing
