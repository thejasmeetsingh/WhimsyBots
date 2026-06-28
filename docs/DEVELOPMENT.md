# Development Guide

> **Documentation Map**
> - [README](../README.md) — Project overview, features, and quick start
> - [ARCHITECTURE](ARCHITECTURE.md) — Technical deep dive, data models, request flows
> - [TROUBLESHOOTING](TROUBLESHOOTING.md) — Common issues and resolutions

A practical guide for anyone contributing to WhimsyBots — from cloning the repo and running the test suite, to adding a model, a Celery task, or an MCP server.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Getting Started](#2-getting-started)
   - 2.1 [Option A — Docker (recommended)](#21-option-a--docker-recommended)
   - 2.2 [Option B — Bare-metal / local venv](#22-option-b--bare-metal--local-venv)
3. [Environment Variables](#3-environment-variables)
4. [Project Layout](#4-project-layout)
5. [Key Concepts](#5-key-concepts)
6. [Celery Tasks Reference](#6-celery-tasks-reference)
7. [Coding Conventions](#7-coding-conventions)
8. [How-to Recipes](#8-how-to-recipes)
   - 8.1 [Add a new model / field](#81-add-a-new-model--field)
   - 8.2 [Add a bot-scoped MCP server](#82-add-a-bot-scoped-mcp-server)
   - 8.3 [Add a Celery task](#83-add-a-celery-task)
   - 8.4 [Add a new prompt template](#84-add-a-new-prompt-template)
9. [Testing](#9-testing)
10. [Debugging & Logs](#10-debugging--logs)
11. [Common Pitfalls](#11-common-pitfalls)
12. [Security Notes](#12-security-notes)

---

## 1. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.12** | Pinned in `Dockerfile` and `.github/workflows/master.yml`. |
| Docker + Docker Compose | recent | Required for the supported path; the `Makefile` is a thin wrapper around `docker compose`. |
| PostgreSQL | **16** | With the [pgvector](https://github.com/pgvector/pgvector) extension. The provided `docker-compose.yml` uses `pgvector/pgvector:pg16-trixie`. |
| Redis | **7** | Used as the Celery broker + result backend + Django cache. |
| Ollama | latest stable | Run on the host, not in Docker. The app reaches it via `host.docker.internal:11434`. |
| WeasyPrint system libs | — | `cairo`, `pango`, `gdk-pixbuf`, `ffi`, `shared-mime-info`. Installed automatically inside the Docker image; required locally too. See the CI workflow for the apt list. |

---

## 2. Getting Started

### 2.1 Option A — Docker (recommended)

The repo's `Makefile` wraps every command you need. From the project root:

```bash
# 1. Configure environment
cd src
cp .env.example .env
# Edit .env — see [§3](#3-environment-variables) for the full list

# 2. Build images and start all services (db, redis, app, celery_worker, celery_beat, flower)
make build
make up

# 3. Run migrations and create your first admin user
make migrate
make createsuperuser

# 4. Verify
make ps              # all containers should be (healthy)
make logs-app        # follow the web app logs
```

Once services are up:

- Django admin → `http://localhost:8000/admin/`
- Flower (Celery UI) → `http://localhost:5555`
- Bot webhooks → `http://localhost:8000/webhook/<bot_token>/` (only meaningful over a public HTTPS URL — see [§11](#11-common-pitfalls))

To stop / restart / tear down:

```bash
make stop            # stop without removing containers
make start           # start stopped containers
make restart         # down + up
make down            # stop and remove containers
make clean           # down + remove volumes (DELETES the database)
```

### 2.2 Option B — Bare-metal / local venv

Useful when you want a tight editor / debugger loop without rebuilding containers.

```bash
# Inside src/
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# System libs (Debian/Ubuntu) — required by WeasyPrint
sudo apt-get install -y libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info fonts-dejavu fonts-liberation

# Point Django at the test settings to skip Postgres/pgvector
export DJANGO_SETTINGS_MODULE=whimsybots.settings.test
export SECRET_KEY=local-dev-key

# Run migrations + worker + beat + web in separate terminals
python manage.py migrate
celery -A whimsybots worker -Q beat,default -l INFO
celery -A whimsybots beat -l INFO
python manage.py runserver
```

> ⚠️ Bare-metal mode is best for *unit-level* iteration. The full integration story (Ollama tool calling, MCP servers, pgvector similarity search) still requires Docker, since `pgvector` is shimmed away in the test settings module.

---

## 3. Environment Variables

Declared in `src/.env.example`. Every variable is read in `whimsybots/settings/base.py`.

| Variable | Purpose | Example |
|---|---|---|
| `SECRET_KEY` | Django secret + Fernet key seed (see [§12](#12-security-notes)). | `<random 50+ char string>` |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | PostgreSQL credentials. | `whimsybots` |
| `DB_HOST` | DB hostname. Use `db` inside Compose, `localhost` outside. | `db` |
| `CELERY_BROKER_URL` | Celery broker. Always Redis in this project. | `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | Celery result backend. Set to the SQLAlchemy-style DB URL so results persist. | `db+postgresql://whimsybots:<pwd>@db/whimsybots` |
| `CELERY_FLOWER_USERNAME` / `CELERY_FLOWER_PASSWORD` | Basic-auth credentials for Flower at `:5555`. | `test` / `test` |
| `CACHE_LOCATION` | Django cache backend URL. | `redis://redis:6379` |
| `WEBHOOK_BASE_URL` | Public base URL Telegram will POST updates to. **Must be HTTPS.** | `https://bots.example.com` |

---

## 4. Project Layout

```text
src/
├── app/                          # Core Django app
│   ├── models.py                 # All ORM models (Ollama, Bot, MCPServer, Message, CronJob, Log)
│   ├── admin.py                  # Django admin registrations (user-scoped via BaseUserFilteredAdmin)
│   ├── tasks.py                  # Celery task definitions (orchestration only)
│   ├── fields.py                 # EncryptedCharField, EncryptedJSONField
│   ├── validators.py             # validate_cron_expression, validate_keep_alive, validate_transport_fields
│   ├── choices.py                # Enum-style choices (MessageRole, MCPTransportType)
│   ├── forms.py                  # BotForm, MCPServerForm
│   └── migrations/               # 0001_enable_pgvector, 0002_initial
│
├── clients/                      # External API clients (pure wrappers)
│   ├── telegram.py               # TelegramClient — send messages/docs, rate-limited via Redis
│   ├── ollama.py                 # OllamaClient — chat, list models, embeddings (wraps `ollama` SDK)
│   └── mcp.py                    # MCPClient — async stdio + streamable-http connections
│
├── managers/                     # Lightweight factories + caches
│   ├── telegram_client.py        # TelegramClientManager.create_client(bot)
│   └── ollama_config.py          # OllamaConfigManager — cached fetch of the active Ollama row
│
├── services/                     # Business logic — the "what the app actually does" layer
│   ├── bot_processor.py          # BotMessageProcessor — process_message / process_cron_job
│   ├── context_assembler.py      # Builds the system prompt + history payload
│   ├── conversation_summary.py   # Summarises old messages when context window tightens
│   ├── embedding.py              # EmbeddingService — generates + queries pgvector
│   ├── observed_patterns.py      # Builds the per-bot user behaviour profile
│   ├── rate_limiter.py           # Redis-backed sliding-window rate limiter
│   ├── telegram_update_handler.py # Parses inbound updates, persists them, queues processing
│   ├── token_budget.py           # TokenBudgetService — priority-tier context budgeting
│   ├── tool_calling_coordinator.py # Loops chat → tool_call → tool_result until the model is done
│   └── tool_executor.py          # MCPToolConfig, MCPToolsBuilder, ToolExecutor
│
├── cron_job/                     # Standalone Cron Job MCP server (FastMCP)
│   ├── server.py                 # @mcp.tool() registrations: list/create/update/delete
│   ├── db.py                     # Async SQLAlchemy session
│   ├── models.py                 # SQLAlchemy mirror of app.models.CronJob
│   └── helpers.py                # Cron validation + formatting
│
├── pdf_generator/                # Standalone PDF Generator MCP server (FastMCP)
│   ├── server.py                 # @mcp.tool() registration: generate_and_send_report
│   ├── db.py                     # Async SQLAlchemy session
│   └── helpers.py                # HTML extraction, PDF rendering (WeasyPrint), Telegram send
│
├── utils/                        # Cross-cutting helpers
│   ├── crypto.py                 # Fernet encryption, deterministic token hashing
│   ├── formatting.py             # convert_messages_to_ollama_format, get_admin_link
│   ├── scheduling.py             # calculate_next_run_at (croniter wrapper)
│   ├── tasks.py                  # Shared Celery helpers (lookups, log, embedding dispatch)
│   ├── telegram.py               # parse_telegram_update
│   └── text.py                   # split_message — Telegram 4096-char aware
│
├── tests/                        # Pytest suite (mirrors the src/ layout)
│   ├── conftest.py               # Root fixtures: fake_redis, fake_redis_server, django_db_setup
│   ├── test_app/                 # admin, choices, fields, forms, models, tasks, validators
│   ├── test_clients/             # mcp, ollama, telegram clients
│   ├── test_cron_job/            # server, db, helpers
│   ├── test_managers/            # ollama_config, telegram_client
│   ├── test_pdf_generator/       # server, helpers
│   ├── test_services/            # bot_processor, context_assembler, conversation_summary,
│   │                             #   embedding, observed_patterns, rate_limiter,
│   │                             #   telegram_update_handler, token_budget,
│   │                             #   tool_calling_coordinator, tool_executor
│   ├── test_utils/               # crypto, formatting, scheduling, telegram, text, tasks
│   └── test_whimsybots/          # views (TelegramWebhook)
│
├── whimsybots/                   # Django project
│   ├── settings/
│   │   ├── base.py               # Production settings (default)
│   │   └── test.py               # SQLite + fakeredis + pgvector/ArrayField shims
│   ├── celery.py                 # Celery app + autodiscover
│   ├── urls.py                   # /admin/, /martor/, /webhook/<token>/
│   ├── views.py                  # TelegramWebhook view
│   └── wsgi.py                   # WSGI entry point for Gunicorn
│
├── user/                         # Django default User app migrations
├── static/                       # Collected static + admin/martor assets
├── prompts.py                    # LLM prompt templates (DEFAULT_SYSTEM_PROMPT, CRON_JOB_PROMPT, …)
├── strings.py                    # User-facing + log message templates (single source of truth)
├── conftest.py                   # Pytest fixtures (fakeredis helpers)
├── pytest.ini                    # DJANGO_SETTINGS_MODULE=whimsybots.settings.test, asyncio_mode=auto
├── Dockerfile                    # python:3.12-alpine, multi-stage, WeasyPrint deps
├── docker-compose.yml            # db, redis, app, celery_worker, celery_beat, celery_flower
├── gunicorn.conf.py              # bind 0.0.0.0:8000, 2 sync workers, 120s timeout
├── manage.py
└── requirements.txt
```

---

## 5. Key Concepts

### Bots
A `Bot` is a single Telegram-fronted AI agent. Every bot carries:
- An LLM config (`ollama_model`, plus an `embedding_model` + `embedding_dimensions`).
- A Markdown system prompt.
- A Telegram bot token (encrypted; looked up via SHA-256 hash).
- A `telegram_chat_id` that is auto-populated the first time the user sends a message.

### MCP Servers
External tool providers, attached per-bot via the admin:
- **LOCAL** — spawned as a subprocess over stdio. Requires `command` (e.g. `python`, `npx`, `uv`) and `args`. Optional `secrets` become env vars.
- **REMOTE** — connected over HTTPS. Requires `endpoint`. Optional `secrets` become HTTP headers.

`MCPServer.get_default_mcp_servers()` returns the always-on servers — `cron_job`, `time`, and `pdf_generator` — appended to every bot's tool list at inference time.

Three global servers ship **inside** the app and are invoked as `python -m cron_job`, `python -m pdf_generator`, and `python -m mcp_server_time`. They share the DB via SQLAlchemy + asyncpg.

### Embeddings
Every user `Message` and every `CronJob` carries a vector embedding, generated from Ollama on save. `EmbeddingService` centralises the dispatch (see [`generate_embedding` task](#6-celery-tasks-reference)). Embeddings power:
- **Memory retrieval** — top-k similar past messages injected into the system prompt.
- **MCP tool ranking** — per-server `tools_description_embedding` is compared (cosine distance) against the current message / schedule embedding.

### CronJobs
A bot schedules itself by calling `create_cron_job` on the cron_job MCP server. Each job stores:
- A standard 5-field `cron_expression` (validated by `croniter`).
- A precomputed `next_run_at` (recomputed after each run).
- A `schedule_embedding` for semantic tool ranking.

### Logs
Every bot execution appends a `Log` row (`is_success`, `description`). Useful for surfacing failures in the admin and for the audit trail.

---

## 6. Celery Tasks Reference

All tasks live in `app/tasks.py`. Shared helpers live in `utils/tasks.py`. Bound tasks (`bind=True`) call `self.retry(...)` on failure with exponential backoff.

| Task | Queue | Trigger | Retry | Purpose |
|---|---|---|---|---|
| `telegram_msg_handler` | `default` | Webhook POST | 3 | Look up bot by token hash → `TelegramUpdateHandler.handle_update` |
| `generate_embedding` | `default` | Enqueued by handler | 3 | Save message embedding → enqueue `process_inbound_message` |
| `process_inbound_message` | `default` | Enqueued by `generate_embedding` | 3 | Full response pipeline: context, tool calling loop, send reply |
| `process_cron_job` | `default` | Enqueued by `cron_job_poller` (ETA = `next_run_at`) | 3 | Execute a scheduled job with `CRON_JOB_PROMPT` |
| `cron_job_poller` | `beat` | Celery Beat, every minute | 3 | Refresh stale schedule embeddings → dispatch due jobs |
| `manage_conversation_summary` | `default` | Manual from admin on `num_ctx` change; post-message | 3 | Summarise old messages for all bots or one bot |
| `setup_bot_webhook` | `default` | Fires on bot save in admin | 3 | Register Telegram webhook with the bot API |
| `observed_patterns.regenerate` | `default` | Periodic, every N user messages | 3 | Rebuild `Bot.observed_patterns` behavioural profile |

### Retry strategy

```python
RETRY_BASE_SECONDS = 60
MAX_RETRIES = 3
# Effective backoff: 60s → 120s → 240s

# Telegram rate limits:
except TelegramRateLimitError as exc:
    raise self.retry(exc=exc, countdown=exc.retry_after)
```

Always wrap a task body in `try / except` so unexpected exceptions hit the retry path with exponential backoff. Use `utils.tasks.create_log` and `log_task_failure` to keep the audit trail consistent.

---

## 7. Coding Conventions

- **Python 3.12**, fully type-hinted. Public functions and module-level constants carry type annotations; legacy code is being progressively annotated.
- **Google-style docstrings** on every public class / function (see any module under `app/` or `services/` for the canonical shape).
- **UUID primary keys** via `BaseModel` (in `app/models.py`). All models inherit it.
- **User-facing strings** live in `strings.py` as templates; do not hard-code log messages or error copy.
- **Secrets at rest** use `EncryptedCharField` / `EncryptedJSONField` (Fernet, keyed off `SECRET_KEY`). Never store plaintext tokens.
- **Token lookup by hash** — `Bot.save()` computes `telegram_bot_token_hash = sha256(token)`. Lookups always go through the hash; ciphertext is only decrypted when constructing an outgoing client.
- **Celery helpers** — shared lookups (`get_bot_obj`, `get_cron_obj`, `get_msg_obj`), embedding dispatch (`generate_*_embedding`), and logging helpers (`create_log`, `log_task_failure`, `build_error_description`) live in `utils/tasks.py`. Don't redefine them inside `app/tasks.py`.
- **Default queues** — `beat` for scheduled dispatchers; `default` for everything else. Use `utils.tasks.DEFAULT_QUEUE` rather than the literal string.
- **No circular imports between `app/` and `utils/`** — `app/tasks.py` imports from `utils/tasks.py`; the reverse direction is forbidden. Where a helper needs to enqueue a task from `app/tasks.py`, import `whimsybots.celery.task` (the Celery app instance) and dispatch by task name.
- **Tests** — every new module gets a matching `tests/test_<module>/` directory. Async tests rely on `pytest-asyncio`'s auto mode (`asyncio_mode = auto` in `pytest.ini`).

---

## 8. How-to Recipes

### 8.1 Add a new model / field

1. Add / extend the model in `src/app/models.py`. Inherit from `BaseModel`.
2. Add validators to `src/app/validators.py` if the field needs domain validation.
3. Register / extend the admin in `src/app/admin.py`. Inherit from `BaseUserFilteredAdmin` if the model is bot-scoped (so users only see their own rows).
4. Generate a migration:
   ```bash
   make makemigrations
   # or: docker compose -f src/docker-compose.yml exec app python manage.py makemigrations
   ```
5. Apply:
   ```bash
   make migrate
   ```
6. Add tests under `src/tests/test_app/test_models.py`.

### 8.2 Add a bot-scoped MCP server

End users add these via the admin; for code-level integration (e.g. a new default server):

1. Add a new entry to `MCPServer.get_default_mcp_servers()` in `app/models.py`.
2. If the server lives **inside** the repo, create a new top-level module next to `cron_job/` and `pdf_generator/` (FastMCP + `python -m <module>`).
3. Make sure the secrets dict carries DB credentials / `SECRET_KEY` when the server needs to decrypt bot tokens.
4. Update `prompts.DEFAULT_SYSTEM_PROMPT` if the LLM should know about the new tool category.
5. Add tests under `src/tests/test_<server>/`.

### 8.3 Add a Celery task

```python
# src/app/tasks.py
from whimsybots.celery import task as celery
from utils.tasks import (
    DEFAULT_QUEUE,
    MAX_RETRIES,
    RETRY_BASE_SECONDS,
    get_bot_obj,
    get_ollama_cfg,
    create_log,
    log_task_failure,
    build_error_description,
)

@celery.task(bind=True, max_retries=MAX_RETRIES, queue=DEFAULT_QUEUE)
def my_new_task(self, bot_id: str) -> None:
    bot = get_bot_obj(bot_id)
    if bot is None:
        return  # helper already logged

    ollama = get_ollama_cfg()
    if ollama is None:
        return

    try:
        # ... do work ...
        create_log(bot=bot, is_success=True, desc="my_new_task ok")
    except TelegramRateLimitError as exc:
        raise self.retry(exc=exc, countdown=exc.retry_after)
    except Exception as exc:
        log_task_failure(
            self, bot=bot, exc=exc, desc=build_error_description(
                "GENERAL_TASK_ERROR", func_name="my_new_task", bot_name=bot.name, error=exc
            ),
        )
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS * 2 ** self.request.retries)
```

Then:
- Add the task name to `CELERY_TASK_ROUTES` in `whimsybots/settings/base.py` if it needs a custom queue.
- Add a fixture in `src/tests/test_app/test_tasks.py`.
- If the task is **scheduled**, add a Celery Beat entry in `app.tasks.cron_job_poller` (or whichever poller owns the cadence).

### 8.4 Add a new prompt template

Add the template string to `src/prompts.py` as a module-level constant. Existing templates use the `"""..."""` triple-quoted style and document inline sections with `# Header` / blank lines. Consume it from the relevant service:

```python
from prompts import MY_NEW_PROMPT
formatted = MY_NEW_PROMPT.format(bot_id=str(bot.id), ...)
```

---

## 9. Testing

### Running tests

```bash
# All tests
make test                    # → pytest src/tests/

# Verbose / coverage / single
make test-verbose            # → pytest src/tests/ -v
make test-coverage           # → pytest src/tests/ --cov
make test-specific FILE=test_app/test_models.py::TestBot::test_str
```

### How the test stack is wired

- `pytest.ini` sets `DJANGO_SETTINGS_MODULE = whimsybots.settings.test`. **Never** point tests at the production `base` settings — pgvector and ArrayField will refuse to load on SQLite.
- `whimsybots/settings/test.py` provides:
  - `SECRET_KEY = "test-secret-key-for-unit-tests-only"` (constant so `get_token_hash` stays deterministic).
  - `DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}`.
  - A `JSONField`-backed shim for `ArrayField`.
  - A `MagicMock`-backed `pgvector.django` module (with a real `VectorField = JSONField` shim) so model imports succeed.
  - `CACHES` using `LocMemCache` (no Redis needed).
- `src/conftest.py` exposes `fake_redis` (per-test) and `fake_redis_server` (per-session) for places where the production code reaches for Redis.
- `pytest-asyncio` is in **auto mode** — no `@pytest.mark.asyncio` needed; just write `async def test_*`.

### Writing a test

```python
# src/tests/test_services/test_my_feature.py
from unittest.mock import patch

import pytest

from app.models import Bot, Ollama


@pytest.fixture
def bot(db):
    ollama = Ollama.objects.create(endpoint="http://localhost:11434")
    return Bot.objects.create(
        created_by_id=1,
        name="t",
        ollama_model="llama3",
        embedding_model="nomic-embed-text",
        telegram_bot_token="test-token",
    )


def test_my_feature(bot):
    with patch("services.my_feature.external_call") as mock_call:
        mock_call.return_value = "ok"
        result = my_feature(bot)
    assert result == "ok"
```

### Mocking external services

- **Ollama** — patch `clients.OllamaClient.chat` / `embed`.
- **Telegram** — patch `clients.TelegramClient.send_message` / `send_document`.
- **MCP** — patch `clients.mcp_client` (the module-level callable) or `services.tool_executor.MCPToolsBuilder.build_tools_from_servers`.

---

## 10. Debugging & Logs

| What you want | Command / URL |
|---|---|
| Follow app logs | `make logs-app` |
| Follow celery worker logs | `make logs-worker` |
| Follow celery beat logs | `make logs-scheduler` |
| Follow redis logs | `make logs-redis` |
| Open Django shell (against live DB) | `make shell` |
| Open a shell inside the app container | `make app-shell` |
| Open a shell inside the celery container | `make worker-shell` |
| Inspect Redis (rate-limit keys, queue state) | `make redis-shell` |
| Open PostgreSQL shell | `make db-shell` |
| Inspect the Celery UI | `http://localhost:5555` |
| Dump the database | `make db-dump` |
| Restore from a dump | `make db-restore FILE=backup.sql` |

Logs are emitted as **JSON** (configured in `whimsybots/settings/base.py`). Pipe through `jq` for readable output:

```bash
make logs-app | jq 'select(.levelname == "ERROR")'
```

---

## 11. Common Pitfalls

- **Embedding dimensions must match the model.** Changing `Bot.embedding_dimensions` after messages exist will break similarity queries (pgvector enforces strict dimension matching). Re-embed everything if you must change it.
- **Rotating `SECRET_KEY` invalidates encrypted data.** Fernet derives its key from `SECRET_KEY` via SHA-256; changing it makes every encrypted token / MCP secret unreadable. Only rotate when wiping the DB.
- **Telegram webhook must be HTTPS** and reachable by Telegram's servers. For local testing, use `ngrok` or `cloudflared` and update `WEBHOOK_BASE_URL` accordingly.
- **Telegram's 23-second webhook timeout.** The view dispatches the message to Celery and returns `200 OK` immediately; never do work inline in the view.
- **Ollama `localhost` → `host.docker.internal`.** `OllamaClient._get_clean_endpoint` rewrites `localhost` automatically; if you've overridden the endpoint, double-check it resolves from inside the app container.
- **Default Ollama context window.** `num_ctx` has a hard floor of 4096 — raise it on capable hardware, but never below.
- **`unittest.mock` and Celery tasks.** The `bind=True` style + `self.retry(...)` interacts oddly with mocks; prefer `with patch("module.function")` over full task replacement.
- **Async tests vs sync tests.** The codebase mixes both freely; respect the function signature (no `await` in a sync test even if the production code is async — mock at the boundary).

---

## 12. Security Notes

- **Telegram bot tokens** are encrypted at rest via `EncryptedCharField`. The webhook view derives a SHA-256 hash and looks up the bot by that hash — the plaintext never leaves the request unless we need to call the Telegram API.
- **MCP server secrets** (env vars for LOCAL, HTTP headers for REMOTE) are encrypted with `EncryptedJSONField`.
- **Per-user isolation in the admin** is provided by `BaseUserFilteredAdmin` — bot-scoped models automatically filter their queryset by `bot__created_by_id = request.user.id`.
- **No CSRF on the webhook URL** — the view is decorated with `@csrf_exempt`. The view validates the payload shape, but anyone who knows a bot token can POST to it; keep tokens secret.
- **Rate limiting** — `clients.TelegramClient` enforces a local 1 msg/sec sliding window via `services/rate_limiter.RateLimiter` (Redis-backed), and honours Telegram's `retry_after` on 429s.
- **`SECRET_KEY`** is the single root secret: it signs Django cookies, derives the Fernet key, and is what `whimsybots.celery` auto-loads. Treat `.env` as production-grade secret material.