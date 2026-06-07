# WhimsyBots - Prioritized Improvement Tasks

**Total Effort Estimate:** 280-375 hours  
**Recommended Duration:** 8-10 weeks (full-time)

---

## Quick Wins (Highest Priority - Do First!)

### Task 1: Create TROUBLESHOOTING.md Documentation
**Effort:** 4 hours | **Impact:** HIGH (reduces support burden)  
**Status:** Not Started

**Description:**
Create a troubleshooting guide documenting common issues, symptoms, and solutions.

**Deliverables:**
- Common error messages and recovery steps
- Database connection issues
- Ollama connection problems
- Telegram webhook failures
- Celery task failures
- Memory/performance issues

**Success Criteria:**
- Document covers top 10 failure scenarios
- Each scenario includes: symptom, cause, resolution
- Guide is discoverable in README

**Acceptance Tests:**
- [ ] File exists at `/TROUBLESHOOTING.md`
- [ ] At least 10 scenarios covered
- [ ] Each scenario has clear resolution steps
- [ ] README links to guide

---

### Task 2: Add Custom Exception Hierarchy
**Effort:** 8-12 hours | **Impact:** MEDIUM (clarifies error flow)  
**Status:** Not Started

**Description:**
Create a custom exception hierarchy for WhimsyBots to replace generic `Exception` catches.

**Deliverables:**
- Create `app/exceptions.py` with exception classes
- Define base `WhimsyBotsError` exception
- Create specialized exceptions:
  - `OllamaError` (with subtypes: ConnectionError, TimeoutError, InvalidModel)
  - `TelegramError` (with subtypes: RateLimitError, AuthError, InvalidToken)
  - `MCPError` (with subtypes: ToolNotFound, ExecutionFailed, ConnectionError)
  - `ConfigurationError`
  - `ValidationError`
  - `RateLimitExceededError`

**Success Criteria:**
- All exceptions inherit from WhimsyBotsError
- Each has meaningful __str__ representation
- Includes optional context dict for debugging

**Acceptance Tests:**
- [ ] File `app/exceptions.py` exists
- [ ] All exception classes inherit properly
- [ ] Can raise and catch specific exceptions
- [ ] Exception messages are informative

---

### Task 3: Fix Obvious N+1 Queries
**Effort:** 8 hours | **Impact:** MEDIUM (10% performance improvement)  
**Status:** Not Started

**Description:**
Identify and fix N+1 query problems in admin interface and views.

**Focus Areas:**
- `app/admin.py` — Bot admin displaying related message counts
- `services/context_assembler.py` — Message queries
- `app/views.py` — Webhook response rendering

**Deliverables:**
- Audit all database queries in above files
- Add `select_related()` where appropriate
- Add `prefetch_related()` for related managers
- Document optimization rationale

**Success Criteria:**
- No N+1 queries in admin
- Message display loads in single query
- Related objects prefetched

**Acceptance Tests:**
- [ ] Run Django Debug Toolbar on admin pages
- [ ] Query count reduced by >30%
- [ ] No warnings in logs about related field access

---

### Task 4: Add Type Hints to Public APIs
**Effort:** 8 hours | **Impact:** MEDIUM (catches bugs early)  
**Status:** Not Started

**Description:**
Add type hints to all public-facing functions and methods.

**Focus Areas:**
1. `services/bot_processor.py` — All public methods
2. `services/context_assembler.py` — Public methods
3. `services/embedding.py` — Public methods
4. `app/utils.py` — All utility functions
5. `managers/` — All manager methods

**Deliverables:**
- Add return type annotations to all public functions
- Add parameter type hints to functions missing them
- Add `from __future__ import annotations` to modules

**Success Criteria:**
- All public APIs have complete type hints
- Can run mypy on these modules without errors

**Acceptance Tests:**
- [ ] `python -m mypy services/bot_processor.py` passes
- [ ] `python -m mypy services/context_assembler.py` passes
- [ ] `python -m mypy app/utils.py` passes
- [ ] No missing-type-def warnings

---

### Task 5: Create Error Handling Decorator
**Effort:** 16-20 hours | **Impact:** HIGH (eliminates 60% error code duplication)  
**Status:** Not Started

