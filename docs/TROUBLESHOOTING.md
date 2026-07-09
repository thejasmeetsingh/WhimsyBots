# Troubleshooting Guide

> **Documentation Map**
> - [README](../README.md) — Project overview, features, and quick start
> - [ARCHITECTURE](ARCHITECTURE.md) — Technical deep dive, data models, request flows
> - [DEVELOPMENT](DEVELOPMENT.md) — Contributor onboarding, project layout, coding conventions

This guide provides common issues, symptoms, and resolutions for components in the WhimsyBots system.

## Overview
Most issues are related to connectivity between the application container and external services (Ollama, Telegram, Redis/Celery). Ensure your environment variables in `.env` or `docker-compose.yml` match the network topology of your deployment.

### First-step Health Checks
Before diving into the table below, run these quick commands:

```bash
# Are all containers running?
make ps

# Are the database and Redis healthy?
docker compose -f src/docker-compose.yml ps

# Are the Celery workers and beat connected?
# Open the Flower dashboard at http://localhost:5555

# Is Ollama reachable from the app container?
docker compose -f src/docker-compose.yml exec app curl -s http://host.docker.internal:11434/api/tags

# Are recent tasks succeeding or failing?
docker compose -f src/docker-compose.yml logs --tail=200 celery_worker | grep -i 'error\|retry'
```

---

## 1. Ollama Connectivity & Model Issues
The system interacts with an Ollama instance for local LLM processing.

| Symptom | Possible Cause | Resolution |
| :--- | :--- | :--- |
| `ollama.ResponseError` or Connection Refused | Incorrect endpoint URL; Docker network mismatch. | If running in Docker, ensure the URL is set to `http://host.docker.internal:11434`. Outside of Docker, use `http://localhost:11434`. |
| "Model not found" or missing capabilities | Ollama service is running but doesn't have the specific model pulled. | Run `ollama pull <model_name>` (e.g., `ollama pull llama3`) on your host machine to ensure the local instance has the required weights. Embedding models (e.g. `nomic-embed-text`) must also be pulled explicitly. |
| Slow responses / Timeout | Model too large for hardware or `num_ctx` set too high. | Check system resources and try a smaller model (e.g., `phi3`, `mistral`) or reduce `num_ctx` in the configuration. |

## 2. Telegram Integration Issues
Issues involving bot messages, webhooks, and rate limits.

| Symptom | Possible Cause | Resolution |
| :--- | :--- | :--- |
| **429 Too Many Requests** | Hit Telegram's global or local (RateLimiter) thresholds. | The system handles these automatically via `RateLimiter`. If persists, check if you are sending too many messages in a loop without delay. |
| "Can't parse entities" (Markdown Error) | Invalid special characters in the prompt or automated response that break Markdown formatting. | The system automatically falls back to plain text for such errors. To avoid this entirely, ensure user input is cleaned of `*`, `_`, and `[` characters unless intended for bolding/italics. |
| Webhook not receiving updates | Incorrect URL provided during initialization or missing HTTPS. | Verify the webhook URL in your configuration. It must be a valid **HTTPS** address accessible by Telegram's servers. |

## 3. Celery & Background Tasks
Issues with long-running tasks (e.g., document generation, processing).

| Symptom | Possible Cause | Resolution |
| :--- | :--- | :--- |
| Worker not connecting to Broker | Redis service is down or incorrect credentials in `CELERY_BROKER_URL`. | Check the status of your message broker (Redis) and verify that the port in `.env` matches the host's port. |
| Task "Pending" for long duration | Worker is under heavy load or task failed silently without retrying. | Check Celery worker logs (`celery -A whimsybots.main:app worker -l info`) or go to `http://localhost:5555` to see if tasks are being picked up and processed. |

## 4. Database & General System
| Symptom | Possible Cause | Resolution |
| :--- | :--- | :--- |
| **Database connection error** | PostgreSQL/MySQL service is not running or credentials in `DATABASE_URL` are incorrect. | Check the status of your DB container and ensure the database name exists. |
| Memory Leak / Process Restart | Python process exceeded container memory limits (OOM). | Increase the memory limit for the main app container in `docker-compose.yml`. |

---

## Quick Reference: Local Development Defaults
If you are running locally without a complex setup, ensure these defaults are met:
- **Ollama URL:** `http://localhost:11434` (or `host.docker.internal` if in Docker)
- **Telegram Token:** Valid token from @BotFather.
- **Redis:** Running on standard ports (`6379`, `5672`).

---

## 5. Tests & Local Development

