import logging
import requests

from app.utils import split_message


logger = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class TelegramClient:
    base_url = None
    chat_id = None

    def __init__(self, token, chat_id: str | None = None):
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self.base_url}/{endpoint}"

        response = requests.post(url=url, json=payload)
        if response.status_code != 200:
            raise TelegramError(f"Telegram API error: Status code - {response.status_code}")
        
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram API error on {endpoint}: {data}")
        
        return data["result"]

    def send_message(self, text: str, parse_mode: str = "Markdown") -> dict:
        result = {}
        chunks = split_message(text)

        for chunk in chunks:
            result = self._post("sendMessage", {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
            })
        
        return result

    def send_document(self, file_bytes: bytes, filename: str, caption: str = "") -> dict:
        url = f"{self.base_url}/sendDocument"

        response = requests.post(
            url,
            data={"chat_id": self.chat_id, "caption": caption},
            files={"document": (filename, file_bytes, "application/octet-stream")}
        )

        if response.status_code != 200:
            raise TelegramError(f"Telegram 'sendDocument' API error: Status code - {response.status_code}")
        
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Failed to send document: {data}")

        return data["result"]

    def send_typing_action(self) -> dict:
        return self._post("sendChatAction", {
            "chat_id": self.chat_id,
            "action": "typing"
        })

    def get_updates(self, offset: int = 0, timeout: int = 20) -> list:
        result = self._post("getUpdates", {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message"],
        })

        return result or []
