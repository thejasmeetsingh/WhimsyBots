"""Tests for `src/app/admin.py` (Tier 5 — admin classes).

The admin classes are mostly thin Django glue: filtering, custom
permissions, save_model hooks, and display helpers. We exercise them
on `SimpleNamespace`-style mocks so the tests stay fast and never
touch a real DB. `format_html` (used by `last_execution_status`) is
imported at module load time, so it stays real.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib import admin as django_admin
from django.test import RequestFactory

from app.admin import (
    BaseReadOnlyUserFilteredAdmin,
    BaseUserFilteredAdmin,
    BotAdmin,
    CronJobAdmin,
    LogAdmin,
    MCPServerAdmin,
    MessageAdmin,
    OllamaAdmin,
)
from app.models import Bot, CronJob, Log, MCPServer, Message, Ollama


@pytest.fixture
def factory():
    return RequestFactory()


# ──────────────────────────────────────────────
# BaseUserFilteredAdmin
# ──────────────────────────────────────────────


def test_base_user_filtered_admin_filters_by_created_by():
    """`get_queryset` must filter by `bot__created_by_id = request.user.id`
    AND `select_related` the `bot__created_by`."""

    parent_qs = MagicMock(name="parent_qs")
    parent_qs.filter.return_value = parent_qs
    parent_qs.select_related.return_value = parent_qs

    with patch("app.admin.admin.ModelAdmin.get_queryset", return_value=parent_qs):
        admin_inst = BaseUserFilteredAdmin(Log, django_admin.site)
        req = SimpleNamespace(user=SimpleNamespace(id=42))
        result = admin_inst.get_queryset(req)

    # Must filter on bot__created_by_id using the request user's id.
    parent_qs.filter.assert_called_once_with(bot__created_by_id=42)
    # Must select_related('bot__created_by')
    parent_qs.select_related.assert_called_once_with("bot__created_by")
    assert result is parent_qs


# ──────────────────────────────────────────────
# BaseReadOnlyUserFilteredAdmin
# ──────────────────────────────────────────────


def test_base_read_only_admin_denies_all_writes():
    """Read-only admins must not allow add/change/delete."""

    admin_inst = BaseReadOnlyUserFilteredAdmin(Log, django_admin.site)
    req = SimpleNamespace(user=SimpleNamespace(id=1, is_superuser=False))

    assert admin_inst.has_add_permission(req) is False
    assert admin_inst.has_change_permission(req) is False
    assert admin_inst.has_delete_permission(req) is False


# ──────────────────────────────────────────────
# OllamaAdmin
# ──────────────────────────────────────────────


def test_ollama_admin_has_add_permission_when_zero_rows():
    admin_inst = OllamaAdmin(Ollama, django_admin.site)
    req = SimpleNamespace(user=SimpleNamespace(id=1, is_superuser=False))

    with patch.object(Ollama.objects, "count", return_value=0):
        assert admin_inst.has_add_permission(req) is True


def test_ollama_admin_has_add_permission_false_when_row_exists():
    admin_inst = OllamaAdmin(Ollama, django_admin.site)
    req = SimpleNamespace(user=SimpleNamespace(id=1, is_superuser=False))

    with patch.object(Ollama.objects, "count", return_value=1):
        assert admin_inst.has_add_permission(req) is False


def test_ollama_admin_save_model_triggers_summary_on_num_ctx_change():
    """When num_ctx changes on an existing Ollama row, we must queue a
    `manage_conversation_summary` task."""

    admin_inst = OllamaAdmin(Ollama, django_admin.site)
    req = SimpleNamespace(user=SimpleNamespace(id=1, is_superuser=False))
    ollama = SimpleNamespace()

    # Pretend a ModelForm with changed_data = ["num_ctx"].
    form = MagicMock()
    form.changed_data = ["num_ctx"]

    with (
        patch("app.admin.manage_conversation_summary") as task,
        patch.object(django_admin.ModelAdmin, "save_model"),
    ):
        admin_inst.save_model(req, ollama, form, change=True)

    task.apply_async.assert_called_once_with(queue="default", countdown=10)


def test_ollama_admin_save_model_does_not_trigger_on_unrelated_change():
    """Changing fields other than `num_ctx` must NOT queue the summary task."""

    admin_inst = OllamaAdmin(Ollama, django_admin.site)
    req = SimpleNamespace(user=SimpleNamespace(id=1, is_superuser=False))
    ollama = SimpleNamespace()

    form = MagicMock()
    form.changed_data = ["temperature"]

    with (
        patch("app.admin.manage_conversation_summary") as task,
        patch.object(django_admin.ModelAdmin, "save_model"),
    ):
        admin_inst.save_model(req, ollama, form, change=True)

    task.apply_async.assert_not_called()


def test_ollama_admin_save_model_does_not_trigger_on_new_instance():
    """Adding a brand-new Ollama row (`change=False`) must NOT queue the task."""

    admin_inst = OllamaAdmin(Ollama, django_admin.site)
    req = SimpleNamespace(user=SimpleNamespace(id=1, is_superuser=False))
    ollama = SimpleNamespace()

    form = MagicMock()
    form.changed_data = ["num_ctx"]

    with (
        patch("app.admin.manage_conversation_summary") as task,
        patch.object(django_admin.ModelAdmin, "save_model"),
    ):
        admin_inst.save_model(req, ollama, form, change=False)

    task.apply_async.assert_not_called()


# ──────────────────────────────────────────────
# BotAdmin
# ──────────────────────────────────────────────


def test_bot_admin_get_queryset_filters_by_user_and_annotates():
    """`BotAdmin.get_queryset` must:
    - filter to the current user's bots only,
    - `select_related` created_by,
    - `prefetch_related` bot_logs,
    - annotate the various counts/subqueries.
    """

    parent_qs = MagicMock(name="parent_qs")
    parent_qs.filter.return_value = parent_qs
    parent_qs.select_related.return_value = parent_qs
    parent_qs.prefetch_related.return_value = parent_qs
    parent_qs.annotate.return_value = parent_qs

    with patch("app.admin.admin.ModelAdmin.get_queryset", return_value=parent_qs):
        admin_inst = BotAdmin(Bot, django_admin.site)
        req = SimpleNamespace(user=SimpleNamespace(id=7, is_superuser=False))
        qs = admin_inst.get_queryset(req)

    parent_qs.filter.assert_called_once_with(created_by_id=7)
    parent_qs.select_related.assert_called_once_with("created_by")
    parent_qs.prefetch_related.assert_called_once_with("bot_logs")
    parent_qs.annotate.assert_called_once()
    kwargs = parent_qs.annotate.call_args.kwargs
    for k in (
        "messages_count",
        "mcp_active_count",
        "mcp_inactive_count",
        "cron_active_count",
        "cron_inactive_count",
        "logs_success_count",
        "logs_error_count",
        "last_log_id",
        "last_log_success",
    ):
        assert k in kwargs, f"missing annotation {k!r}"
    assert qs is parent_qs


def test_bot_admin_save_model_sets_current_user():
    """`save_model` must associate the request user as `created_by`."""

    admin_inst = BotAdmin(Bot, django_admin.site)
    user = SimpleNamespace(id=11, is_superuser=False, username="me")
    req = SimpleNamespace(user=user)

    bot = SimpleNamespace(
        name="newbot",
        telegram_bot_token=None,
    )

    form = MagicMock()
    form.changed_data = []
    with patch.object(django_admin.ModelAdmin, "save_model") as parent_save:
        admin_inst.save_model(req, bot, form, change=False)

    assert bot.created_by is user
    parent_save.assert_called_once_with(req, bot, form, False)


def test_bot_admin_save_model_queues_webhook_on_create_with_token():
    """On a new bot with a token, the webhook setup task is queued."""

    admin_inst = BotAdmin(Bot, django_admin.site)
    user = SimpleNamespace(id=11, is_superuser=False)
    req = SimpleNamespace(user=user)

    bot = SimpleNamespace(
        name="newbot",
        id="00000000-0000-0000-0000-000000000099",
        telegram_bot_token="X",
    )

    form = MagicMock()
    form.changed_data = []
    with (
        patch("app.admin.setup_bot_webhook") as task,
        patch.object(django_admin.ModelAdmin, "save_model"),
    ):
        admin_inst.save_model(req, bot, form, change=False)

    task.apply_async.assert_called_once()
    call = task.apply_async.call_args
    assert call.kwargs["queue"] == "default"
    assert call.kwargs["countdown"] == 10
    assert call.kwargs["kwargs"] == {"bot_id": str(bot.id)}


def test_bot_admin_save_model_does_not_queue_webhook_without_token():
    admin_inst = BotAdmin(Bot, django_admin.site)
    user = SimpleNamespace(id=11, is_superuser=False)
    req = SimpleNamespace(user=user)

    bot = SimpleNamespace(
        name="newbot",
        id="00000000-0000-0000-0000-000000000099",
        telegram_bot_token=None,
    )

    form = MagicMock()
    form.changed_data = []
    with (
        patch("app.admin.setup_bot_webhook") as task,
        patch.object(django_admin.ModelAdmin, "save_model"),
    ):
        admin_inst.save_model(req, bot, form, change=False)

    task.apply_async.assert_not_called()


def test_bot_admin_save_model_queues_webhook_on_token_change():
    admin_inst = BotAdmin(Bot, django_admin.site)
    user = SimpleNamespace(id=11, is_superuser=False)
    req = SimpleNamespace(user=user)

    bot = SimpleNamespace(
        name="existing",
        id="00000000-0000-0000-0000-000000000099",
        telegram_bot_token="NEW-TOK",
    )
    form = MagicMock()
    form.changed_data = ["telegram_bot_token"]

    with (
        patch("app.admin.setup_bot_webhook") as task,
        patch.object(django_admin.ModelAdmin, "save_model"),
    ):
        admin_inst.save_model(req, bot, form, change=True)

    task.apply_async.assert_called_once()


def test_bot_admin_save_model_does_not_queue_webhook_on_unrelated_change():
    admin_inst = BotAdmin(Bot, django_admin.site)
    user = SimpleNamespace(id=11, is_superuser=False)
    req = SimpleNamespace(user=user)

    bot = SimpleNamespace(
        name="existing",
        id="00000000-0000-0000-0000-000000000099",
        telegram_bot_token="X",
    )
    form = MagicMock()
    form.changed_data = ["name"]

    with (
        patch("app.admin.setup_bot_webhook") as task,
        patch.object(django_admin.ModelAdmin, "save_model"),
    ):
        admin_inst.save_model(req, bot, form, change=True)

    task.apply_async.assert_not_called()


# ──────────────────────────────────────────────
# BotAdmin — display helpers
# ──────────────────────────────────────────────


def test_bot_admin_get_stats_renders_dash_when_no_obj():
    admin_inst = BotAdmin(Bot, django_admin.site)
    assert admin_inst._render_stats_table(None) == "-"


def test_bot_admin_get_stats_includes_table_when_obj_present():
    """When an obj is given, the rendered HTML must include the stats table."""

    admin_inst = BotAdmin(Bot, django_admin.site)
    obj = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000099",
        messages_count=1,
        mcp_active_count=2,
        mcp_inactive_count=3,
        cron_active_count=4,
        cron_inactive_count=5,
        logs_success_count=6,
        logs_error_count=7,
    )
    html = admin_inst._render_stats_table(obj)
    assert "<table" in html
    assert "Messages" in html
    # Each counter must appear in the rendered output.
    for v in (1, 2, 3, 4, 5, 6, 7):
        assert str(v) in html


def test_bot_admin_last_execution_status_dash_when_no_log():
    admin_inst = BotAdmin(Bot, django_admin.site)
    # No `last_log_id` attr ⇒ must return "-".
    assert admin_inst.last_execution_status(None) == "-"
    obj = SimpleNamespace(last_log_id=None)
    assert admin_inst.last_execution_status(obj) == "-"


def test_bot_admin_last_execution_status_success_icon():
    admin_inst = BotAdmin(Bot, django_admin.site)
    obj = SimpleNamespace(last_log_id=42, last_log_success=True)
    out = admin_inst.last_execution_status(obj)
    assert "icon-yes.svg" in str(out)


def test_bot_admin_last_execution_status_failure_link():
    admin_inst = BotAdmin(Bot, django_admin.site)
    obj = SimpleNamespace(last_log_id=42, last_log_success=False)
    out = admin_inst.last_execution_status(obj)
    assert "icon-no.svg" in str(out)
    assert "/admin/app/log/42" in str(out)


# ──────────────────────────────────────────────
# MCPServerAdmin — generic registration
# ──────────────────────────────────────────────


def test_mcp_server_admin_uses_form_and_lists_fields():
    """The admin class wires MCPServerForm and exposes expected list_display."""

    admin_inst = MCPServerAdmin(MCPServer, django_admin.site)
    assert admin_inst.form.__name__ == "MCPServerForm"
    assert "name" in admin_inst.list_display
    assert "is_active" in admin_inst.list_display
    assert "transport" in admin_inst.list_display
    assert "bot" in admin_inst.autocomplete_fields


# ──────────────────────────────────────────────
# MessageAdmin
# ──────────────────────────────────────────────


def test_message_admin_get_sender_uses_username_for_user_role():
    admin_inst = MessageAdmin(Message, django_admin.site)

    user = SimpleNamespace(username="alice")
    bot = SimpleNamespace(
        created_by=user,
        ollama_model="my-llama3",
    )
    msg = SimpleNamespace(bot=bot, role="U", content="hi")
    assert admin_inst.get_sender(msg) == "alice"


def test_message_admin_get_sender_uses_ollama_model_for_assistant_role():
    admin_inst = MessageAdmin(Message, django_admin.site)

    user = SimpleNamespace(username="alice")
    bot = SimpleNamespace(
        created_by=user,
        ollama_model="my-llama3",
    )
    msg = SimpleNamespace(bot=bot, role="A", content="hi")
    assert admin_inst.get_sender(msg) == "my-llama3"


def test_message_admin_get_sender_dash_when_no_obj():
    admin_inst = MessageAdmin(Message, django_admin.site)
    assert admin_inst.get_sender(None) == "-"


# ──────────────────────────────────────────────
# CronJobAdmin — overrides to allow change/delete
# ──────────────────────────────────────────────


def test_cron_job_admin_allows_change_and_delete():
    req = SimpleNamespace(user=SimpleNamespace(id=1, is_superuser=False))

    admin_inst = CronJobAdmin(CronJob, django_admin.site)
    # Despite inheriting from BaseReadOnlyUserFilteredAdmin, CronJobAdmin
    # explicitly enables change + delete.
    assert admin_inst.has_change_permission(req) is True
    assert admin_inst.has_delete_permission(req) is True
    # …but still does not allow add.
    assert admin_inst.has_add_permission(req) is False


# ──────────────────────────────────────────────
# LogAdmin — fully read-only
# ──────────────────────────────────────────────


def test_log_admin_is_fully_read_only():
    req = SimpleNamespace(user=SimpleNamespace(id=1, is_superuser=False))

    admin_inst = LogAdmin(Log, django_admin.site)
    assert admin_inst.has_add_permission(req) is False
    assert admin_inst.has_change_permission(req) is False
    assert admin_inst.has_delete_permission(req) is False