| Symptom | Possible Cause | Resolution |
| :--- | :--- | :--- |
| `ImportError: No module named 'pgvector.django'` when running `pytest` | The test settings module has not been loaded yet — `pgvector` is shimmed inside `whimsybots/settings/test.py`. | Ensure `pytest.ini` declares `DJANGO_SETTINGS_MODULE = whimsybots.settings.test` and you're running from the `src/` directory. |
| `django.contrib.postgres.fields.ArrayField` errors on SQLite | Tests run against SQLite (see `whimsybots/settings/test.py`). | The shim in `test.py` patches `ArrayField` to a `JSONField` subclass. Don't bypass the test settings module. |
| `OSError: cannot load library 'libgobject-2.0-0'` from WeasyPrint when running `pytest` | CI worker (or local Windows host) is missing WeasyPrint's native dependencies. | The `weasyprint` module is stubbed in `whimsybots/settings/test.py` *and* `src/conftest.py` so the import-time side effect in `mcp_tools/pdf_generator.py` doesn't blow up. Don't remove the stub. |
| `ImportError: No module named 'pgvector.django.vector'` while loading the generated `0002_initial.py` migration | The generated migration imports `pgvector.django.vector` directly; the parent `pgvector.django` package is shimmed but the `.vector` sub-module wasn't. | Already fixed in `whimsybots/settings/test.py` — both `pgvector.django` and `pgvector.django.vector` are stubbed at import time. If the error reappears, ensure the shim lines in `test.py` are still present. |
| `pgvector` errors after changing `Bot.embedding_dimensions` | Existing rows still hold vectors of the old size; pgvector enforces a strict dimension match. | Re-embed all messages (and any cron / MCP embeddings) after changing dimensions. The admin shows a warning next to the field — do not ship without re-embedding. |
| `Fernet: InvalidToken` after rotating `SECRET_KEY` | The Fernet key is derived from `SECRET_KEY` via SHA-256; rotating it invalidates every encrypted column. | Never rotate `SECRET_KEY` without first decrypting and re-encrypting existing rows. For development, drop and recreate the DB instead. |
| `IntegrityError` on `telegram_bot_token_hash` | Two bots ended up with the same hash (hash collision is astronomically unlikely — almost always a duplicate token). | The admin enforces uniqueness. If you see this in tests, make sure you're not reusing the same token across two test bots. |
| Cron job is created but never fires at the expected time | The bot might be inactive, missing `telegram_chat_id`, or its `is_active` flag may have flipped. | `cron_job_poller` only dispatches active jobs on active bots with a `telegram_chat_id`. Check `Bot.is_active` and `Bot.telegram_chat_id`. |
| `web_search` tool returns 5xx or `requests` timeout | The host running the worker has no outbound network access (or DDG is blocking the IP). | The worker calls `https://html.duckduckgo.com/html/` over HTTPS — ensure the network egress is open. |
| `fetch_and_extract` returns `Failed to fetch ...` for every URL | `trafilatura` is missing or the worker doesn't have outbound HTTPS. | `trafilatura` and `requests` are listed in `requirements.txt`; install them in the Docker image if a custom build stripped them. |
| `tool_response_chars` / `truncate_tool_response` referenced in old code/tests | The tool-response budget tier was removed. | Drop the references; the new token budget is documented in [ARCHITECTURE §9](ARCHITECTURE.md#9-token-budgeting-system-rewritten). |
| `MCPServer.get_default_mcp_servers` no longer exists | The default tool catalog was moved to `mcp_tools/` (in-process registry). | Use `from mcp_tools.tools import CRON_JOB_TOOLS, PDF_GENERATOR_TOOLS, WEB_SEARCH_TOOLS` instead. |
| Celery task runs but bot never responds in Telegram | The webhook is not reachable from the public internet, or `WEBHOOK_BASE_URL` is misconfigured. | Telegram requires an **HTTPS** endpoint reachable by their servers. Set `WEBHOOK_BASE_URL` to a public URL pointing at `/webhook/<bot_token>/` (use a tunnel like `ngrok` or a reverse proxy for local testing). |

---

## 6. Getting More Help

- **Architecture details** — see [ARCHITECTURE.md](ARCHITECTURE.md) for the full request flow, data model, and Celery task reference.
- **Contributor setup** — see [DEVELOPMENT.md](DEVELOPMENT.md) for environment setup, project layout, and testing conventions.
- **Logs and debugging** — `make logs-app`, `make logs-worker`, `make logs-scheduler`, and the Flower UI at `http://localhost:5555`.
- **Django shell** — `make shell` for ad-hoc ORM queries against a live container.
- **Redis CLI** — `make redis-shell` to inspect rate-limit keys (`tg_rate:*`) and Celery queues directly.
