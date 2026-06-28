<div align="center">

# WhimsyBots

**A self-hosted, Django-based AI agent platform where you create and configure AI-powered "apps" through an admin panel.**

Each app talks to users over Telegram, reasons through a local Ollama LLM, calls external tools via the Model Context Protocol (MCP), runs on a cron schedule, and can generate rich PDF reports on demand.

</div>

<div align="center">

[![Tests](https://img.shields.io/badge/CI-pytest-blue?logo=githubactions&logoColor=white)](.github/workflows/master.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django 5.2](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-000?logo=ollama)](https://ollama.com)
[![MCP](https://img.shields.io/badge/Tools-MCP-8A2BE2)](https://modelcontextprotocol.io)

</div>

---

## ✨ Features

- 🤖 **Per-bot Telegram agents** — Create as many independent bots as you need; each with its own system prompt, personality, and Telegram token.
- 🧠 **Local LLM via Ollama** — Runs entirely against a self-hosted Ollama instance. Bring your own model (`llama3`, `mistral`, `phi3`, …) provided it supports tool calling.
- 🔌 **MCP tool integration** — Connect any [Model Context Protocol](https://modelcontextprotocol.io) server (local stdio or remote HTTPS) to extend a bot's capabilities.
- 🧭 **Semantic MCP tool ranking** — MCP servers are re-ranked per message using pgvector cosine similarity, so the most relevant tools surface first.
- ⏰ **Scheduled cron jobs** — Bots can schedule themselves: create, list, update, and delete jobs through the LLM using cron expressions. Embedding-driven tool selection for scheduled tasks too.
- 📄 **On-demand PDF reports** — Built-in PDF generator MCP server turns LLM-authored HTML into styled PDFs and ships them directly to the user's Telegram chat.
- 🧠 **Semantic memory (pgvector)** — Every user message, cron job schedule, and MCP tool description is embedded for similarity retrieval and used to enrich context.
- 📝 **Conversation summarisation** — Older messages are summarised into a system-role message when the context window tightens, preserving recent verbatim history.
- 🎯 **Observed user patterns** — An async worker periodically rebuilds a behavioural profile of the user (communication style, recurring topics, preferences) and injects it into the system prompt.
- 🔐 **Encrypted secrets at rest** — Telegram bot tokens and MCP server secrets are Fernet-encrypted in the DB, keyed off `SECRET_KEY`; lookup happens via deterministic SHA-256 hash.
- 🛠️ **Ops tooling** — Celery + Redis for the worker pool, Celery Beat for scheduling, Flower at `:5555` for queue inspection, structured JSON logs everywhere.

---

## 🧱 Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Django 5.2 |
| Async tasks | Celery 5.6 + Celery Beat |
| Broker / Cache | Redis 7 |
| Database | PostgreSQL 16 with [pgvector](https://github.com/pgvector/pgvector) |
| LLM | [Ollama](https://ollama.com) (any tool-calling-capable model) |
| Tool protocol | [Model Context Protocol](https://modelcontextprotocol.io) (FastMCP + `mcp` SDK) |
| PDF rendering | WeasyPrint |
| Admin Markdown | [Martor](https://github.com/agusmakmun/django-markdown-editor) |
| WSGI server | Gunicorn |
| Container | Docker + Docker Compose |

---

## 🏛️ Architecture (at a glance)

```text
┌─────────────────────────────────────────────────────────┐
│                     Django (Core)                       │
│  Admin Panel  │  REST API  │  Models  │  Business Logic │
└───────────────────────────┬─────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
   ┌──────▼──────┐  ┌───────▼──────┐  ┌──────▼──────┐
   │   Celery    │  │   Ollama     │  │  Telegram   │
   │   Workers   │  │  (Local LLM) │  │   Bot API   │
   │  + Beat DB  │  │              │  │             │
   └──────┬──────┘  └─────┬────────┘  └──────┬──────┘
          │               │                  │
   ┌──────▼──────┐ ┌──────▼──────┐  ┌────────▼──────┐
   │    Redis    │ │ pgvector    │  │  Telegram    │
   │  (Broker)   │ │ (Embeddings)│  │  Polling /   │
   └─────────────┘ └─────────────┘  │  Webhook     │
                                    └──────────────┘
```

> 📐 For the full architecture, data models, request flows, and Celery task reference, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## 🚀 Quick Start (Docker)

> Prerequisite: Docker + Docker Compose installed.

```bash
# 1. Clone the repository
git clone <your-fork-url> WhimsyBots
cd WhimsyBots

# 2. Configure environment
cd src
cp .env.example .env
# Edit .env — set SECRET_KEY, DB_PASSWORD, WEBHOOK_BASE_URL, etc.

# 3. Build images
make build

# 4. Start services (db, redis, app, celery_worker, celery_beat, flower)
make up

# 5. Run migrations and create an admin user
make migrate
make createsuperuser

# 6. Open the admin
open http://localhost:8000/admin/
```

That's it. The admin panel is the only UI — every bot, MCP server, cron job, and Ollama config is configured there.

---

## ⚙️ First-time Configuration Checklist

After `make createsuperuser`, log into `http://localhost:8000/admin/` and complete these steps **in order**:

1. **Pull your Ollama models** on the host (not in Docker):
   ```bash
   ollama pull llama3          # or any tool-calling-capable model
   ollama pull nomic-embed-text # or your preferred embedding model
   ```
2. **Add an `Ollama` configuration** (`/admin/app/ollama/add/`):
   - Endpoint: `http://host.docker.internal:11434` (when running in Docker)
   - `num_ctx`: 4096 minimum; raise if your hardware allows.
3. **Create a `Bot`** (`/admin/app/bot/add/`):
   - Pick the LLM model and embedding model you just pulled.
   - Set `embedding_dimensions` to match the embedding model (e.g. 768 for `nomic-embed-text`).
   - Author a system prompt (Markdown supported).
   - Paste a Telegram bot token from [@BotFather](https://t.me/BotFather).
4. **Register the webhook** by saving the bot — a `setup_bot_webhook` Celery task fires automatically. Ensure `WEBHOOK_BASE_URL` in `.env` is publicly reachable over HTTPS.
5. **Send your bot a message** on Telegram. The chat ID is auto-populated, an embedding is generated, and the bot responds.

> 💡 For a deeper walkthrough of all `make` targets, the project layout, and coding conventions, see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

---

## 📚 Documentation

| Document | What's inside |
|---|---|
| [README.md](README.md) *(this file)* | Project overview, features, quick start, first-time setup |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | High-level diagram, request flows, data models, Celery task reference, retry strategy |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Contributor onboarding, project layout, coding conventions, testing, debugging |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues with Ollama, Telegram, Celery, database, tests, and local dev |

---

## 🗂️ Project Layout

```text
WhimsyBots/
├── src/                          # All application code lives here
│   ├── app/                      # Core Django app — models, admin, tasks, validators
│   ├── clients/                  # External API clients (Telegram, Ollama, MCP)
│   ├── managers/                 # Lightweight factory / cache managers
│   ├── services/                 # Business logic (bot processor, embeddings, rate limiter, …)
│   ├── cron_job/                 # Standalone Cron Job MCP server (FastMCP)
│   ├── pdf_generator/            # Standalone PDF Generator MCP server (FastMCP)
│   ├── utils/                    # Cross-cutting helpers (crypto, formatting, scheduling, …)
│   ├── tests/                    # Pytest suite (test_app/, test_services/, test_utils/, …)
│   ├── whimsybots/               # Django project (settings/, urls.py, views.py, celery.py, wsgi.py)
│   ├── user/                     # Django migrations for the user app
│   ├── manage.py
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── Makefile
│   └── requirements.txt
├── docs/                         # All in-depth documentation
│   ├── ARCHITECTURE.md
│   ├── DEVELOPMENT.md
│   └── TROUBLESHOOTING.md
├── Makefile                      # Top-level convenience targets (cd src && …)
├── README.md                     # ← you are here
└── LICENSE                       # MIT
```

> 📁 Full annotated layout, including every module's purpose, lives in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#project-layout).

---

## 🧪 Testing

```bash
# Run the full suite
make test

# Verbose or a single test:
make test-verbose
make test-specific FILE=test_app/test_models.py::TestBot::test_str
```

Tests use the dedicated `whimsybots.settings.test` module — SQLite in-memory, `fakeredis` for Celery/cache, and a pgvector shim — so they don't need a running Postgres or Ollama.

---

## 🤝 Contributing

PRs welcome. Please read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for project conventions (type hints, docstring style, error handling, how to add models / tasks / MCP servers) before opening a pull request. CI runs the pytest suite on every push to `master` and every PR.

---

## 📄 License

This project is licensed under the [MIT LICENSE](LICENSE)