**Description:**
Extract error handling patterns into a reusable decorator to eliminate code duplication.

**Deliverables:**
- Create `services/decorators.py` with:
  - `@task_error_handler` for Celery tasks
  - `@service_error_handler` for service methods
  - `@api_error_handler` for view methods
- Support configurable:
  - Max retries and retry delay
  - Specific exception types to catch
  - Fallback error handlers
  - Custom logging per task

**Implementation Requirements:**
```python
@task_error_handler(
    max_retries=3,
    retry_delay=60,
    exceptions_to_catch=[OllamaError, TelegramError],
    log_level='ERROR'
)
def process_cron_job(job_id: str):
    # No need for try/except; decorator handles it
    pass
```

**Success Criteria:**
- Decorator handles retry logic automatically
- Consistent error logging across all tasks
- Reduces error handling code by 60%+

**Acceptance Tests:**
- [ ] Decorator applies to Celery tasks correctly
- [ ] Max retries enforced
- [ ] Retry delay respected
- [ ] Exceptions logged with context
- [ ] Can be applied to 5+ existing tasks

---

### Task 6: Remove Exception Anti-patterns
**Effort:** 12-16 hours | **Impact:** MEDIUM (better debugging)  
**Status:** Not Started

**Description:**
Replace generic exception catching with specific exception handling.

**Target Files:**
- `app/tasks.py` — 4+ instances of generic catches
- `app/forms.py` — 3+ instances of `except Exception as _`
- `services/bot_processor.py` — Generic error handler
- `clients/telegram.py` — Consolidate exception types

**Changes Required:**
1. Replace `except Exception as e:` with specific exception types
2. Remove `except Exception as _:` (unused variable)
3. Add context to error logs (include relevant IDs, states)
4. Use custom exceptions created in Task 2

**Example:**
```python
# Before: Anti-pattern
try:
    models = client.list_models()
except Exception as _:
    logger.error("Failed", exc_info=True)
    return []

# After: Specific handling
try:
    models = client.list_models()
except OllamaConnectionError as e:
    logger.error("Failed to connect to Ollama at %s", self.endpoint, exc_info=True)
    return []  # Fallback
except OllamaTimeoutError as e:
    logger.warning("Ollama timeout, retrying", exc_info=True)
    raise  # Let caller decide
```

**Acceptance Tests:**
- [ ] All generic `Exception` catches replaced
- [ ] Each catch has specific exception type
- [ ] Error logs include context (IDs, URLs, etc.)
- [ ] Unused exception variables removed

---

## Phase 1: Foundation (Next Priority)

### Task 7: Configure Python Type Checking
**Effort:** 4-6 hours | **Impact:** HIGH (enables automated checks)  
**Status:** Not Started

**Description:**
Set up mypy configuration and pre-commit hooks for type checking.

**Deliverables:**
- Create `mypy.ini` with strict mode settings
- Create `.pre-commit-config.yaml` with:
  - mypy hook
  - black (formatting)
  - isort (import sorting)
  - flake8 (linting)
- Install pre-commit hooks in local dev environment
- Document setup in DEVELOPMENT.md

**Configuration Example:**
```ini
[mypy]
python_version = 3.11
warn_return_any = True
warn_unused_configs = True
disallow_untyped_defs = True
```

**Acceptance Tests:**
- [ ] mypy configuration file exists
- [ ] Pre-commit hooks configured
- [ ] Hooks run on `git commit`
- [ ] Documentation updated

---

### Task 8: Configuration Extraction & Validation
**Effort:** 12-16 hours | **Impact:** MEDIUM (easier environment management)  
**Status:** Not Started

**Description:**
Extract settings from Django settings.py into a centralized config module with validation.

**Deliverables:**
- Create `config/` directory with:
  - `config/base.py` — Base settings
  - `config/development.py` — Dev-specific
  - `config/production.py` — Prod-specific
  - `config/validators.py` — Config validation
- Validate all required environment variables at startup
- Create `config/README.md` documenting all settings
- Update `whimsybots/settings.py` to import from config

**Success Criteria:**
- All settings centralized in config module
- Validation fails on startup if required vars missing
- Easy to see all configurable options

