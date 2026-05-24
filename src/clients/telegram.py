"""
Telegram bot client.

Provides a high-level interface for Telegram Bot API interactions.
Handles message sending, file uploads, user actions, and webhook updates.
"""

import logging
import time
from typing import Dict, List, Optional

import redis
import requests
from django.conf import settings

from services.rate_limiter import RateLimiter
from app.utils import split_message


logger = logging.getLogger(__name__)


class TelegramError(Exception):
    """
    Custom exception for Telegram API errors.

    Raised when:
    - HTTP request fails
    - Telegram API returns error status
    - Required chat_id is missing
    """

    pass


class TelegramRateLimitError(TelegramError):
    """
    Raised when Telegram returns a 429 Too Many Requests response.

    Attributes:
        retry_after (int): Seconds to wait before retrying, as specified
                           by Telegram in the response body.
    """

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Telegram rate limit hit. Retry after {retry_after}s")


class TelegramClient:
    """
    Client for interacting with Telegram Bot API.

    Provides methods to send messages, documents, and user actions to a Telegram chat.
    Automatically handles message splitting for long texts (Telegram limit: 4096 chars).

    Attributes:
        base_url (str): Telegram API base URL (constructed from token)
        chat_id (str): Target chat ID for operations

    Example:
        >>> from clients import TelegramClient
        >>> client = TelegramClient("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", "987654321")
        >>> client.send_message("Hello from WhimsyBots!")
        >>> client.send_typing_action()
    """

    base_url: str = None
    chat_id: str = None

    # Max attempts for the inline rate limit retry loop in send_message/send_document.
    _RATE_LIMIT_RETRIES = 3
    _RATE_LIMIT_WAIT = 1  # seconds to wait between retries when throttled locally

    def __init__(self, token: str, chat_id: str = None):
        """
        Initialize Telegram client.

        Args:
            token (str): Telegram bot API token (from BotFather)
                Format: "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
            chat_id (str | None): Default target chat ID
                Can be overridden per method if needed

        Example:
            >>> client = TelegramClient(
            ...     token="123456:ABC-DEF...",
            ...     chat_id="987654321"
            ... )
        """

        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"

        self._token = token

        # Instantiate Redis client from broker URL (redis package already present via Celery)
        self._redis = redis.from_url(settings.CELERY_BROKER_URL)
        self._rate_limiter = RateLimiter(self._redis, token)

    def _post(self, endpoint: str, payload: Dict) -> Dict:
        """
        Make authenticated POST request to Telegram API.

        Handles HTTP request, response validation, and error handling.

        Args:
            endpoint (str): API endpoint name (e.g., 'sendMessage', 'sendDocument')
            payload (dict): Request body as JSON

        Returns:
            dict: Response result (from api response["result"])

        Raises:
            TelegramError: If HTTP status != 200 or ok != true
            TelegramRateLimitError: with retry_after on 429.

        Internal method - not meant to be called directly.
        """

        url = f"{self.base_url}/{endpoint}"
        response = requests.post(url=url, json=payload)

        # Rate Limit Handling
        if response.status_code == 429:
            data = response.json()
            retry_after = data.get("parameters", {}).get("retry_after", 1)
            raise TelegramRateLimitError(retry_after=retry_after)

        if response.status_code != 200:
            raise TelegramError(
                f"Telegram API error: Status code - {response.status_code}"
            )

        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram API error on {endpoint}: {data}")

        return data["result"]

    def _acquire_rate_limit(self):
        """
        Block until a send slot is available within the local rate limiter.

        Retries up to _RATE_LIMIT_RETRIES times, sleeping _RATE_LIMIT_WAIT
        seconds between attempts. Logs a warning if throttled.
        """

        for attempt in range(self._RATE_LIMIT_RETRIES):
            if self._rate_limiter.acquire():
                return
            logger.warning(
                f"Rate limiter throttled send attempt {attempt + 1}/"
                f"{self._RATE_LIMIT_RETRIES} for bot token ...{self._token[-6:]}"
            )
            time.sleep(self._RATE_LIMIT_WAIT)

        # Final attempt — if still throttled let it through and rely
        # on Telegram's 429 + task retry as the hard backstop.
        logger.warning("Rate limiter exhausted retries — proceeding anyway")

    def send_message(self, text: str, parse_mode: str = "Markdown") -> Dict:
        """
        Send a text message to the chat.

        Automatically splits long messages into multiple messages if text exceeds
        4096 characters (Telegram limit), sending each chunk separately.

        Args:
            text (str): Message text to send
                - Supports Markdown formatting when parse_mode="Markdown"
                - Plain text when parse_mode="HTML"
            parse_mode (str): Text formatting
                Default: "Markdown" (supports **bold**, *italic*, `code`, etc.)
                Options: "Markdown", "MarkdownV2", "HTML", None (plain text)

        Returns:
            dict: API response with sent message details
                - message_id: Unique message identifier
                - chat: Chat information
                - from: Bot info
                - date: Send timestamp
                - text: Message text sent

        Raises:
            TelegramError: If message sending fails

        Example - Simple message:
            >>> client.send_message("Hello World!")

        Example - Formatted message:
            >>> client.send_message(
            ...     "**Bold** and *italic* text",
            ...     parse_mode="Markdown"
            ... )

        Example - Long message (auto-split):
            >>> very_long_text = "x" * 10000
            >>> client.send_message(very_long_text)  # Sends 3 messages
        """

        result = {}
        chunks = split_message(text)

        for chunk in chunks:
            self._acquire_rate_limit()  # proactive check before each chunk

            result = self._post(
                "sendMessage",
                {
                    "chat_id": self.chat_id,
                    "text": chunk,
                    "parse_mode": parse_mode,
                },
            )

        return result

    def send_document(
        self, file_bytes: bytes, filename: str, caption: str = ""
    ) -> Dict:
        """
        Send a document (file) to the chat.

        Sends binary file content as a Telegram document.
        Used for reports, PDFs, etc.

        Args:
            file_bytes (bytes): File content as bytes
            filename (str): Filename to display (e.g., "report.pdf")
            caption (str): Optional caption text below the file

        Returns:
            dict: API response with sent document details
                - message_id: Unique message identifier
                - document: Document information (file_id, file_size, etc.)
                - caption: Caption text sent

        Raises:
            TelegramError: If file sending fails

        Example - Send PDF report:
            >>> pdf_bytes = generate_pdf(html_content)
            >>> client.send_document(
            ...     file_bytes=pdf_bytes,
            ...     filename="report-2024-04.pdf",
            ...     caption="📄 Your monthly report"
            ... )
        """

        self._acquire_rate_limit()  # proactive check before upload

        url = f"{self.base_url}/sendDocument"
        response = requests.post(
            url,
            data={"chat_id": self.chat_id, "caption": caption},
            files={"document": (filename, file_bytes, "application/octet-stream")},
        )

        # Rate Limit Handling
        if response.status_code == 429:
            data = response.json()
            retry_after = data.get("parameters", {}).get("retry_after", 1)
            raise TelegramRateLimitError(retry_after=retry_after)

        if response.status_code != 200:
            raise TelegramError(
                f"Telegram 'sendDocument' API error: Status code - {response.status_code}"
            )

        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Failed to send document: {data}")

        return data["result"]

    def send_typing_action(self) -> Dict:
        """
        Show 'typing...' indicator in chat.

        Sends a typing action that displays "Bot is typing..." to the user.
        Useful for indicating processing before sending a response.

        Returns:
            dict: API response (empty on success)

        Raises:
            TelegramError: If operation fails

        Example:
            >>> client.send_typing_action()  # Show typing indicator
            >>> # ... do some processing ...
            >>> client.send_message("Here's your response!")
        """

        return self._post(
            "sendChatAction", {"chat_id": self.chat_id, "action": "typing"}
        )

    def set_webhook(
        self, url: str, allowed_updates: Optional[List[str]] = None
    ) -> Dict:
        """
        Set a webhook for the bot to receive updates.

        Registers a webhook URL with Telegram, so updates are sent via POST requests
        to your server instead of polling.

        Args:
            url (str): HTTPS URL where Telegram will send updates
                Must be a valid HTTPS URL with a trusted certificate
                Example: "https://yourdomain.com/webhook/123456:ABC-DEF..."
            allowed_updates (list[str] | None): List of update types to receive
                Default: None (all update types)
                Common: ["message", "callback_query", "edited_message"]

        Returns:
            dict: API response with success status
                - ok: true on success
                - result: boolean indicating webhook was set

        Raises:
            TelegramError: If webhook registration fails

        Example - Set webhook for all updates:
            >>> client.set_webhook("https://example.com/webhook/bot123")

        Example - Set webhook for messages only:
            >>> client.set_webhook(
            ...     "https://example.com/webhook/bot123",
            ...     allowed_updates=["message"]
            ... )

        Note:
            - URL must use HTTPS (except for localhost testing)
            - Telegram will verify SSL certificate
            - Use delete_webhook() to remove webhook and switch back to polling
        """

        payload = {
            "url": url,
        }

        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates

        return self._post("setWebhook", payload)

    def get_updates(self, offset: int = 0, timeout: int = 20) -> List[Dict]:
        """
        Get pending updates from Telegram.

        Long polls Telegram for incoming messages and events.
        Used for webhook-style polling (alternative to webhook URL).

        Args:
            offset (int): Update ID to start from
                Increment this after processing updates to avoid re-receiving
                Default: 0 (gets oldest unprocessed updates)
            timeout (int): Long poll timeout in seconds
                Keeps connection open up to this duration waiting for updates
                Default: 20 seconds

        Returns:
            list[dict]: List of updates, each containing:
                - update_id: Unique update identifier
                - message: Message object (if available)
                - other updates...

        Raises:
            TelegramError: If API call fails

        Example - Polling loop:
            >>> offset = 0
            >>> while True:
            ...     updates = client.get_updates(offset=offset, timeout=20)
            ...     for update in updates:
            ...         process_update(update)
            ...         offset = update["update_id"] + 1

        Note:
            - Only "message" updates are requested (allowed_updates=["message"])
            - Returns empty list if no updates within timeout period
            - In production, typically use Telegram webhook instead of polling
        """

        result = self._post(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message"],
            },
        )

        return result or []
