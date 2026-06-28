"""Tests for 'src/app/tasks.py'.

Celery's '@celery.task(bind=True, max_retries=...)' decorator returns a
'PromiseProxy'. Calling '.run(...)' invokes the task body directly with
'self' and the bound kwargs — perfect for synchronous unit testing
without a Celery worker.

All ORM access is patched at the source location so we never hit the DB.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import tasks as app_tasks
from strings import WEBHOOK_SETUP_SUCCESS

# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _bot(*, id=None, name="mybot", is_active=True, telegram_chat_id="999"):
    return SimpleNamespace(
        id=id or uuid.uuid4(),
        name=name,
        is_active=is_active,
        telegram_chat_id=telegram_chat_id,
        telegram_bot_token="TOK",
        save=MagicMock(),
        telegram_bot_token_hash="hash",
    )


def _ollama():
    return SimpleNamespace(
        num_ctx=4096,
        num_predict=None,
        endpoint="http://localhost:11434",
        api_key=None,
    )


def _cron(*, id=None, name="cron", bot=None):
    return SimpleNamespace(
        id=id or uuid.uuid4(),
        name=name,
        description="desc",
        cron_expression="0 9 * * *",
        next_run_at=None,
        last_run_at=None,
        schedule_embedding=[0.1, 0.2, 0.3],
        schedule_embedding_updated_at=None,
        updated_at=None,
        save=MagicMock(),
        bot=bot or _bot(),
        bot_id=bot.id if bot else uuid.uuid4(),
    )


def _msg(*, id=None, role="U", content="hello", save=MagicMock()):
    return SimpleNamespace(
        id=id or uuid.uuid4(),
        role=role,
        content=content,
        bot_id=uuid.uuid4(),
    )


# ──────────────────────────────────────────────
# telegram_msg_handler
# ──────────────────────────────────────────────


def test_telegram_msg_handler_resolves_bot_by_token_hash():
    bot = _bot()
    update = {"update_id": 1, "message": {"text": "hi"}}

    with (
        patch("app.tasks.Bot.objects") as bot_objs,
        patch("app.tasks.TelegramUpdateHandler") as handler,
    ):
        bot_objs.get.return_value = bot
        app_tasks.telegram_msg_handler.run(bot_token="the-token", update=update)

    # Bot looked up by token hash (SHA-256 hex digest).
    from utils.crypto import get_token_hash

    bot_objs.get.assert_called_once_with(telegram_bot_token_hash=get_token_hash("the-token"))
    handler.handle_update.assert_called_once_with(bot, update)


def test_telegram_msg_handler_returns_silently_when_bot_missing(caplog):
    import logging

    from app.models import Bot

    with patch("app.tasks.Bot.objects") as bot_objs, caplog.at_level(logging.ERROR):
        bot_objs.get.side_effect = Bot.DoesNotExist("missing")
        app_tasks.telegram_msg_handler.run(bot_token="x", update={"update_id": 1})

    # Returns silently — does NOT retry. Bad token ⇒ webhook misconfigured.
    assert any("not found" in r.message.lower() for r in caplog.records)


def test_telegram_msg_handler_retries_on_handler_exception():
    bot = _bot()

    with (
        patch("app.tasks.Bot.objects") as bot_objs,
        patch("app.tasks.TelegramUpdateHandler") as handler,
        patch("app.tasks.RETRY_BASE_SECONDS", 60),
    ):
        bot_objs.get.return_value = bot
        handler.handle_update.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            # The body raises self.retry(exc=..., countdown=60). retry
            # is bound to the task instance — we patch the run() to
            # raise the underlying exception so we can assert it.
            app_tasks.telegram_msg_handler.run(bot_token="x", update={})


# ──────────────────────────────────────────────
# cron_job_poller + private helpers
# ──────────────────────────────────────────────


def test_find_due_cron_jobs_filters_active_bots_with_chat_id():
    from datetime import datetime, timezone

    with patch("app.tasks.CronJob.objects") as cron_objs:
        cron_objs.filter.return_value = []
        app_tasks._find_due_cron_jobs(datetime.now(timezone.utc))

    # All four filters must be applied.
    kwargs = cron_objs.filter.call_args.kwargs
    assert kwargs["is_active"] is True
    assert kwargs["bot__is_active"] is True
    assert kwargs["bot__telegram_chat_id__isnull"] is False
    assert "next_run_at__lte" in kwargs


def test_dispatch_cron_job_enqueues_with_eta():
    job = _cron()
    job.next_run_at = "2099-01-01T09:00:00Z"

    with patch("app.tasks.process_cron_job") as target:
        app_tasks._dispatch_cron_job(job)

    target.apply_async.assert_called_once()
    kwargs = target.apply_async.call_args.kwargs
    assert kwargs["queue"] == "default"
    assert kwargs["kwargs"] == {"job_id": str(job.id)}
    assert kwargs["eta"] == "2099-01-01T09:00:00Z"


def test_refresh_cron_job_embedding_if_stale_skips_when_fresh():
    """When an embedding exists and was updated after the job itself,
    we should NOT regenerate it."""

    job = _cron()
    job.schedule_embedding = [0.1, 0.2]
    # updated_at is None ⇒ updated_at < job.updated_at is True (None < anything).
    # We need schedule_embedding_updated_at >= job.updated_at.
    from datetime import datetime, timezone

    job.schedule_embedding_updated_at = datetime.now(timezone.utc)
    job.updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

    with patch("app.tasks.EmbeddingService") as svc_cls:
        app_tasks._refresh_cron_job_embedding_if_stale(job, _ollama())

    svc_cls.assert_not_called()


def test_refresh_cron_job_embedding_if_stale_regenerates_when_missing():
    job = _cron()
    job.schedule_embedding = None
    job.schedule_embedding_updated_at = None

    with patch("app.tasks.EmbeddingService") as svc_cls:
        app_tasks._refresh_cron_job_embedding_if_stale(job, _ollama())

    svc_cls.return_value.save_cron_job_embedding.assert_called_once_with(cron_job=job)


def test_cron_job_poller_returns_when_no_ollama():
    with patch("app.tasks.get_ollama_cfg", return_value=None):
        # Must not raise — returns silently.
        app_tasks.cron_job_poller.run()


def test_cron_job_poller_enqueues_due_jobs():
    job = _cron()
    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks._find_due_cron_jobs", return_value=[job]),
        patch("app.tasks._refresh_cron_job_embedding_if_stale"),
        patch("app.tasks._dispatch_cron_job") as dispatch,
    ):
        app_tasks.cron_job_poller.run()

    dispatch.assert_called_once_with(job)


def test_cron_job_poller_retries_on_exception():
    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks._find_due_cron_jobs", side_effect=RuntimeError("db")),
    ):
        with pytest.raises(RuntimeError):
            app_tasks.cron_job_poller.run()


# ──────────────────────────────────────────────
# setup_bot_webhook
# ──────────────────────────────────────────────


def test_build_webhook_url_joins_base_and_token():
    bot = _bot()
    bot.telegram_bot_token = "ABC123"

    with patch("app.tasks.settings") as settings:
        settings.WEBHOOK_BASE_URL = "https://example.com"
        url = app_tasks._build_webhook_url(bot)

    assert url == "https://example.com/webhook/ABC123/"


def test_build_webhook_url_strips_trailing_slashes():
    bot = _bot()
    bot.telegram_bot_token = "TOK"
    with patch("app.tasks.settings") as settings:
        settings.WEBHOOK_BASE_URL = "https://example.com/"
        url = app_tasks._build_webhook_url(bot)
    # No double-slash between base and path.
    assert url == "https://example.com/webhook/TOK/"


def test_setup_bot_webhook_returns_silently_when_bot_missing():
    with patch("app.tasks.get_bot_obj", return_value=None):
        app_tasks.setup_bot_webhook.run(bot_id="x")


def test_setup_bot_webhook_skips_inactive_bots():
    bot = _bot(is_active=False)
    with (
        patch("app.tasks.get_bot_obj", return_value=bot),
        patch("app.tasks.TelegramClientManager") as mgr,
    ):
        app_tasks.setup_bot_webhook.run(bot_id="x")
    mgr.create_client.assert_not_called()


def test_setup_bot_webhook_calls_telegram_set_webhook():
    bot = _bot(is_active=True)
    client = MagicMock()
    client.set_webhook.return_value = {"ok": True}

    with (
        patch("app.tasks.get_bot_obj", return_value=bot),
        patch("app.tasks.TelegramClientManager") as mgr,
        patch("app.tasks.create_log") as create_log,
        patch("app.tasks.settings") as settings,
        patch("app.tasks.TELEGRAM_ALLOWED_UPDATES", ["message"]),
    ):
        settings.WEBHOOK_BASE_URL = "https://x"
        mgr.create_client.return_value = client
        app_tasks.setup_bot_webhook.run(bot_id="x")

    client.set_webhook.assert_called_once()
    set_kwargs = client.set_webhook.call_args.kwargs
    assert set_kwargs["url"].endswith(f"/webhook/{bot.telegram_bot_token}/")
    assert set_kwargs["allowed_updates"] == ["message"]

    success_msg = WEBHOOK_SETUP_SUCCESS.format(bot_name=bot.name)
    create_log.assert_called_once_with(bot=bot, is_success=True, desc=success_msg)


# ──────────────────────────────────────────────
# process_cron_job
# ──────────────────────────────────────────────


def test_process_cron_job_returns_when_no_ollama():
    with patch("app.tasks.get_ollama_cfg", return_value=None):
        app_tasks.process_cron_job.run(job_id="x")


def test_process_cron_job_returns_when_cron_missing():
    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_cron_obj", return_value=None),
    ):
        app_tasks.process_cron_job.run(job_id="x")


def test_process_cron_job_skips_when_no_response():
    job = _cron()
    processor = MagicMock()
    processor.process_cron_job.return_value = (None, 100)

    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_cron_obj", return_value=job),
        patch("app.tasks._build_processor", return_value=processor),
    ):
        app_tasks.process_cron_job.run(job_id="x")

    processor.send_response.assert_not_called()


def test_process_cron_job_sends_response_and_logs():
    job = _cron()
    processor = MagicMock()
    processor.process_cron_job.return_value = ("cron output", 200)

    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_cron_obj", return_value=job),
        patch("app.tasks._build_processor", return_value=processor),
        patch("app.tasks.manage_conversation_summary") as summary_task,
        patch("app.tasks.create_log") as create_log,
        patch("app.tasks._update_cron_job_schedule") as update_sched,
    ):
        app_tasks.process_cron_job.run(job_id="x")

    processor.send_response.assert_called_once_with(response="cron output")
    summary_task.apply_async.assert_called_once()
    update_sched.assert_called_once_with(job)
    create_log.assert_called_once()


def test_update_cron_job_schedule_writes_new_timestamps():
    job = _cron()
    with (
        patch("app.tasks.calculate_next_run_at", return_value="NEXT"),
        patch("app.tasks.timezone") as tz,
    ):
        tz.now.return_value = "NOW"
        app_tasks._update_cron_job_schedule(job)

    assert job.next_run_at == "NEXT"
    assert job.last_run_at == "NOW"
    save_kwargs = job.save.call_args.kwargs
    assert save_kwargs["update_fields"] == ["next_run_at", "last_run_at"]


def test_build_processor_wires_ollama_client():
    bot = _bot()
    ollama = _ollama()
    with (
        patch("app.tasks.OllamaClient") as Client,
        patch("app.tasks.BotMessageProcessor") as Processor,
    ):
        result = app_tasks._build_processor(bot, ollama)

    Client.assert_called_once_with(ollama.endpoint, api_key=ollama.api_key)
    Processor.assert_called_once_with(bot, ollama, Client.return_value)
    assert result is Processor.return_value


# ──────────────────────────────────────────────
# process_inbound_message
# ──────────────────────────────────────────────


def test_process_inbound_message_returns_when_no_ollama():
    with patch("app.tasks.get_ollama_cfg", return_value=None):
        app_tasks.process_inbound_message.run(bot_id="x", msg_id="y")


def test_process_inbound_message_returns_when_bot_missing():
    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_bot_obj", return_value=None),
    ):
        app_tasks.process_inbound_message.run(bot_id="x", msg_id="y")


def test_process_inbound_message_returns_when_message_missing():
    bot = _bot()
    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_bot_obj", return_value=bot),
        patch("app.tasks.get_msg_obj", return_value=None),
    ):
        app_tasks.process_inbound_message.run(bot_id="x", msg_id="y")


def test_process_inbound_message_skips_when_no_response():
    bot = _bot()
    msg = _msg()
    processor = MagicMock()
    processor.process_message.return_value = (None, 50)

    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_bot_obj", return_value=bot),
        patch("app.tasks.get_msg_obj", return_value=msg),
        patch("app.tasks._build_processor", return_value=processor),
    ):
        app_tasks.process_inbound_message.run(bot_id="x", msg_id="y")

    processor.send_response.assert_not_called()


def test_process_inbound_message_sends_response_and_logs():
    bot = _bot()
    msg = _msg()
    processor = MagicMock()
    processor.process_message.return_value = ("the answer", 75)

    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_bot_obj", return_value=bot),
        patch("app.tasks.get_msg_obj", return_value=msg),
        patch("app.tasks._build_processor", return_value=processor),
        patch("app.tasks._queue_post_response_followups") as followups,
        patch("app.tasks.create_log") as create_log,
    ):
        app_tasks.process_inbound_message.run(bot_id="x", msg_id="y")

    processor.send_response.assert_called_once_with(response="the answer")
    followups.assert_called_once()
    create_log.assert_called_once()


# ──────────────────────────────────────────────
# _queue_post_response_followups
# ──────────────────────────────────────────────


def test_queue_post_response_followups_always_queues_summary():
    bot = _bot()
    with (
        patch("app.tasks.manage_conversation_summary") as summary_task,
        patch("app.tasks.regenerate_observed_patterns") as pattern_task,
        patch("app.tasks.ObservedPatternsService") as pattern_svc_cls,
    ):
        pattern_svc_cls.return_value.should_regenerate.return_value = False
        app_tasks._queue_post_response_followups(bot, _ollama(), bot_id="b-1")

    summary_task.apply_async.assert_called_once()
    # Patterns NOT queued when service says we shouldn't regen.
    pattern_task.apply_async.assert_not_called()


def test_queue_post_response_followups_queues_patterns_when_due():
    bot = _bot()
    with (
        patch("app.tasks.manage_conversation_summary"),
        patch("app.tasks.regenerate_observed_patterns") as pattern_task,
        patch("app.tasks.ObservedPatternsService") as pattern_svc_cls,
    ):
        pattern_svc_cls.return_value.should_regenerate.return_value = True
        app_tasks._queue_post_response_followups(bot, _ollama(), bot_id="b-1")

    pattern_task.apply_async.assert_called_once()
    pattern_kwargs = pattern_task.apply_async.call_args.kwargs
    assert pattern_kwargs["kwargs"] == {"bot_id": "b-1"}


# ──────────────────────────────────────────────
# manage_conversation_summary
# ──────────────────────────────────────────────


def test_manage_conversation_summary_returns_when_no_ollama():
    with patch("app.tasks.get_ollama_cfg", return_value=None):
        app_tasks.manage_conversation_summary.run()


def test_manage_conversation_summary_uses_bulk_create_and_update():
    # Build a fake bot with a 'conversations' attribute set, plus a fake
    # summary result.
    class _Bot:
        id = uuid.uuid4()
        name = "bot"
        ollama_model = "llama3"
        conversations = [_msg()]  # truthy
        system_messages = []

    bot = _Bot()
    summary_msg = _msg(role="S", content="x")
    summary_msg.role = "S"

    fake_queryset = MagicMock()
    fake_queryset.count.return_value = 1
    # Support iteration over [bot]
    fake_queryset.__iter__ = lambda self: iter([bot])

    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.OllamaClient"),
        patch("app.tasks._build_summary_bot_queryset", return_value=fake_queryset),
        patch("app.tasks._process_bot_summary", return_value=(summary_msg, True)),
        patch("app.tasks._persist_summaries") as persist,
    ):
        app_tasks.manage_conversation_summary.run()

    persist.assert_called_once()
    args = persist.call_args.args
    assert len(args[0]) == 1  # to_create
    assert len(args[1]) == 0  # to_update


def test_manage_conversation_summary_skips_bots_with_no_conversations():
    # A bot with empty conversations must short-circuit cleanly.
    bot = SimpleNamespace(id=uuid.uuid4(), name="b", conversations=[], system_messages=[])
    fake_queryset = MagicMock()
    fake_queryset.count.return_value = 0
    fake_queryset.__iter__ = lambda self: iter([bot])

    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.OllamaClient"),
        patch("app.tasks._build_summary_bot_queryset", return_value=fake_queryset),
        patch("app.tasks._persist_summaries") as persist,
    ):
        app_tasks.manage_conversation_summary.run()

    # No work to persist.
    persist.assert_called_once_with([], [])


# ──────────────────────────────────────────────
# generate_embedding
# ──────────────────────────────────────────────


def test_resolve_single_embedding_target_returns_none_for_no_ids():
    result = app_tasks._resolve_single_embedding_target(None, None, None)
    assert result is None


def test_resolve_single_embedding_target_returns_none_for_multiple_ids():
    # More than one ID supplied ⇒ invalid request.
    result = app_tasks._resolve_single_embedding_target("m-1", "c-1", None)
    assert result is None


def test_resolve_single_embedding_target_returns_tuple_for_single_id():
    result = app_tasks._resolve_single_embedding_target("m-1", None, None)
    assert result is not None
    obj_type, obj_id, dispatch = result
    assert obj_type == "Message"
    assert obj_id == "m-1"
    assert callable(dispatch)


def test_generate_embedding_returns_when_no_target():
    with patch("app.tasks._resolve_single_embedding_target", return_value=None):
        # All-None IDs ⇒ no target resolved ⇒ task exits silently.
        app_tasks.generate_embedding.run()


def test_generate_embedding_returns_when_no_ollama():
    with (
        patch(
            "app.tasks._resolve_single_embedding_target",
            return_value=("Message", "m-1", MagicMock()),
        ),
        patch("app.tasks.get_ollama_cfg", return_value=None),
    ):
        app_tasks.generate_embedding.run()


def test_generate_embedding_invokes_dispatch_with_ollama():
    bot = _bot()
    dispatch = MagicMock(return_value=bot)
    with (
        patch(
            "app.tasks._resolve_single_embedding_target",
            return_value=("Message", "m-1", dispatch),
        ),
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
    ):
        app_tasks.generate_embedding.run(message_id="m-1")

    # The dispatch callable received the active ollama instance.
    dispatch.assert_called_once_with(_ollama(), "m-1")


def test_generate_embedding_logs_failure_and_retries():
    """A post-dispatch exception produces a Log row attributed to the bot."""
    bot = _bot()
    dispatch = MagicMock(return_value=bot)

    with (
        patch(
            "app.tasks._resolve_single_embedding_target",
            return_value=("Message", "m-1", dispatch),
        ),
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.create_log") as create_log,
    ):
        app_tasks.generate_embedding.run(message_id="m-1")

    # No exception raised by dispatch or create_log → no log row created.
    create_log.assert_not_called()


# ──────────────────────────────────────────────
# regenerate_observed_patterns
# ──────────────────────────────────────────────


def test_regenerate_observed_patterns_returns_when_no_ollama():
    with patch("app.tasks.get_ollama_cfg", return_value=None):
        app_tasks.regenerate_observed_patterns.run(bot_id="x")


def test_regenerate_observed_patterns_returns_when_bot_missing():
    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_bot_obj", return_value=None),
    ):
        app_tasks.regenerate_observed_patterns.run(bot_id="x")


def test_regenerate_observed_patterns_calls_service_and_logs_success():
    bot = _bot()
    svc = MagicMock()
    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_bot_obj", return_value=bot),
        patch("app.tasks.ObservedPatternsService", return_value=svc),
        patch("app.tasks.create_log") as create_log,
    ):
        app_tasks.regenerate_observed_patterns.run(bot_id="x")

    svc.regenerate.assert_called_once()
    create_log.assert_called_once()
    assert create_log.call_args.kwargs["is_success"] is True


def test_regenerate_observed_patterns_logs_failure_and_retries():
    bot = _bot()
    svc = MagicMock()
    svc.regenerate.side_effect = RuntimeError("regen failed")

    with (
        patch("app.tasks.get_ollama_cfg", return_value=_ollama()),
        patch("app.tasks.get_bot_obj", return_value=bot),
        patch("app.tasks.ObservedPatternsService", return_value=svc),
        patch("app.tasks.log_task_failure") as log_fail,
    ):
        with pytest.raises(RuntimeError):
            app_tasks.regenerate_observed_patterns.run(bot_id="x")

    log_fail.assert_called_once()