**Acceptance Tests:**
- [ ] `config/` directory structure created
- [ ] All Django settings moved to config
- [ ] Validation runs on application startup
- [ ] Missing env vars cause clear error messages
- [ ] README documents all settings

---

### Task 9: Rate Limiting Integration
**Effort:** 10-15 hours | **Impact:** MEDIUM (reliability, cost control)  
**Status:** Not Started

**Description:**
Integrate existing RateLimiter across task handlers and external API calls.

**Deliverables:**
- Apply rate limiting to:
  - Telegram message handlers
  - Ollama API calls (per bot)
  - MCP tool execution
  - Cron job execution
- Create `services/rate_limiting_service.py`:
  - Unified rate limit checking
  - Different limits for different operations
  - Metrics collection
- Add rate limit configuration to settings

**Success Criteria:**
- Rate limiter blocks excessive requests
- Metrics collected for monitoring
- Configurable per bot/operation

**Acceptance Tests:**
- [ ] Rate limiter applied to 4+ handlers
- [ ] Exceeding limit raises RateLimitExceededError
- [ ] Configuration adjustable per environment
- [ ] Metrics logged for monitoring

---

### Task 10: Async Task Improvements
**Effort:** 15-20 hours | **Impact:** MEDIUM (reliability, scalability)  
**Status:** Not Started

**Description:**
Improve async/sync boundary handling in Celery tasks.

**Current Issues:**
- `asyncio.run()` creates new event loop per call (inefficient)
- No proper cleanup for long-running async operations
- Inconsistent async patterns across codebase

**Deliverables:**
- Create async wrapper service for MCP calls
- Update Celery configuration for async support
- Replace `asyncio.run()` calls with async-aware patterns
- Add proper signal handlers for graceful shutdown
- Document async patterns in DEVELOPMENT.md

**Success Criteria:**
- MCP tool loading uses shared event loop
- Long-running tasks don't block others
- Graceful shutdown cleans up resources

**Acceptance Tests:**
- [ ] No `asyncio.run()` in task methods
- [ ] Async operations properly awaited
- [ ] Resource cleanup verified
- [ ] No event loop warnings in logs

---

## Phase 2: Core Quality Improvements

### Task 11: Documentation Improvements
**Effort:** 20-25 hours | **Impact:** HIGH (onboarding, troubleshooting)  
**Status:** Not Started

**Description:**
Create comprehensive documentation guide.

**Deliverables:**
- Create `/docs/` directory with:
  - `DEVELOPMENT.md` — Local setup, testing, debugging
  - `DEPLOYMENT.md` — Production setup, scaling
  - `API.md` — REST endpoints, webhook formats
  - `ARCHITECTURE.md` — Deep dive into system design
  - `TESTING.md` — Testing strategy and patterns
- Update README.md with quick links
- Add API documentation for webhook handlers

**Success Criteria:**
- Developer can set up environment from DEVELOPMENT.md
- Operator can deploy from DEPLOYMENT.md
- All endpoints documented with examples

**Acceptance Tests:**
- [ ] `/docs/` directory created with 5 guides
- [ ] Each guide includes examples and common issues
- [ ] README links to documentation
- [ ] At least one external contributor uses docs successfully

---

### Task 12: Refactor Long Functions
**Effort:** 25-35 hours | **Impact:** MEDIUM (readability, testability)  
**Status:** Not Started

**Description:**
Break down functions >50 lines into smaller, single-purpose functions.

**Target Functions:**
1. `services/tool_calling_coordinator.py::run_tool_calling_loop()` — 120 lines
   - Extract: `_get_next_response()`, `_execute_tool()`, `_handle_error()`
   
2. `services/context_assembler.py::assemble()` — 95 lines
   - Extract: `_build_context_parts()`, `_optimize_token_usage()`
   
3. `app/tasks.py::process_cron_job()` — 110 lines
   - Extract: `_prepare_job()`, `_execute_job()`, `_finalize_job()`
   
4. `services/bot_processor.py::process_message()` — 78 lines
   - Extract: `_build_tools()`, `_run_llm()`, `_send_response()`

**Success Criteria:**
- All functions <50 lines
- Each function has single clear purpose
- Cyclomatic complexity <10 for all functions

