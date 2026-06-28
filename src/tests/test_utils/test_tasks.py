"""Tests for 'src/utils/tasks.py'.

The helpers in 'utils.tasks' cover three concerns:
  1. Object lookups (get_ollama_cfg, get_bot_obj, get_cron_obj, get_msg_obj).
  2. Embedding generation dispatch (generate_message_embedding,
     generate_cron_job_embedding, generate_mcp_embedding).
  3. Error-handling helpers (build_error_description, log_task_failure).

Most tests use 'unittest.mock.patch' because the helpers cross-cut the
ORM and the EmbeddingService — we want to assert the contract, not
exercise the entire stack.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from app.choices import MessageRole
from clients.telegram import TelegramRateLimitError
from strings import (
    GENERAL_TASK_ERROR,
    TELEGRAM_RATE_LIMIT_ERROR,
)
from utils.tasks import (
    DEFAULT_QUEUE,
    MAX_RETRIES,
    RETRY_BASE_SECONDS,
    TELEGRAM_ALLOWED_UPDATES,
    _get_by_id_or_log,
    build_error_description,
    create_log,
    generate_cron_job_embedding,
    generate_mcp_embedding,
    generate_message_embedding,
    get_bot_obj,
    get_cron_obj,
    get_msg_obj,
    get_ollama_cfg,
    log_task_failure,
)

# ──────────────────────────────────────────────
# Module-level constants
# ──────────────────────────────────────────────


def test_module_constants_have_expected_values():
    # Pin the retry timing + queue topology — these are part of the
    # operational contract and changing them requires a deployment note.
    assert RETRY_BASE_SECONDS == 60
    assert MAX_RETRIES == 3
    assert DEFAULT_QUEUE == "default"
    assert TELEGRAM_ALLOWED_UPDATES == ["message"]


# ──────────────────────────────────────────────
# _get_by_id_or_log (private, but central)
# ──────────────────────────────────────────────


def test_get_by_id_or_log_returns_instance_when_found():
    bot = MagicMock()
    bot.__class__.DoesNotExist = type("DoesNotExist", (Exception,), {})
    with patch("utils.tasks.Bot") as Model:
        Model.objects.get.return_value = bot
        Model.DoesNotExist = bot.__class__.DoesNotExist
        result = _get_by_id_or_log(Model, "abc-123", obj_label="bot")
    assert result is bot


def test_get_by_id_or_log_returns_none_and_logs_when_missing(caplog):
    Model = MagicMock()
    Model.DoesNotExist = type("DoesNotExist", (Exception,), {})

    def raise_does_not_exist(*a, **kw):
        raise Model.DoesNotExist("missing")

    Model.objects.get.side_effect = raise_does_not_exist

    with caplog.at_level(logging.ERROR):
        result = _get_by_id_or_log(Model, "abc-123", obj_label="bot")

    assert result is None
    assert any("bot" in r.message for r in caplog.records)


# ──────────────────────────────────────────────
# get_bot_obj / get_cron_obj / get_msg_obj
# ──────────────────────────────────────────────


def test_get_bot_obj_returns_none_when_bot_missing():
    with patch("utils.tasks.Bot.objects.get") as get:
        get.side_effect = Exception("missing")
        # Ensure DoesNotExist is properly raised
        from app.models import Bot

        get.side_effect = Bot.DoesNotExist("missing")
        assert get_bot_obj("missing-id") is None


def test_get_bot_obj_returns_bot_when_present():
    bot = MagicMock()
    with patch("utils.tasks.Bot.objects.get", return_value=bot):
        assert get_bot_obj("some-id") is bot


def test_get_cron_obj_returns_none_when_missing():
    from app.models import CronJob

    with patch("utils.tasks.CronJob.objects.get") as get:
        get.side_effect = CronJob.DoesNotExist("missing")
        assert get_cron_obj("missing-id") is None


def test_get_cron_obj_returns_cron_job_when_present():
    cron = MagicMock()
    with patch("utils.tasks.CronJob.objects.get", return_value=cron):
        assert get_cron_obj("some-id") is cron


def test_get_msg_obj_returns_none_when_missing():
    from app.models import Message

    with patch("utils.tasks.Message.objects.get") as get:
        get.side_effect = Message.DoesNotExist("missing")
        assert get_msg_obj("missing-id") is None


def test_get_msg_obj_returns_message_when_present():
    msg = MagicMock()
    with patch("utils.tasks.Message.objects.get", return_value=msg):
        assert get_msg_obj("some-id") is msg


# ──────────────────────────────────────────────
# get_ollama_cfg
# ──────────────────────────────────────────────


def test_get_ollama_cfg_returns_config_when_present():
    ollama_cfg = MagicMock()
    with patch("utils.tasks.OllamaConfigManager.get_ollama_config", return_value=ollama_cfg):
        assert get_ollama_cfg() is ollama_cfg


def test_get_ollama_cfg_returns_none_and_logs_when_missing(caplog):
    with patch("utils.tasks.OllamaConfigManager.get_ollama_config", return_value=None):
        with caplog.at_level(logging.WARNING):
            result = get_ollama_cfg()
    assert result is None
    assert any("ollama" in r.message.lower() for r in caplog.records)


# ──────────────────────────────────────────────
# create_log
# ──────────────────────────────────────────────


def test_create_log_persists_log_row_with_correct_fields():
    bot = MagicMock()
    with patch("utils.tasks.Log.objects.create") as create:
        create_log(bot=bot, is_success=True, desc="all good")
    create.assert_called_once_with(bot=bot, is_success=True, description="all good")


def test_create_log_persists_failure_entry():
    bot = MagicMock()
    with patch("utils.tasks.Log.objects.create") as create:
        create_log(bot=bot, is_success=False, desc="boom")
    create.assert_called_once_with(bot=bot, is_success=False, description="boom")


# ──────────────────────────────────────────────
# _should_skip_message_embedding (via generate_message_embedding)
# ──────────────────────────────────────────────


def test_generate_message_embedding_returns_none_when_message_missing():
    with patch("utils.tasks.get_msg_obj", return_value=None):
        assert generate_message_embedding(MagicMock(), "missing-id") is None


def test_generate_message_embedding_skips_non_user_role():
    ollama_cfg = MagicMock()
    msg = MagicMock()
    msg.role = MessageRole.SYSTEM.value[0]  # 'S', not 'U'
    msg.id = "m-1"
    with patch("utils.tasks.get_msg_obj", return_value=msg):
        assert generate_message_embedding(ollama_cfg, "m-1") is None


def test_generate_message_embedding_skips_message_with_existing_embedding():
    ollama_cfg = MagicMock()
    msg = MagicMock()
    msg.role = MessageRole.USER.value[0]  # 'U'
    msg.content_embedding = [0.1, 0.2]  # already embedded
    msg.id = "m-1"
    with patch("utils.tasks.get_msg_obj", return_value=msg):
        assert generate_message_embedding(ollama_cfg, "m-1") is None


def test_generate_message_embedding_persists_and_dispatches_task():
    ollama_cfg = MagicMock()
    msg = MagicMock()
    msg.role = MessageRole.USER.value[0]
    msg.content_embedding = None
    msg.id = "m-1"
    msg.bot_id = "bot-1"
    bot = MagicMock()
    msg.bot = bot

    with (
        patch("utils.tasks.get_msg_obj", return_value=msg),
        patch("utils.tasks.EmbeddingService") as svc_cls,
        patch("utils.tasks.celery_app.send_task") as send_task,
    ):
        result = generate_message_embedding(ollama_cfg, "m-1")

    # EmbeddingService was instantiated with the right inputs and
    # save_message_embedding was called on it.
    svc_cls.assert_called_once_with(bot=bot, ollama=ollama_cfg)
    svc_cls.return_value.save_message_embedding.assert_called_once_with(message=msg)

    # The follow-up process_inbound_message task was dispatched by name
    # (avoids a circular import between utils.tasks and app.tasks).
    send_task.assert_called_once()
    args, kwargs = send_task.call_args
    assert args[0] == "app.tasks.process_inbound_message"
    assert kwargs["queue"] == DEFAULT_QUEUE
    assert kwargs["kwargs"] == {"bot_id": "bot-1", "msg_id": "m-1"}

    # The owning bot is returned on success.
    assert result is bot


def test_generate_message_embedding_returns_bot_on_success():
    # Defensive check: ensures the caller gets the bot even if it
    # doesn't reach the dispatch path.
    ollama_cfg = MagicMock()
    msg = MagicMock()
    msg.role = MessageRole.USER.value[0]
    msg.content_embedding = None
    msg.id = "m-1"
    msg.bot_id = "bot-1"
    bot = MagicMock()
    msg.bot = bot

    with (
        patch("utils.tasks.get_msg_obj", return_value=msg),
        patch("utils.tasks.EmbeddingService"),
        patch("utils.tasks.celery_app.send_task"),
    ):
        result = generate_message_embedding(ollama_cfg, "m-1")
    assert result is bot


# ──────────────────────────────────────────────
# generate_cron_job_embedding
# ──────────────────────────────────────────────


def test_generate_cron_job_embedding_returns_none_when_missing():
    with patch("utils.tasks.get_cron_obj", return_value=None):
        assert generate_cron_job_embedding(MagicMock(), "missing") is None


def test_generate_cron_job_embedding_persists_embedding():
    ollama_cfg = MagicMock()
    cron_job = MagicMock()
    cron_job.bot = MagicMock()
    cron_job.id = "c-1"

    with (
        patch("utils.tasks.get_cron_obj", return_value=cron_job),
        patch("utils.tasks.EmbeddingService") as svc_cls,
    ):
        result = generate_cron_job_embedding(ollama_cfg, "c-1")

    svc_cls.assert_called_once_with(bot=cron_job.bot, ollama=ollama_cfg)
    svc_cls.return_value.save_cron_job_embedding.assert_called_once_with(cron_job=cron_job)
    assert result is cron_job.bot


# ──────────────────────────────────────────────
# generate_mcp_embedding
# ──────────────────────────────────────────────


def test_generate_mcp_embedding_returns_none_when_missing_silently():
    # MCP servers can be deleted between dispatch and execution —
    # we must silently skip rather than logging an error.
    from app.models import MCPServer

    with patch("utils.tasks.MCPServer.objects.get") as get:
        get.side_effect = MCPServer.DoesNotExist("missing")
        assert generate_mcp_embedding(MagicMock(), "missing-id") is None


def test_generate_mcp_embedding_persists_embedding():
    ollama_cfg = MagicMock()
    mcp = MagicMock()
    mcp.bot = MagicMock()
    mcp.id = "mcp-1"

    with (
        patch("utils.tasks.MCPServer.objects.get", return_value=mcp),
        patch("utils.tasks.EmbeddingService") as svc_cls,
    ):
        result = generate_mcp_embedding(ollama_cfg, "mcp-1")

    svc_cls.assert_called_once_with(bot=mcp.bot, ollama=ollama_cfg)
    svc_cls.return_value.save_mcp_embedding.assert_called_once_with(mcp_server=mcp)
    assert result is mcp.bot


# ──────────────────────────────────────────────
# build_error_description
# ──────────────────────────────────────────────


def test_build_error_description_uses_rate_limit_template_for_telegram_errors():
    bot = MagicMock()
    bot.name = "mybot"
    err = TelegramRateLimitError(retry_after=42)

    description, retry_after = build_error_description(
        func_name="telegram_msg_handler", bot=bot, error=err
    )

    assert retry_after == 42
    assert "telegram_msg_handler" in description
    assert "mybot" in description


def test_build_error_description_uses_generic_template_for_other_errors():
    bot = MagicMock()
    bot.name = "mybot"

    description, retry_minutes = build_error_description(
        func_name="process_cron_job", bot=bot, error=RuntimeError("kaboom")
    )

    assert retry_minutes == 1
    assert "process_cron_job" in description
    assert "mybot" in description
    assert "kaboom" in description


def test_build_error_description_handles_none_bot():
    # Some tasks fail before a bot is resolved — verify graceful handling.
    err = RuntimeError("pre-bot failure")

    description, retry_minutes = build_error_description(
        func_name="early_task", bot=None, error=err
    )

    assert retry_minutes == 1
    assert "early_task" in description
    assert "unknown" in description.lower()


def test_build_error_description_telegram_error_template_matches_strings():
    # Ensure the human-readable text is constructed from the actual
    # strings.py template (guards against silent template drift).
    from types import SimpleNamespace

    bot = SimpleNamespace(name="mybot")
    err = TelegramRateLimitError(retry_after=5)

    description, _ = build_error_description(func_name="some_task", bot=bot, error=err)

    expected = TELEGRAM_RATE_LIMIT_ERROR.format(
        func_name="some_task", bot_name="mybot", error=str(err)
    )
    assert description == expected


def test_build_error_description_generic_template_matches_strings():
    from types import SimpleNamespace

    bot = SimpleNamespace(name="mybot")
    err = ValueError("bad input")

    description, _ = build_error_description(func_name="some_task", bot=bot, error=err)

    expected = GENERAL_TASK_ERROR.format(func_name="some_task", bot_name="mybot", error=str(err))
    assert description == expected


# ──────────────────────────────────────────────
# log_task_failure
# ──────────────────────────────────────────────


def test_log_task_failure_persists_log_when_bot_present(caplog):
    bot = MagicMock()
    with (
        patch("utils.tasks.create_log") as create,
        caplog.at_level(logging.ERROR, logger="utils.tasks"),
    ):
        log_task_failure(bot=bot, description="boom", log_message="crashed")

    create.assert_called_once_with(bot=bot, is_success=False, desc="boom")
    assert any("crashed" in r.message for r in caplog.records)


def test_log_task_failure_skips_log_creation_when_bot_none(caplog):
    # When bot resolution itself fails, we cannot persist a Log row
    # (no FK target) — but the logger call still happens.
    with (
        patch("utils.tasks.create_log") as create,
        caplog.at_level(logging.ERROR, logger="utils.tasks"),
    ):
        log_task_failure(bot=None, description="pre-bot failure", log_message="crashed")

    create.assert_not_called()
    assert any("crashed" in r.message for r in caplog.records)


def test_log_task_failure_attaches_traceback(caplog):
    bot = MagicMock()
    with (
        patch("utils.tasks.create_log"),
        caplog.at_level(logging.ERROR, logger="utils.tasks"),
    ):
        try:
            raise RuntimeError("simulated failure")
        except RuntimeError:
            log_task_failure(bot=bot, description="boom", log_message="crashed")

    # The log record should carry exc_info so the traceback is rendered.
    assert any(r.exc_info is not None for r in caplog.records)
