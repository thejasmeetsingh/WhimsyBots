"""Tests for 'src/services/bot_processor.py'.

'BotMessageProcessor' is the orchestration layer between the Celery
task and the lower-level services. The biggest testing hurdle is that
'_get_tools_config' is synchronous but calls
'asyncio.run(MCPToolsBuilder.build_tools_from_servers(...))' internally.

We patch 'asyncio.run' to a sync mock so we don't need a real event
loop, and we patch 'MCPToolsBuilder.build_tools_from_servers' to a
plain function (not coroutine) since 'asyncio.run' is what makes it
async.

After the mcp_tools refactor:
- '_get_tools_config' no longer accepts a 'servers_to_exclude' parameter
  and no longer merges in 'MCPServer.get_default_mcp_servers()'.
  Default tools (cron, PDF, web search) are appended directly to the
  'tool_definitions' list passed to 'ContextAssembler'.
- 'process_cron_job' now relies on the loop's 'exclude_crons' flag
  (set on 'run_tool_calling_loop') to drop the cron tools — there is
  no MCP server to exclude at the server level.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mcp_tools.tools import (
    CRON_JOB_TOOLS,
    PDF_GENERATOR_TOOLS,
    WEB_SEARCH_TOOLS,
)
from services.bot_processor import BotMessageProcessor

# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _bot():
    return SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ollama_model="llama3",
        telegram_bot_token="TOK",
        telegram_chat_id="999",
    )


def _ollama():
    return SimpleNamespace(num_ctx=4096, num_predict=None, temperature=0.7)


def _ollama_client():
    return MagicMock(name="OllamaClient")


def _msg(content: str = "hi", embedding=None):
    return SimpleNamespace(
        id="m-1",
        role="U",
        content=content,
        content_embedding=embedding,
    )


@pytest.fixture
def patch_telegram_client():
    """Avoid constructing a real TelegramClient (and its Redis dependency)
    by patching `TelegramClientManager.create_client` everywhere we build
    a BotMessageProcessor.
    """
    with patch("services.bot_processor.TelegramClientManager.create_client") as create:
        client = MagicMock(name="TelegramClient")
        create.return_value = client
        yield client


# ──────────────────────────────────────────────
# Constructor
# ──────────────────────────────────────────────


def test_constructor_wires_telegram_client(patch_telegram_client):
    bot = _bot()
    with patch("services.bot_processor.TelegramClientManager") as mgr:
        proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    mgr.create_client.assert_called_once_with(bot)
    assert proc.telegram_client is mgr.create_client.return_value


# ──────────────────────────────────────────────
# _get_tools_config — synchronous wrapper around async builder
# ──────────────────────────────────────────────


def test_get_tools_config_uses_asyncio_run_to_bridge_sync_to_async(
    patch_telegram_client,
):
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())

    fake_configs = [SimpleNamespace(tool={"name": "t"}, config={}, transport_type="L")]

    # Patch the builder at the symbol the source actually calls,
    # AND patch asyncio.run so we never enter a real event loop.
    with (
        patch(
            "services.bot_processor.MCPToolsBuilder.build_tools_from_servers",
            return_value=fake_configs,
        ) as builder,
        patch(
            "services.bot_processor.asyncio.run",
            side_effect=lambda coro: fake_configs,
        ) as runner,
        patch("services.bot_processor.MCPServer.objects.filter", return_value=[]),
    ):
        result = proc._get_tools_config()

    # asyncio.run was used to bridge sync → async.
    runner.assert_called_once()
    builder.assert_called_once()
    assert result == fake_configs


def test_get_tools_config_returns_builder_output_directly(patch_telegram_client):
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())

    fake_configs = [SimpleNamespace(tool={"name": "t1"}, config={}, transport_type="L")]
    with (
        patch(
            "services.bot_processor.MCPToolsBuilder.build_tools_from_servers",
            return_value=fake_configs,
        ),
        patch("services.bot_processor.asyncio.run", side_effect=lambda coro: fake_configs),
        patch("services.bot_processor.MCPServer.objects.filter", return_value=[]),
    ):
        result = proc._get_tools_config()
    assert result is fake_configs


def test_get_tools_config_filters_active_mcp_servers(patch_telegram_client):
    """'_get_tools_config' must call 'MCPServer.objects.filter' with
    is_active=True and scope it to the bot id.
    """
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())

    captured = {}

    def fake_filter(*args, **kwargs):
        captured["kwargs"] = kwargs
        return []

    with (
        patch(
            "services.bot_processor.MCPServer.objects.filter",
            side_effect=fake_filter,
        ),
        patch(
            "services.bot_processor.asyncio.run",
            side_effect=lambda coro: [],
        ),
    ):
        proc._get_tools_config()

    assert captured["kwargs"]["bot_id"] == bot.id
    assert captured["kwargs"]["is_active"] is True


def test_get_tools_config_passes_bot_id_to_builder(patch_telegram_client):
    """The 'bot_id' is forwarded to the builder so it can scope its
    Redis-backed tool-list cache per bot.
    """
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())

    with (
        patch(
            "services.bot_processor.MCPServer.objects.filter",
            return_value=[],
        ),
        patch(
            "services.bot_processor.asyncio.run",
            side_effect=lambda coro: [],
        ),
        patch(
            "services.bot_processor.MCPToolsBuilder.build_tools_from_servers",
            return_value=[],
        ) as builder,
    ):
        proc._get_tools_config()

    # bot_id must be passed as a kwarg.
    assert builder.call_args.kwargs["bot_id"] == str(bot.id)


def test_get_tools_config_annotates_distance_when_query_vector_given(
    patch_telegram_client,
):
    """When a 'query_vector' is supplied, '_get_tools_config' annotates
    the queryset with 'CosineDistance' and orders by it.
    """
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())

    annotated_qs = MagicMock(name="annotated_qs")
    annotated_qs.annotate.return_value.order_by.return_value = []

    raw_qs = MagicMock(name="raw_qs")
    raw_qs.annotate.return_value.order_by.return_value = []

    with (
        patch("services.bot_processor.MCPServer.objects.filter", return_value=raw_qs),
        patch(
            "services.bot_processor.CosineDistance",
            return_value="distance-expr",
        ),
        patch(
            "services.bot_processor.asyncio.run",
            side_effect=lambda coro: [],
        ),
    ):
        proc._get_tools_config(query_vector=[0.1, 0.2, 0.3])

    raw_qs.annotate.assert_called_once()
    raw_qs.annotate.return_value.order_by.assert_called_once_with("distance")


# ──────────────────────────────────────────────
# process_message
# ──────────────────────────────────────────────


def test_process_message_returns_response_and_duration(patch_telegram_client):
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    msg = _msg()

    with (
        patch.object(proc, "_get_tools_config", return_value=[]),
        patch("services.bot_processor.ContextAssembler") as Ctx,
        patch("services.bot_processor.run_tool_calling_loop") as loop,
    ):
        Ctx.return_value.assemble.return_value = SimpleNamespace(
            history=[], budget=SimpleNamespace(recommended_tool_count=0)
        )
        loop.return_value = ("the answer", 123)
        response, ms = proc.process_message(msg)

    assert response == "the answer"
    assert ms == 123


def test_process_message_sends_typing_indicator(patch_telegram_client):
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    msg = _msg()

    with (
        patch.object(proc, "_get_tools_config", return_value=[]),
        patch("services.bot_processor.ContextAssembler") as Ctx,
        patch("services.bot_processor.run_tool_calling_loop", return_value=("r", 1)),
    ):
        Ctx.return_value.assemble.return_value = SimpleNamespace(
            history=[], budget=SimpleNamespace(recommended_tool_count=0)
        )
        proc.process_message(msg)

    proc.telegram_client.send_typing_action.assert_called_once()


def test_process_message_passes_query_vector_for_tools(patch_telegram_client):
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    embedding = [0.1, 0.2, 0.3]
    msg = _msg(embedding=embedding)

    with (
        patch.object(proc, "_get_tools_config", return_value=[]) as get_tools,
        patch("services.bot_processor.ContextAssembler") as Ctx,
        patch("services.bot_processor.run_tool_calling_loop", return_value=("r", 1)),
    ):
        Ctx.return_value.assemble.return_value = SimpleNamespace(
            history=[], budget=SimpleNamespace(recommended_tool_count=0)
        )
        proc.process_message(msg)

    # The message's content_embedding is forwarded as the query vector.
    assert get_tools.call_args.kwargs["query_vector"] == embedding


def test_process_message_truncates_tools_to_recommended_count(patch_telegram_client):
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())

    # Three tool configs available; budget says use at most 2.
    tool_a = SimpleNamespace(tool={"name": "a"}, config={}, transport_type="L")
    tool_b = SimpleNamespace(tool={"name": "b"}, config={}, transport_type="L")
    tool_c = SimpleNamespace(tool={"name": "c"}, config={}, transport_type="L")

    with (
        patch.object(proc, "_get_tools_config", return_value=[tool_a, tool_b, tool_c]),
        patch("services.bot_processor.ContextAssembler") as Ctx,
        patch("services.bot_processor.run_tool_calling_loop", return_value=("r", 1)) as loop,
    ):
        Ctx.return_value.assemble.return_value = SimpleNamespace(
            history=[], budget=SimpleNamespace(recommended_tool_count=2)
        )
        proc.process_message(_msg())

    # Only the first two tools were passed to the loop.
    passed_tools = loop.call_args.kwargs["tools_config"]
    assert len(passed_tools) == 2
    assert passed_tools[0] is tool_a
    assert passed_tools[1] is tool_b


def test_process_message_raises_validation_error_upon_bad_response(
    patch_telegram_client,
):
    from pydantic import ValidationError

    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())

    with (
        patch.object(proc, "_get_tools_config", return_value=[]),
        patch("services.bot_processor.ContextAssembler") as Ctx,
        patch(
            "services.bot_processor.run_tool_calling_loop",
            side_effect=ValidationError.from_exception_data("x", []),
        ),
    ):
        Ctx.return_value.assemble.return_value = SimpleNamespace(
            history=[], budget=SimpleNamespace(recommended_tool_count=0)
        )
        with pytest.raises(ValidationError):
            proc.process_message(_msg())


def test_process_message_appends_default_tool_groups(patch_telegram_client):
    """The ContextAssembler is given the bot's MCP tools PLUS the
    built-in web search, PDF, and cron tool groups.
    """
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())

    bot_tool = {"name": "bot_tool"}
    with (
        patch.object(proc, "_get_tools_config", return_value=[SimpleNamespace(tool=bot_tool)]),
        patch("services.bot_processor.ContextAssembler") as Ctx,
        patch("services.bot_processor.run_tool_calling_loop", return_value=("r", 1)),
    ):
        Ctx.return_value.assemble.return_value = SimpleNamespace(
            history=[], budget=SimpleNamespace(recommended_tool_count=0)
        )
        proc.process_message(_msg())

    # The system receives the bot's tools + the three default groups
    # (concatenated into a single list of tool definitions).
    assemble_kwargs = Ctx.return_value.assemble.call_args.kwargs
    defs = assemble_kwargs["tool_definitions"]
    assert defs[0] is bot_tool

    # Build the set of advertised function names and ensure every default
    # tool is present.
    advertised_names = {d.get("function", {}).get("name") for d in defs}
    for group in (WEB_SEARCH_TOOLS, PDF_GENERATOR_TOOLS, CRON_JOB_TOOLS):
        for tool in group:
            assert tool["function"]["name"] in advertised_names


# ──────────────────────────────────────────────
# process_cron_job
# ──────────────────────────────────────────────


def test_process_cron_job_uses_cron_schedule_embedding(patch_telegram_client):
    """The cron job's 'schedule_embedding' is forwarded as query vector."""
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    cron_job = SimpleNamespace(
        name="daily digest",
        description="summary every morning",
        schedule_embedding=[0.1] * 4,
    )

    with (
        patch.object(proc, "_get_tools_config", return_value=[]) as get_tools,
        patch("services.bot_processor.run_tool_calling_loop", return_value=("r", 10)),
    ):
        proc.process_cron_job(cron_job)

    # The cron job's schedule_embedding is forwarded as the query vector.
    assert get_tools.call_args.kwargs["query_vector"] == [0.1] * 4


