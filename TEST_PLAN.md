# WhimsyBots — Test Coverage Plan

> **Status:** Inventory + roadmap only. No test code is being written in this document.
> **Generated:** June 25, 2026
> **Current coverage:** 0% (only the empty `src/tests/__init__.py` and Django's placeholder `src/app/tests.py` exist).
> **Harness state:** Fully wired — see [Existing Test Infrastructure](#existing-test-infrastructure).

---

## Table of Contents

1. [Goal & Approach](#goal--approach)
2. [Existing Test Infrastructure](#existing-test-infrastructure)
3. [Tiered Inventory](#tiered-inventory)
   - [Tier 1 — Pure Logic](#tier-1--pure-logic-no-io)
   - [Tier 2 — Service Logic with Mocks](#tier-2--service-logic-with-mocks)
   - [Tier 3 — Higher-Level Orchestration](#tier-3--higher-level-orchestration)
   - [Tier 4 — External-System Clients](#tier-4--external-system-clients)
   - [Tier 5 — Views / Admin / Shell](#tier-5--views--admin--shell)
   - [Tier 6 — Cron / PDF MCP Subsystems](#tier-6--cron--pdf-mcp-subsystems)
4. [Proposed Test File Layout](#proposed-test-file-layout)
5. [Cross-Cutting Concerns](#cross-cutting-concerns)
6. [Implementation Phases](#implementation-phases)
7. [Verification Checklist](#verification-checklist)
8. [Out of Scope](#out-of-scope)

---

## Goal & Approach

Move the WhimsyBots codebase from **0% coverage** to a meaningful baseline by writing pytest tests for every testable module. Tests are organized by priority tier so the highest-ROI work (pure utilities + validators) is delivered first.

**Test framework:** `pytest` with `pytest-asyncio` (auto mode).
**Test style:** Pure `pytest` functions (not `django.test.TestCase`) for speed and explicitness. Django's `TestCase` is used only when transaction semantics differ.
**Mocking:** `unittest.mock` from stdlib; `pytest-mock` may be added later for the `mocker` fixture.

---

## Existing Test Infrastructure

The harness is **already in place** — no setup work needed before writing tests.

| File | Purpose |
|---|---|
| `src/pytest.ini` | `asyncio_mode = auto`, `testpaths = tests`, `python_files = tests.py test_*.py *_test.py` |
| `src/conftest.py` | Provides `django_db_setup`, `fake_redis`, `fake_redis_server` fixtures |
| `src/whimsybots/settings/test.py` | In-memory SQLite DB + `ArrayField`→`JSONField` shim + `pgvector.django` stub + `LocMemCache` |
| `src/tests/__init__.py` | Empty (ready for new tests) |
| `src/app/tests.py` | Django default placeholder (`# Create your tests here.`) |

---

## Tiered Inventory

Every module below has **public functions or classes that should be tested**. Tests counts are ballpark (specific to functions found, not "many").

### Tier 1 — Pure Logic (no I/O)

These modules have **no I/O dependencies** and are the highest ROI. They can be tested without DB fixtures, fakeredis, or HTTP mocks.

| Module | Public Surface | ~Tests | Key Edge Cases |
|---|---|---|---|
| `src/utils/crypto.py` | `get_fernet`, `encrypt`, `decrypt`, `get_token_hash` | 6–8 | round-trip; bad ciphertext raises; deterministic SHA-256 hex digest |
| `src/utils/formatting.py` | `convert_messages_to_ollama_format`, `get_admin_link` | 5–7 | role mapping (`U`/`A` only; skip `S`); system prompt prepend; `"0"` fallback when obj/value missing |
| `src/utils/scheduling.py` | `calculate_next_run_at` | 3–4 | timezone-aware datetime in the future |
| `src/utils/telegram.py` | `parse_telegram_update` | 6–8 | missing `message` returns `None`; strips text; stringifies chat_id; default empty username/first_name |
| `src/utils/text.py` | `split_message` + 3 private helpers (`_find_split_point`, `_get_unclosed_fence`, `_needs_fence_prefix`) | 8–12 | **highest-value single file** — 6-priority split (closing ``` → \n\n → \n → sentence → space → hard cut); code-fence preservation; language-tag reopen |
| `src/app/validators.py` | `validate_cron_expression`, `validate_transport_fields`, `validate_keep_alive` | 10–12 | empty/invalid cron; 4 transport branches (LOCAL w/o cmd, REMOTE w/o endpoint, valid LOCAL, valid REMOTE); regex for `-1`/`0`/`<int>s/m/h` |
| `src/app/choices.py` | `BaseChoices.get_values`, `get_readable`; subclasses `MCPTransportType`, `MessageRole` | 6–8 | unknown code raises `ValueError` |
| `src/app/fields.py` | `EncryptedCharField` + `EncryptedJSONField` (3 hooks each: `from_db_value`, `to_python`, `get_prep_value`) | 10–14 | round-trip plaintext↔ciphertext; dict↔encrypted-JSON round-trip; decrypt-failure passthrough (legacy data); avoid double-encrypt |
| `src/services/token_budget.py` | `TokenBudgetService.compute`, `truncate_text`, `fit_messages_to_token_budget`, `fit_embeddings_to_char_budget`, `truncate_tool_response`, `_measure_tool_def_tokens`; `TokenBudget` pydantic model | 14–18 | oldest-dropped-first history; lowest-similarity-dropped embeddings; JSON-serialized tool defs + safety buffer |
| `src/services/rate_limiter.py` | `RateLimiter.acquire` | 4–6 | first call `True`; 2nd call in same window `False`; TTL expiry → next call `True` again |

**Subtotal Tier 1:** ~72–100 tests across 10 modules.

---

### Tier 2 — Service Logic with Mocks

These have minor dependencies (Redis cache, Django ORM cache) but can be tested without HTTP mocks.

| Module | Public Surface | ~Tests | Key Notes |
|---|---|---|---|
| `src/managers/ollama_config.py` | `OllamaConfigManager.get_ollama_config` | 3–5 | **Module-level `_cached_ollama` cache** — tests need `autouse` fixture to reset. |
| `src/managers/telegram_client.py` | `TelegramClientManager.create_client` | 2–3 | Patch `clients.TelegramClient` constructor; verify token + chat_id wiring. |
| `src/utils/tasks.py` | `get_ollama_cfg`, `get_bot_obj`, `get_cron_obj`, `get_msg_obj`, `_get_by_id_or_log`, `create_log`, `build_error_description`, `log_task_failure`, `_should_skip_message_embedding`, `generate_message_embedding`, `generate_cron_job_embedding`, `generate_mcp_embedding` | 12–16 | `DoesNotExist` → `None` + log; ORM row creation; embedding dispatcher delegation |

**Subtotal Tier 2:** ~17–24 tests across 3 modules.

---

### Tier 3 — Higher-Level Orchestration

Services that orchestrate multiple collaborators. Heavy use of `unittest.mock.patch` for clients and ORM.

| Module | Public Surface | ~Tests | Key Notes |
|---|---|---|---|
| `src/services/observed_patterns.py` | `ObservedPatternsService.should_regenerate`, `regenerate`, `_format_history`, `_call_llm`; constants `MAX_PATTERN_CHARS=4096`, `PATTERN_REGEN_EVERY_N_MESSAGES=20`, `PATTERN_ANALYSIS_MESSAGE_LIMIT=100` | 10–12 | mock `OllamaClient.chat`; SYSTEM messages skipped in history; truncation at MAX_PATTERN_CHARS |
| `src/services/embedding.py` | `EmbeddingService._build_text_for_cron_job`, `_build_text_for_mcp_server`, `_generate`, `save_message_embedding`, `save_cron_job_embedding`, `save_mcp_embedding`, `get_relevant_memories` | 14–18 | empty content → False; missing embedding_model → False; dimension mismatch early return; distance-to-similarity `1 - (distance/2)` conversion |
| `src/services/tool_executor.py` | `MCPToolConfig` dataclass + `.get_transport`; `MCPToolsBuilder.build_tools_from_servers`, `_build_server_config`, `_get_transport_type`; `ToolExecutor.find_tool_by_call`, `execute_tool`, `execute_tool_call_sync` | 10–14 | LOCAL→`command/args/env`; REMOTE→`url/headers`; isError path returns `TOOL_EXECUTION_FAILED`; tool-not-found returns `INVALID_TOOL.format(...)` |
| `src/services/tool_calling_coordinator.py` | `run_tool_calling_loop` | 6–8 | no-tool-calls terminates loop; tool-call appends `{role:tool}` to history; `add_keep_alive=True` passes `ollama.keep_alive`, else `None` |
| `src/services/telegram_update_handler.py` | `TelegramUpdateHandler.handle_update` | 7–10 | **Local import** `from app.tasks import generate_embedding` — patch at `app.tasks` module level (not at the importer). Sets chat_id on first message; queues embedding task. |
| `src/services/context_assembler.py` | `ContextAssembler.assemble`, `_fit_patterns`, `_fit_memories`, `_fit_history`; constants `SECTION_SEP`, `RECENT_MESSAGES_CAP=200`; `AssembledContext` pydantic | 10–14 | Section assembly with `SECTION_SEP`; truncation logging; budget-aware pattern/memory/history fitting |
| `src/services/bot_processor.py` | `BotMessageProcessor._get_tools_config`, `process_message`, `process_cron_job`, `send_response` | 10–14 | **Uses `asyncio.run` inside sync `_get_tools_config`** — tests must patch `asyncio.run` OR the call sites need a refactor (see [Cross-Cutting Concerns](#cross-cutting-concerns)). `process_cron_job` excludes `cron_job` server; uses `CRON_JOB_PROMPT` template. |
| `src/services/conversation_summary.py` | `ConversationSummaryService._set_summary_budget`, `process`, `_split_by_budget`, `_summarize` | 8–12 | Reads `self.bot.conversations` / `system_messages` — verify these are real prefetch-related attributes on the Bot model before writing tests. |
| `src/app/tasks.py` | 8 Celery tasks: `telegram_msg_handler`, `cron_job_poller`, `setup_bot_webhook`, `process_cron_job`, `process_inbound_message`, `generate_embedding`, `manage_conversation_summary`, `update_observed_patterns` + 5 private helpers | 18–24 | **Largest single test file.** Call `.run(...)` directly; patch `self.retry` per task. Verify `apply_async` kwargs including `eta` for cron jobs. |
| `src/app/forms.py` | `BotForm`, `MCPServerForm`; helpers `fetch_ollama_models_with_capabilities`, `get_models_from_cache`, `save_models_to_cache`, `get_filtered_models` | 12–16 | `BotForm.clean` rejects duplicate `telegram_bot_token` (excludes self on update); `BotForm._populate_model_choices` falls back to `EMPTY_MODEL_CHOICES` when no Ollama; cache-first strategy. |

**Subtotal Tier 3:** ~105–134 tests across 9 modules.

---

### Tier 4 — External-System Clients

Tests for clients that wrap external HTTP APIs. Heavy mocking of `requests`, `ollama`, `httpx`, `mcp`.

| Module | Public Surface | ~Tests | Key Notes |
|---|---|---|---|
| `src/clients/telegram.py` | `TelegramClient`, `TelegramError`, `TelegramRateLimitError`; methods `_post`, `_acquire_rate_limit`, `send_message`, `send_typing_action`, `set_webhook` | 12–16 | Patch `requests.post`; patch `redis.from_url` to inject `fake_redis`; 429 → raises `TelegramRateLimitError(retry_after)`; markdown parse failure → retry without `parse_mode` |
| `src/clients/ollama.py` | `OllamaClient._get_clean_endpoint`, `__init__`, `list_models`, `chat`, `generate_embeddings`, `fetch_model_capabilities` | 10–14 | localhost → host.docker.internal; keep_alive normalization (`"-1"`/`"0"` → int, else string); Bearer header when api_key present; flatten nested-list embedding response |
| `src/clients/mcp.py` | `MCPClient.__init__`, `_connect`, `list_tools`, `execute_tool`, `cleanup`; `mcp_client()` convenience async function | 10–14 | Use `AsyncMock` for `ClientSession`, `stdio_client`, `streamable_http_client`; MCP `inputSchema` → Ollama `{type:function,function:{name,description,parameters}}` conversion |

**Subtotal Tier 4:** ~32–44 tests across 3 modules.

---

### Tier 5 — Views / Admin / Shell

Django surface area. Lower priority — mostly UI glue.

| Module | Public Surface | ~Tests | Key Notes |
|---|---|---|---|
| `src/whimsybots/views.py` | `TelegramWebhook.post` | 4–6 | Use `RequestFactory`; missing token → `HttpResponseBadRequest("No bot token provided")`; empty payload → `HttpResponseBadRequest("Empty payload received")`; valid → queues `telegram_msg_handler.apply_async(...)`; returns `HttpResponse()` |
| `src/app/admin.py` | `BaseUserFilteredAdmin`, `BaseReadOnlyUserFilteredAdmin`, `OllamaAdmin`, `BotAdmin`; methods `get_queryset`, `has_add_permission`, `save_model`, `_render_stats_table` | 8–12 | `BaseReadOnlyUserFilteredAdmin` denies all `has_*_permission`; `OllamaAdmin.has_add_permission` only when zero rows; `OllamaAdmin.save_model` triggers `manage_conversation_summary.apply_async` when `num_ctx` changed; `BotAdmin.get_queryset` annotations (`messages_count`, `mcp_active_count`, `cron_active_count`, `logs_*_count`, `last_log_id`, `last_log_success`) |
| `src/app/models.py` | `Bot.save`, `MCPServer.clean`, `MCPServer.get_default_mcp_servers` | 6–10 | `Bot.save` populates `telegram_bot_token_hash` only when token provided; `MCPServer.get_default_mcp_servers` returns `cron_job` / `time` / `pdf_generator` with LOCAL transport and DB creds in secrets |

**Subtotal Tier 5:** ~18–28 tests across 3 modules.

---

### Tier 6 — Cron / PDF MCP Subsystems

Lower ROI because they require environment-variable fixtures. Pure helpers in this tier are still high value.

| Module | Public Surface | ~Tests | Key Notes |
|---|---|---|---|
| `src/cron_job/db.py` | `_build_db_url`, `get_session` async context manager | 3–5 | **Module-import side effect:** `_build_db_url()` runs at import and raises `EnvironmentError` if `DB_NAME`/`DB_USER`/`DB_PASSWORD` unset. Needs env-var fixture. `get_session` rolls back on `SQLAlchemyError`. |
| `src/pdf_generator/db.py` | Same as above | 3–5 | Same env-var constraint. |
| `src/cron_job/helpers.py` | `validate_cron`, `calc_next_run`, `parse_uuid`, `fmt_job`, `fmt_jobs` | 6–8 | `parse_uuid` has dual return: `uuid.UUID` on success or markdown error `str` on failure — one test per branch. |
| `src/pdf_generator/helpers.py` | `generate_pdf`, `extract_html` + likely `decrypt_token`, `send_document`, `parse_uuid` | 6–8 | `extract_html` is high-value — 4-step regex extraction (```html → generic ``` block → raw HTML → partial fallback). `generate_pdf` wraps weasyprint (needs weasyprint installed in test env). |
| `src/cron_job/server.py` | `@mcp.tool()` async functions: `list_cron_jobs`, `create_cron_job`, `update_cron_job`, `delete_cron_job` | 4–6 | Patch `get_session`; verify `parse_uuid` error branch returns the error string. |
| `src/pdf_generator/server.py` | `@mcp.tool()` async function: `generate_and_send_report` | 2–4 | Patch `get_session`, `send_document`, `generate_pdf`; verify full orchestration. |

**Subtotal Tier 6:** ~24–36 tests across 6 modules.

---

## Proposed Test File Layout

The structure mirrors the source layout and satisfies the `python_files = tests.py test_*.py *_test.py` discovery pattern in `pytest.ini`.

```
src/tests/
├── __init__.py                          (already exists, empty)
├── test_utils/
│   ├── __init__.py
│   ├── test_crypto.py                   Tier 1
│   ├── test_formatting.py               Tier 1
│   ├── test_scheduling.py               Tier 1
│   ├── test_telegram.py                 Tier 1
│   ├── test_text.py                     Tier 1 (highest value)
│   └── test_tasks.py                    Tier 2
├── test_app/
│   ├── __init__.py
│   ├── test_choices.py                  Tier 1
│   ├── test_fields.py                   Tier 1
│   ├── test_validators.py               Tier 1
│   ├── test_models.py                   Tier 5
│   ├── test_forms.py                    Tier 3
│   ├── test_admin.py                    Tier 5
│   └── test_tasks.py                    Tier 3 (largest)
├── test_services/
│   ├── __init__.py
│   ├── test_rate_limiter.py             Tier 1
│   ├── test_token_budget.py             Tier 1
│   ├── test_observed_patterns.py        Tier 3
│   ├── test_embedding.py                Tier 3
│   ├── test_tool_executor.py            Tier 3
│   ├── test_tool_calling_coordinator.py Tier 3
│   ├── test_telegram_update_handler.py  Tier 3
│   ├── test_context_assembler.py        Tier 3
│   ├── test_bot_processor.py            Tier 3
│   └── test_conversation_summary.py     Tier 3
├── test_clients/
│   ├── __init__.py
│   ├── test_telegram_client.py          Tier 4
│   ├── test_ollama_client.py            Tier 4
│   └── test_mcp_client.py               Tier 4
├── test_managers/
│   ├── __init__.py
│   ├── test_ollama_config.py            Tier 2
│   └── test_telegram_client.py          Tier 2
├── test_whimsybots/
│   ├── __init__.py
│   └── test_views.py                    Tier 5
├── test_cron_job/
│   ├── __init__.py
│   ├── test_helpers.py                  Tier 6
│   ├── test_db.py                       Tier 6
│   └── test_server.py                   Tier 6
└── test_pdf_generator/
    ├── __init__.py
    ├── test_helpers.py                  Tier 6
    └── test_server.py                   Tier 6
```

**Total:** ~34 test files, ~180–280 tests.

---

## Cross-Cutting Concerns

These issues affect multiple tiers and need decisions before (or during) test writing.

### 1. `asyncio.run()` inside sync functions

`services/bot_processor.py` (`_get_tools_config`) and `services/embedding.py` (`_build_text_for_mcp_server`) call `asyncio.run(...)` from synchronous code. This blocks the event loop and is awkward to test.

**Options:**
- **A.** Tests patch `asyncio.run` to delegate to a sync wrapper.
- **B.** Refactor call sites to `await` directly (changes function signatures).
- **Decision needed:** before Tier 3 (services).

### 2. Module-level caches must be reset

`OllamaConfigManager._cached_ollama` is a class-level attribute that persists across tests.

**Solution:** Add an `autouse` fixture to `src/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _reset_ollama_cache():
    from managers.ollama_config import OllamaConfigManager
    OllamaConfigManager._cached_ollama = None
    yield
    OllamaConfigManager._cached_ollama = None
```

### 3. Celery `bind=True` tasks

Test by calling `.run(...)` directly. Patch `self.retry` per task:

```python
def test_telegram_msg_handler_retries(mocker):
    mocker.patch("app.tasks.TelegramUpdateHandler.handle_update", side_effect=RuntimeError)
    mocker.patch.object(telegram_msg_handler, "retry")
    telegram_msg_handler.run("token", {})
    telegram_msg_handler.retry.assert_called_once()
```

### 4. Circular-import workaround in `telegram_update_handler.py`

The function does `from app.tasks import generate_embedding` *inside* its body to dodge a circular import. Tests must patch at the `app.tasks` module level, not at `services.telegram_update_handler`.

```python
mocker.patch("app.tasks.generate_embedding.apply_async")
```

### 5. `pytest-django` requirement

`@pytest.mark.django_db` (needed for all model/admin/form/task tests) requires `pytest-django` to be in `requirements.txt` and listed in `pytest_plugins` (or auto-loaded). **Verify before Tier 3.**

### 6. Async services in Tier 3–4

`mcp.py`, `tool_executor.py` async methods, and `bot_processor.py` internal `asyncio.run` calls all need `@pytest.mark.asyncio` (already auto-enabled) or `AsyncMock`.

---

## Implementation Phases

### Phase 1 — Tier 1 Quick Wins (~6–8 hours)

Pure utilities + validators + choices + fields. Establishes the test-runner flow, proves conftest fixtures work, builds confidence.

1. `tests/test_utils/test_crypto.py` (6–8 tests)
2. `tests/test_utils/test_formatting.py` (5–7)
3. `tests/test_utils/test_scheduling.py` (3–4)
4. `tests/test_utils/test_telegram.py` (6–8)
5. `tests/test_utils/test_text.py` (8–12) ← **highest-value single file**
6. `tests/test_app/test_choices.py` (6–8)
7. `tests/test_app/test_validators.py` (10–12)
8. `tests/test_app/test_fields.py` (10–14)

### Phase 2 — Tier 1 + 2 Service Logic (~4–6 hours)

9. `tests/test_services/test_token_budget.py` (14–18)
10. `tests/test_services/test_rate_limiter.py` (4–6)
11. `tests/test_managers/test_ollama_config.py` (3–5)
12. `tests/test_managers/test_telegram_client.py` (2–3)
13. `tests/test_utils/test_tasks.py` (12–16)

### Phase 3 — Tier 3 Services with Mocked Clients (~10–14 hours)

14. `tests/test_services/test_observed_patterns.py`
15. `tests/test_services/test_embedding.py`
16. `tests/test_services/test_tool_executor.py`
17. `tests/test_services/test_tool_calling_coordinator.py`
18. `tests/test_services/test_telegram_update_handler.py`
19. `tests/test_services/test_context_assembler.py`
20. `tests/test_services/test_bot_processor.py`
21. `tests/test_services/test_conversation_summary.py`
22. `tests/test_app/test_forms.py`
23. `tests/test_app/test_models.py`
24. `tests/test_app/test_tasks.py` ← **largest single test file**

### Phase 4 — Tier 4 External Clients (~10–14 hours)

25. `tests/test_clients/test_ollama_client.py`
26. `tests/test_clients/test_telegram_client.py`
27. `tests/test_clients/test_mcp_client.py`

### Phase 5 — Tier 5 + 6 Views / Admin / MCP (~6–10 hours)

28. `tests/test_whimsybots/test_views.py`
29. `tests/test_app/test_admin.py`
30. `tests/test_cron_job/test_helpers.py`
31. `tests/test_cron_job/test_db.py`
32. `tests/test_cron_job/test_server.py`
33. `tests/test_pdf_generator/test_helpers.py`
34. `tests/test_pdf_generator/test_server.py`

**Total estimate:** 180–280 tests across 34 files, ~50–70 hours of writing.

---

## Verification Checklist

After each phase, run from `src/`:

1. **Tier-isolated run:** `pytest tests/test_utils -v` (or relevant subdir) — all new tests pass.
2. **Discovery check:** `pytest --collect-only` — confirms pytest picks up new files.
3. **No import errors:** `pytest --tb=short` — confirms existing modules still import cleanly.
4. **Coverage delta:** `pytest --cov=src --cov-report=term-missing` — requires `pytest-cov` in `requirements.txt`. Measures coverage gain per phase.
5. **Async sanity:** `pytest tests/test_services tests/test_clients -v` — confirms `asyncio_mode=auto` is working.

---

## Out of Scope

- **Integration tests** against real Ollama / Telegram / PostgreSQL. Those would belong in a separate `tests/integration/` suite and require live credentials + Docker compose.
- **Performance / load tests.** Not requested.
- **Mutation testing** (e.g., `mutmut`). Could be a follow-up after baseline coverage is established.
- **Excluded modules** (boilerplate, config, migrations):
  - `whimsybots/settings/{base,test,__init__}.py`
  - `whimsybots/urls.py`, `whimsybots/wsgi.py`, `whimsybots/celery.py`
  - `manage.py`, `gunicorn.conf.py`, `conftest.py`, `pytest.ini`
  - All `migrations/`
  - All `__init__.py`
  - `app/apps.py`

---

## Summary Table

| Tier | Modules | Tests | Hours |
|---|---|---|---|
| 1 — Pure logic | 10 | 72–100 | 6–8 |
| 2 — Service mocks | 3 | 17–24 | 4–6 |
| 3 — Orchestration | 9 | 105–134 | 10–14 |
| 4 — External clients | 3 | 32–44 | 10–14 |
| 5 — Views/Admin | 3 | 18–28 | 3–5 |
| 6 — Cron/PDF MCP | 6 | 24–36 | 3–5 |
| **Total** | **34** | **~180–280** | **~50–70** |
