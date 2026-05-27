"""
Telegram webhook views for handling incoming messages.

This module provides Django class-based views to receive Telegram webhook
updates and queue them for async processing via Celery tasks.
"""

import json
import logging

from django.http.response import HttpResponse, HttpResponseBadRequest
from django.views import View

from app.tasks import telegram_msg_handler

logger = logging.getLogger(__name__)


class TelegramWebhook(View):
    """
    Django view for handling Telegram webhook updates.

    Receives POST requests from Telegram API when users send messages to bots.
    Validates the request, extracts the bot token from URL, and queues the
    update for async processing via the telegram_msg_handler Celery task.
    """

    def post(self, request, *args, **kwargs):
        """
        Handle incoming Telegram webhook POST request.

        Validates the request payload and bot token, then queues the update
        for async processing. Returns immediately to acknowledge receipt to
        Telegram API (which requires response within 23 seconds).

        Args:
            request (HttpRequest): Django HTTP request object
            *args: Unused positional arguments
            **kwargs: URL parameters including 'token' (bot token from URL path)

        Returns:
            HttpResponse: Empty 200 OK on success
            HttpResponseBadRequest: If token missing or payload empty/invalid

        Request Body:
            JSON payload from Telegram API containing update object:
            {
                "update_id": 123456789,
                "message": {
                    "message_id": 123,
                    "chat": {"id": 987654321, ...},
                    "text": "Hello bot!",
                    ...
                }
            }
        """

        if not kwargs.get("token"):
            return HttpResponseBadRequest(content="No bot token provided")

        payload = request.body.decode("utf-8")

        if not payload:
            return HttpResponseBadRequest(content="Empty payload received")

        update = json.loads(payload)

        telegram_msg_handler.apply_async(
            queue="default", kwargs={"bot_token": kwargs["token"], "update": update}
        )

        return HttpResponse()
