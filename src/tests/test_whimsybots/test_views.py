"""Tests for 'src/whimsybots/views.py'.

We use Django's 'RequestFactory' to build synthetic POST requests without
spinning up a real HTTP server, and patch 'telegram_msg_handler.apply_async'
to verify the queued task signature.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.http.response import HttpResponse, HttpResponseBadRequest
from django.test import RequestFactory

from whimsybots.views import TelegramWebhook


@pytest.fixture
def factory():
    return RequestFactory()


# ──────────────────────────────────────────────
# Missing token → 400
# ──────────────────────────────────────────────


def test_post_without_token_returns_bad_request(factory):
    """'token' is captured from the URL kwargs. If absent, respond 400."""

    view = TelegramWebhook()
    request = factory.post("/webhook/", data="{}", content_type="application/json")

    response = view.post(request)

    assert isinstance(response, HttpResponseBadRequest)
    assert response.status_code == 400
    assert response.content == b"No bot token provided"


def test_post_without_token_does_not_queue_task(factory):
    view = TelegramWebhook()
    request = factory.post("/webhook/", data="{}", content_type="application/json")

    with patch("whimsybots.views.telegram_msg_handler") as task:
        view.post(request)

    task.apply_async.assert_not_called()


# ──────────────────────────────────────────────
# Empty payload → 400
# ──────────────────────────────────────────────


def test_post_with_empty_body_returns_bad_request(factory):
    view = TelegramWebhook()
    request = factory.post("/webhook/ABC/", data="", content_type="application/json")

    response = view.post(request, token="ABC")

    assert isinstance(response, HttpResponseBadRequest)
    assert response.status_code == 400
    assert response.content == b"Empty payload received"


def test_post_with_empty_body_does_not_queue_task(factory):
    view = TelegramWebhook()
    request = factory.post("/webhook/ABC/", data="", content_type="application/json")

    with patch("whimsybots.views.telegram_msg_handler") as task:
        view.post(request, token="ABC")

    task.apply_async.assert_not_called()


# ──────────────────────────────────────────────
# Valid payload → 200 OK + queue task
# ──────────────────────────────────────────────


def test_post_with_valid_payload_returns_empty_200(factory):
    view = TelegramWebhook()
    payload = {"update_id": 1, "message": {"message_id": 5, "text": "hi"}}
    request = factory.post(
        "/webhook/TOKEN/",
        data=json.dumps(payload),
        content_type="application/json",
    )

    with patch("whimsybots.views.telegram_msg_handler"):
        response = view.post(request, token="TOKEN")

    assert isinstance(response, HttpResponse)
    assert response.status_code == 200
    assert response.content == b""


def test_post_with_valid_payload_queues_telegram_task(factory):
    """The view must dispatch `telegram_msg_handler.apply_async(...)` with
    the URL-captured token and the decoded JSON update."""

    view = TelegramWebhook()
    payload = {
        "update_id": 7,
        "message": {"message_id": 1, "chat": {"id": 99}, "text": "hello"},
    }
    request = factory.post(
        "/webhook/TOKEN/",
        data=json.dumps(payload),
        content_type="application/json",
    )

    with patch("whimsybots.views.telegram_msg_handler") as task:
        view.post(request, token="TOKEN")

    task.apply_async.assert_called_once()
    kwargs = task.apply_async.call_args.kwargs
    assert kwargs["queue"] == "default"
    assert kwargs["kwargs"] == {"bot_token": "TOKEN", "update": payload}


def test_post_passes_through_token_from_url_kwargs(factory):
    """The bot token must come from `**kwargs` (URL parameter), not the body."""

    view = TelegramWebhook()
    payload = {"update_id": 1}
    request = factory.post(
        "/webhook/SECRET-TOKEN/",
        data=json.dumps(payload),
        content_type="application/json",
    )

    with patch("whimsybots.views.telegram_msg_handler") as task:
        view.post(request, token="SECRET-TOKEN")

    task.apply_async.assert_called_once()
    assert task.apply_async.call_args.kwargs["kwargs"]["bot_token"] == "SECRET-TOKEN"


# ──────────────────────────────────────────────
# Malformed JSON → propagates (view doesn't catch)
# ──────────────────────────────────────────────


def test_post_with_malformed_json_raises_json_decode_error(factory):
    """The view does not catch JSON decode errors; that's intentional —
    the celery task is the consumer, and a malformed payload should bubble
    up so the operator can debug."""

    view = TelegramWebhook()
    request = factory.post(
        "/webhook/TOK/",
        data="not json {",
        content_type="application/json",
    )

    with pytest.raises(json.JSONDecodeError):
        view.post(request, token="TOK")