**Acceptance Tests:**
- [ ] All target functions refactored
- [ ] Radon complexity score improved
- [ ] Tests pass (after Task 15 completed)
- [ ] Performance not degraded

---

### Task 13: Add Complete Type Hints (Comprehensive)
**Effort:** 30-40 hours | **Impact:** HIGH (catches bugs, IDE support)  
**Status:** Not Started

**Description:**
Complete type hint coverage across entire codebase (building on Task 4).

**Phase:**
1. **Core modules** (already done in Task 4): services, utils, managers
2. **Task layer**: app/tasks.py, app/views.py
3. **Model layer**: app/models.py, app/admin.py
4. **Client layer**: clients/*, managers/*
5. **Test module**: app/tests.py (Task 15)

**Deliverables:**
- Add return type annotations to 100% of public functions
- Add parameter type hints to all functions
- Add type hints to class attributes
- Run mypy with `--strict` flag

**Effort Breakdown:**
- Services/utils (Task 4): 8 hours ✓
- Tasks/views: 8 hours
- Models/admin: 8 hours
- Clients/managers: 8 hours
- Testing/misc: 8 hours

**Success Criteria:**
- `python -m mypy src --strict` passes with 0 errors
- No type: ignore comments except where necessary

**Acceptance Tests:**
- [ ] mypy strict mode passes
- [ ] <5 type: ignore comments
- [ ] IDE type checking works
- [ ] No missing-type-def warnings

---

### Task 14: Implement Dependency Injection Container
**Effort:** 40-50 hours | **Impact:** HIGH (enables testing, reduces coupling)  
**Status:** Not Started

**Description:**
Create a service container for managing dependencies and enabling easier testing.

**Deliverables:**
- Create `services/container.py`:
  ```python
  class ServiceContainer:
      @property
      def ollama_client(self) -> OllamaClient:
          # Lazy initialization
          if not hasattr(self, '_ollama_client'):
              config = OllamaConfigManager.get_ollama_config()
              self._ollama_client = OllamaClient(...)
          return self._ollama_client
      
      @property
      def bot_processor(self) -> BotMessageProcessor:
          return BotMessageProcessor(
              self.ollama_client,
              self.telegram_client
          )
  ```

- Update task handlers to use container
- Create factory functions for testing
- Document dependency graph

**Implementation Steps:**
1. Create container with properties for each service
2. Update `app/tasks.py` to use container
3. Update `services/` to accept container
4. Create `tests/factories.py` for test dependencies
5. Update existing code to use container

**Success Criteria:**
- All services accessed via container
- Easy to swap implementations in tests
- No singletons scattered across codebase

**Acceptance Tests:**
- [ ] Container initialized once per request
- [ ] Services properly injected
- [ ] Can create test container with mocks
- [ ] All 5+ services configurable via container

---

## Phase 3: Testing Foundation (Critical Blocker)

### Task 15: Implement Test Suite Foundation
**Effort:** 80-120 hours | **Impact:** CRITICAL (unblocks all improvements)  
**Status:** Not Started

**Description:**
Create comprehensive test suite from scratch covering services, tasks, and models.

**Phase 1: Test Infrastructure (20-25 hours)**
- [ ] Install pytest, pytest-django, pytest-cov, factory-boy
- [ ] Create `tests/` directory structure
- [ ] Create `conftest.py` with fixtures
- [ ] Create mock clients (OllamaClient, TelegramClient, MCP)
- [ ] Set up CI/CD to run tests

**Phase 2: Service Tests (25-35 hours)**
- [ ] Test `EmbeddingService` (5-8 tests)
- [ ] Test `ContextAssembler` (8-12 tests)
- [ ] Test `BotMessageProcessor` (10-15 tests)
- [ ] Test `TokenBudgetService` (5-8 tests)
- [ ] Test `RateLimiter` (3-5 tests)

**Phase 3: Task Tests (20-30 hours)**
- [ ] Test `telegram_msg_handler` (5-8 tests)
- [ ] Test `cron_job_poller` (5-8 tests)
- [ ] Test `process_cron_job` (8-12 tests)
- [ ] Test `setup_bot_webhook` (3-5 tests)
- [ ] Test `process_inbound_message` (8-12 tests)

**Phase 4: Model & Validation Tests (10-15 hours)**
- [ ] Test model validators (5-8 tests)
- [ ] Test model methods (3-5 tests)
- [ ] Test admin forms (3-5 tests)

**Phase 5: Integration Tests (10-15 hours)**
- [ ] End-to-end message processing flow
- [ ] Webhook handler → processing → response
- [ ] Cron job execution flow
- [ ] Error recovery scenarios

**Directory Structure:**
```
tests/
├── conftest.py                    # Fixtures & mocks
├── factories.py                   # Test data factories
├── mocks.py                       # Mock clients
├── fixtures/                      # Test data files
├── test_services.py               # Service tests
├── test_tasks.py                  # Task tests
├── test_models.py                 # Model validation
├── test_admin.py                  # Admin interface
└── integration/
    ├── test_message_flow.py
    ├── test_cron_flow.py
    └── test_error_handling.py
```

**Success Criteria:**
- >80% code coverage
- All critical paths tested
- CI/CD runs tests on every commit
- Tests run in <5 minutes

**Acceptance Tests:**
- [ ] `pytest src/` runs 100+ tests
- [ ] Coverage report shows >80% coverage
- [ ] All services have >5 tests each
- [ ] Integration tests pass
- [ ] Tests run in CI/CD pipeline

---

## Summary Table (Sorted by Effort)

| Task # | Name | Effort (hrs) | Impact | Phase |
|--------|------|--------------|--------|-------|
| 5 | Create TROUBLESHOOTING.md | 4 | HIGH | Quick Win |
| 3 | Fix Obvious N+1 Queries | 8 | MEDIUM | Quick Win |
| 4 | Add Type Hints to Public APIs | 8 | MEDIUM | Quick Win |
| 2 | Add Custom Exception Hierarchy | 8-12 | MEDIUM | Quick Win |
| 1 | Create Error Handling Decorator | 16-20 | HIGH | Quick Win |
| 6 | Remove Exception Anti-patterns | 12-16 | MEDIUM | Quick Win |
| 7 | Configure Python Type Checking | 4-6 | HIGH | Phase 1 |
| 9 | Rate Limiting Integration | 10-15 | MEDIUM | Phase 1 |
| 8 | Configuration Extraction | 12-16 | MEDIUM | Phase 1 |
| 10 | Async Task Improvements | 15-20 | MEDIUM | Phase 1 |
| 11 | Documentation Improvements | 20-25 | HIGH | Phase 2 |
| 12 | Refactor Long Functions | 25-35 | MEDIUM | Phase 2 |
| 13 | Add Complete Type Hints | 30-40 | HIGH | Phase 2 |
| 14 | Implement DI Container | 40-50 | HIGH | Phase 2 |
| 15 | Test Suite Foundation | 80-120 | CRITICAL | Phase 3 |

**Total Effort:** 287-369 hours

---

## Execution Strategy

### Week 1: Quick Wins (44 hours)
- Tasks 5, 3, 4, 2, 1, 6, 7
- Focus: Foundation for quality improvements

### Week 2-3: Phase 1 Foundation (57 hours)
- Tasks 8, 9, 10
- Focus: Configuration, reliability, async

### Week 4-5: Phase 2 Quality (120-150 hours)
- Tasks 11, 12, 13, 14
- Focus: Documentation, code clarity, testing enablement

### Week 6-10: Phase 3 Testing (80-120 hours)
- Task 15
- Focus: Comprehensive test coverage

---

## Definition of Done (Per Task)

Each task is considered complete when:
1. ✅ All deliverables are created/updated
2. ✅ Acceptance tests pass
3. ✅ Code review completed by peer
4. ✅ Tests pass (if applicable)
5. ✅ Documentation updated if needed
6. ✅ Changes committed to git with descriptive message

---

## Notes

- **Start with Quick Wins** to build momentum and prove feasibility
- **Test Suite (Task 15)** should start in parallel early but doesn't block other work
- **Prioritize by Impact**, not effort — high-impact items first
- **Refactor in small PRs** to make review easier
- **Document as you go** — don't defer documentation to end

---

**Last Updated:** June 8, 2026