def test_process_cron_job_uses_cron_prompt_template(patch_telegram_client):
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    cron_job = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        name="daily digest",
        description="summary every morning",
        schedule_embedding=None,
    )

    with (
        patch.object(proc, "_get_tools_config", return_value=[]),
        patch("services.bot_processor.run_tool_calling_loop", return_value=("r", 10)) as loop,
    ):
        proc.process_cron_job(cron_job)

    history = loop.call_args.kwargs["history"]
    assert len(history) == 1
    assert history[0]["role"] == "user"
    # Both name and description show up in the prompt.
    assert "daily digest" in history[0]["content"]
    assert "summary every morning" in history[0]["content"]
    # bot_id placeholder is NOT in the new prompt (the value is no longer needed there).
    assert "{" not in history[0]["content"]


def test_process_cron_job_passes_bot_id_and_telegram_client(patch_telegram_client):
    """Both bot_id and telegram_client are forwarded to the loop so the
    default tools (cron, PDF) can use them.
    """
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    cron_job = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        name="n",
        description="d",
        schedule_embedding=None,
    )

    with (
        patch.object(proc, "_get_tools_config", return_value=[]),
        patch("services.bot_processor.run_tool_calling_loop", return_value=("r", 10)) as loop,
    ):
        proc.process_cron_job(cron_job)

    assert loop.call_args.kwargs["bot_id"] == str(bot.id)
    assert loop.call_args.kwargs["telegram_client"] is proc.telegram_client


