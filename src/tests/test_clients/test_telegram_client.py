"""Tests for `src/clients/telegram.py` (Tier 4 — External-System Clients).

TelegramClient wraps the Telegram Bot API. It uses:

  - `requests.post` — patched at the import site (`clients.telegram.requests.post`).
  - `redis.from_url` — patched to inject a `fake_redis` instance.
  - `RateLimiter` (real one, real behaviour with fakeredis).
  - `utils.text.split_message` — real behaviour (covered in Tier 1).

Test plan highlights:
  - 429 → `TelegramRateLimitError(retry_after=...)`.
  - non-200 → `TelegramError`.
  - non-`ok` body → `TelegramError`.
  - Markdown parse failure → retry without `parse_mode`.
  - long text → chunked via `split_message`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from clients.telegram import TelegramClient, TelegramError, TelegramRateLimitError


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _ok_response(result: dict | None = None):
    """Build a `requests.post` return value that looks like a 200 OK."""
    resp = MagicMock(name="Response")
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "result": result or {"message_id": 1}}
    resp.text = "ok"
    return resp


def _error_response(status_code: int, body: dict | None = None):
    resp = MagicMock(name="Response")
    resp.status_code = status_code
    resp.json.return_value = body or {"ok": False, "description": "bad"}
    resp.text = str(body or {"ok": False})
    return resp


def _rate_limited_response(retry_after: int = 5):
    return _error_response(
        429,
        {"ok": False, "parameters": {"retry_after": retry_after}},
    )


@pytest.fixture
def tg(fake_redis):
    """Build a TelegramClient whose redis.from_url returns fake_redis.

    We also rate-limit-disable by pre-loading the rate limiter so the
    'proactive' check always passes. (Tier-1 rate_limiter tests cover
    the throttling behaviour itself.)
    """

    with patch("clients.telegram.redis.from_url", return_value=fake_redis):
        client = TelegramClient("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", "999")
    # Reset the local rate limiter so we don't accidentally throttle
    # ourselves across tests (defensive — fake_redis is per-test).
    fake_redis.flushall()
    return client


@pytest.fixture
def patched_post():
    """Patch `clients.telegram.requests.post`. Yields the mock."""
    with patch("clients.telegram.requests.post") as post:
        yield post


# ──────────────────────────────────────────────
# Constructor & basic wiring
# ──────────────────────────────────────────────


def test_constructor_sets_base_url_from_token():
    with patch("clients.telegram.redis.from_url") as fr:
        client = TelegramClient("MYTOKEN:abc", "999")
    assert client.base_url == "https://api.telegram.org/botMYTOKEN:abc"
    assert client.chat_id == "999"
    assert client._token == "MYTOKEN:abc"
    fr.assert_called_once()


def test_constructor_uses_redis_from_url():
    with patch("clients.telegram.redis.from_url") as fr:
        fr.return_value = MagicMock(name="Redis")
        TelegramClient("TOK", None)
    # `redis.from_url` is invoked with the Celery broker URL.
    fr.assert_called_once()


def test_constructor_chat_id_optional():
    with patch("clients.telegram.redis.from_url") as fr:
        fr.return_value = MagicMock(name="Redis")
        client = TelegramClient("TOK")
    assert client.chat_id is None


# ──────────────────────────────────────────────
# _post — error handling
# ──────────────────────────────────────────────


def test_post_returns_result_on_200(patched_post, tg):
    patched_post.return_value = _ok_response({"message_id": 42})
    result = tg._post("sendMessage", {"chat_id": "999", "text": "hi"})
    assert result == {"message_id": 42}


def test_post_calls_correct_url_with_payload(patched_post, tg):
    patched_post.return_value = _ok_response()
    tg._post("sendMessage", {"chat_id": "999", "text": "hi"})
    patched_post.assert_called_once_with(
        url="https://api.telegram.org/bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/sendMessage",
        json={"chat_id": "999", "text": "hi"},
    )


def test_post_raises_rate_limit_error_on_429(patched_post, tg):
    patched_post.return_value = _rate_limited_response(retry_after=7)
    with pytest.raises(TelegramRateLimitError) as excinfo:
        tg._post("sendMessage", {"chat_id": "999", "text": "hi"})
    assert excinfo.value.retry_after == 7


def test_post_raises_error_on_non_200(patched_post, tg):
    patched_post.return_value = _error_response(500, {"description": "boom"})
    with pytest.raises(TelegramError) as excinfo:
        tg._post("sendMessage", {"chat_id": "999", "text": "hi"})
    assert "500" in str(excinfo.value)


def test_post_raises_error_when_response_not_ok(patched_post, tg):
    resp = MagicMock(name="Response")
    resp.status_code = 200
    resp.json.return_value = {"ok": False, "description": "bad request"}
    resp.text = ""
    patched_post.return_value = resp

    with pytest.raises(TelegramError):
        tg._post("sendMessage", {"chat_id": "999", "text": "hi"})


# ──────────────────────────────────────────────
# send_message — basic + chunking
# ──────────────────────────────────────────────


def test_send_message_single_chunk(patched_post, tg):
    patched_post.return_value = _ok_response({"message_id": 1})
    result = tg.send_message("hello world")
    # Single chunk — one HTTP call.
    assert patched_post.call_count == 1
    assert result == {"message_id": 1}


def test_send_message_passes_parse_mode(patched_post, tg):
    patched_post.return_value = _ok_response()
    tg.send_message("hello", parse_mode="HTML")
    sent = patched_post.call_args.kwargs["json"]
    assert sent["parse_mode"] == "HTML"


def test_send_message_chunks_long_text(patched_post, tg):
    """Text > 4096 chars must be split via utils.text.split_message."""
    patched_post.return_value = _ok_response()
    # 10 000 chars — guaranteed to be split.
    long_text = "x" * 10_000
    tg.send_message(long_text)
    # More than one HTTP call ⇒ message was split.
    assert patched_post.call_count > 1


def test_send_message_retries_without_parse_mode_on_markdown_error(patched_post, tg):
    """When Telegram complains about markdown entities, retry as plain text."""

    parse_err = TelegramError("400: Bad Request: can't parse entities")
    ok = _ok_response({"message_id": 99})

    # First call: 400 with markdown parse failure. Second call: 200 plain.
    patched_post.side_effect = [
        _error_response(400, {"description": "can't parse entities"}),
        ok,
    ]

    result = tg.send_message("*bold* and _italic_", parse_mode="Markdown")
    assert result == {"message_id": 99}
    assert patched_post.call_count == 2

    # Second call must NOT include parse_mode.
    second_payload = patched_post.call_args_list[1].kwargs["json"]
    assert "parse_mode" not in second_payload


def test_send_message_does_not_retry_on_unrelated_errors(patched_post, tg):
    """Only markdown-parse failures trigger the plain-text fallback.

    For any other TelegramError the plain-text retry must NOT be issued —
    only the original call (with parse_mode) is made.
    """
    patched_post.return_value = _error_response(
        500, {"description": "internal server error"}
    )

    tg.send_message("hello")

    # Exactly one HTTP call — no plain-text fallback for unrelated errors.
    assert patched_post.call_count == 1
    # And that call still included parse_mode.
    assert patched_post.call_args.kwargs["json"].get("parse_mode") == "Markdown"


# ──────────────────────────────────────────────
# send_typing_action
# ──────────────────────────────────────────────


def test_send_typing_action_posts_to_send_chat_action(patched_post, tg):
    patched_post.return_value = _ok_response()
    tg.send_typing_action()
    payload = patched_post.call_args.kwargs["json"]
    assert payload == {"chat_id": "999", "action": "typing"}


def test_send_typing_action_propagates_api_errors(patched_post, tg):
    patched_post.return_value = _error_response(403, {"description": "forbidden"})
    with pytest.raises(TelegramError):
        tg.send_typing_action()


# ──────────────────────────────────────────────
# set_webhook
# ──────────────────────────────────────────────


def test_set_webhook_basic(patched_post, tg):
    patched_post.return_value = _ok_response({"ok": True})
    tg.set_webhook("https://example.com/webhook/bot")
    payload = patched_post.call_args.kwargs["json"]
    assert payload == {"url": "https://example.com/webhook/bot"}


def test_set_webhook_with_allowed_updates(patched_post, tg):
    patched_post.return_value = _ok_response()
    tg.set_webhook(
        "https://example.com/webhook/bot",
        allowed_updates=["message", "callback_query"],
    )
    payload = patched_post.call_args.kwargs["json"]
    assert payload["url"] == "https://example.com/webhook/bot"
    assert payload["allowed_updates"] == ["message", "callback_query"]


def test_set_webhook_propagates_rate_limit(patched_post, tg):
    patched_post.return_value = _rate_limited_response(retry_after=2)
    with pytest.raises(TelegramRateLimitError) as excinfo:
        tg.set_webhook("https://example.com/webhook/bot")
    assert excinfo.value.retry_after == 2


# ──────────────────────────────────────────────
# Exception classes
# ──────────────────────────────────────────────


def test_telegram_error_is_exception():
    assert issubclass(TelegramError, Exception)


def test_telegram_rate_limit_error_is_telegram_error():
    assert issubclass(TelegramRateLimitError, TelegramError)


def test_telegram_rate_limit_error_message_includes_retry_after():
    err = TelegramRateLimitError(retry_after=12)
    assert err.retry_after == 12
    assert "12" in str(err)


# ──────────────────────────────────────────────
# Rate-limit constants
# ──────────────────────────────────────────────


def test_rate_limit_constants_match_telegram_limit():
    # Telegram Bot API: 1 msg/sec per chat to the same group/user.
    # These constants back the proactive local throttle.
    assert TelegramClient._RATE_LIMIT_RETRIES == 3
    assert TelegramClient._RATE_LIMIT_WAIT == 1
