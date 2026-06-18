# Troubleshooting Guide

This guide provides common issues, symptoms, and resolutions for components in the WhimsyBots system.

## Overview
Most issues are related to connectivity between the application container and external services (Ollama, Telegram, Redis/Celery). Ensure your environment variables in `.env` or `docker-compose.yml` match the network topology of your deployment.

---

## 1. Ollama Connectivity & Model Issues
The system interacts with an Ollama instance for local LLM processing.

| Symptom | Possible Cause | Resolution |
| :--- | :--- | :--- |
| `ollama.ResponseError` or Connection Refused | Incorrect endpoint URL; Docker network mismatch. | If running in Docker, ensure the URL is set to `http://host.docker.internal:11434`. Outside of Docker, use `http://localhost:11434`. |
| "Model not found" or missing capabilities | Ollama service is running but doesn't have the specific model pulled. | Run `ollama pull <model_name>` (e.g., `llm pull llama3`) on your host machine to ensure the local instance has the required weights. |
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