def test_process_cron_job_sets_exclude_crons_true(patch_telegram_client):
    """When the loop is driven by a cron job, cron job tools must be
    excluded from the advertised tool list to prevent recursive scheduling.
    """
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    cron_job = SimpleNamespace(
        id="c",
        name="n",
        description="d",
        schedule_embedding=None,
    )

    with (
        patch.object(proc, "_get_tools_config", return_value=[]),
        patch("services.bot_processor.run_tool_calling_loop", return_value=("r", 10)) as loop,
    ):
        proc.process_cron_job(cron_job)

    assert loop.call_args.kwargs["exclude_crons"] is True


def test_process_cron_job_does_not_pass_keep_alive(patch_telegram_client):
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    cron_job = SimpleNamespace(
        id="c",
        name="n",
        description="d",
        schedule_embedding=None,
    )

    with (
        patch.object(proc, "_get_tools_config", return_value=[]),
        patch("services.bot_processor.run_tool_calling_loop", return_value=("r", 10)) as loop,
    ):
        proc.process_cron_job(cron_job)

    # Cron jobs don't pass keep_alive (default add_keep_alive=False).
    assert "add_keep_alive" not in loop.call_args.kwargs or not loop.call_args.kwargs.get(
        "add_keep_alive"
    )


# ──────────────────────────────────────────────
# send_response
# ──────────────────────────────────────────────


def test_send_response_persists_assistant_message_and_sends_to_telegram(
    patch_telegram_client,
):
    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())

    with patch("services.bot_processor.Message.objects.create") as create:
        proc.send_response("the answer")

    proc.telegram_client.send_message.assert_called_once_with(text="the answer")
    create.assert_called_once()
    kwargs = create.call_args.kwargs
    assert kwargs["bot"] is bot
    assert kwargs["role"] == "A"  # ASSISTANT
    assert kwargs["content"] == "the answer"


def test_send_response_raises_and_logs_on_telegram_failure(patch_telegram_client, caplog):
    import logging

    bot = _bot()
    proc = BotMessageProcessor(bot, _ollama(), _ollama_client())
    proc.telegram_client.send_message.side_effect = RuntimeError("tg down")

    with (
        patch("services.bot_processor.Message.objects.create") as create,
        caplog.at_level(logging.ERROR),
    ):
        with pytest.raises(RuntimeError):
            proc.send_response("x")

    # Message persistence must NOT happen when sending failed.
    create.assert_not_called()
    assert any("Failed to send response" in r.message for r in caplog.records)
