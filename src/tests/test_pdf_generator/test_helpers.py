"""Tests for `src/pdf_generator/helpers.py` (Tier 6 — pure logic + light IO).

We focus on:
- `extract_html` — 4-step regex extraction logic
- `parse_uuid` — dual-return contract
- `decrypt_token` — round-trip + missing SECRET_KEY
- `generate_pdf` — happy path passes bytes through
- `send_document` — Telegram sendDocument wiring
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from pdf_generator.helpers import (
    decrypt_token,
    extract_html,
    generate_pdf,
    parse_uuid,
    send_document,
)


# ──────────────────────────────────────────────
# extract_html
# ──────────────────────────────────────────────


HTML_SAMPLE = "<html><body><h1>Hello</h1></body></html>"


def test_extract_html_returns_none_for_empty():
    assert extract_html("") is None


def test_extract_html_returns_none_for_whitespace():
    assert extract_html("   \n\t  ") is None


def test_extract_html_returns_none_for_none():
    assert extract_html(None) is None


def test_extract_html_extracts_from_html_code_block():
    """Highest-priority: a ```html ... ``` block."""

    text = f"Here you go:\n\n```html\n{HTML_SAMPLE}\n```\n\nEnjoy!"
    assert extract_html(text) == HTML_SAMPLE


def test_extract_html_html_code_block_is_case_insensitive():
    text = f"```HTML\n{HTML_SAMPLE}\n```"
    assert extract_html(text) == HTML_SAMPLE


def test_extract_html_extracts_from_generic_code_block():
    """A ``` block containing HTML but no language tag is also accepted."""

    text = f"```\n{HTML_SAMPLE}\n```"
    out = extract_html(text)
    assert out is not None
    assert HTML_SAMPLE in out


def test_extract_html_extracts_raw_html_with_doctype():
    """Raw HTML (no code fences) starting with `<!DOCTYPE html>` is matched."""

    text = f"Sure, here is the report:\n\n{HTML_SAMPLE}"
    out = extract_html(text)
    assert out is not None
    assert HTML_SAMPLE in out


def test_extract_html_extracts_raw_html_with_html_tag():
    text = f"Here you go: {HTML_SAMPLE}  cheers!"
    out = extract_html(text)
    assert out is not None
    assert HTML_SAMPLE in out


def test_extract_html_partial_html_fallback_grabs_tagged_substring():
    """When there are HTML-looking tags but no full `<html>...</html>` block,
    grab from the first `<` to the last `>`."""

    text = "Intro text <b>bold</b> and <i>italic</i> end"
    out = extract_html(text)
    assert out is not None
    assert out.startswith("<")
    assert out.endswith(">")


def test_extract_html_returns_none_for_plain_text():
    """Plain text with no HTML tags must yield None."""

    assert extract_html("Just some text, no markup here.") is None


def test_extract_html_handles_multiline_html_in_code_block():
    """Multi-line HTML inside a code block should be preserved."""

    html = "<html>\n  <body>\n    <h1>Title</h1>\n    <p>Para</p>\n  </body>\n</html>"
    text = f"```html\n{html}\n```"
    assert extract_html(text) == html


def test_extract_html_prefers_explicit_html_block_over_partial():
    """When both an ```html block and partial HTML tags are present,
    the explicit code block wins."""

    partial = "<div>partial</div>"
    text = f"```html\n{HTML_SAMPLE}\n``` and also {partial}"
    out = extract_html(text)
    # The full HTML inside the code block wins.
    assert HTML_SAMPLE in out


# ──────────────────────────────────────────────
# parse_uuid
# ──────────────────────────────────────────────


def test_parse_uuid_returns_uuid_object_on_valid_input():
    valid = "12345678-1234-5678-1234-567812345678"
    result = parse_uuid(valid, "bot_id")
    assert isinstance(result, uuid.UUID)
    assert str(result) == valid


def test_parse_uuid_returns_error_string_on_invalid_input():
    bad = "not-a-uuid"
    result = parse_uuid(bad, "bot_id")
    assert isinstance(result, str)
    assert "## Error" in result
    assert bad in result
    assert "bot_id" in result


# ──────────────────────────────────────────────
# decrypt_token
# ──────────────────────────────────────────────


def _encrypt_with_secret(plaintext: str, secret: str) -> str:
    """Helper: Fernet-encrypt a plaintext using the same key derivation
    that `decrypt_token` uses."""

    key = hashlib.sha256(secret.encode()).digest()
    encoded_key = base64.urlsafe_b64encode(key)
    return Fernet(encoded_key).encrypt(plaintext.encode()).decode()


def test_decrypt_token_round_trip():
    """`decrypt_token` must recover the original plaintext from a Fernet
    ciphertext encrypted with the same SECRET_KEY."""

    secret = "shhh"
    ciphertext = _encrypt_with_secret("my-bot-token", secret)
    with patch.dict(os.environ, {"SECRET_KEY": secret}):
        assert decrypt_token(ciphertext) == "my-bot-token"


def test_decrypt_token_raises_when_secret_key_missing(monkeypatch):
    """`decrypt_token` requires the `SECRET_KEY` env var to be set."""

    monkeypatch.delenv("SECRET_KEY", raising=False)
    ciphertext = _encrypt_with_secret("x", "anything")
    with pytest.raises(EnvironmentError):
        decrypt_token(ciphertext)


def test_decrypt_token_raises_on_wrong_secret_key():
    """Wrong SECRET_KEY ⇒ Fernet fails to decrypt."""

    ciphertext = _encrypt_with_secret("x", "secret-a")
    with patch.dict(os.environ, {"SECRET_KEY": "secret-b"}):
        with pytest.raises(Exception):
            decrypt_token(ciphertext)


# ──────────────────────────────────────────────
# generate_pdf
# ──────────────────────────────────────────────


def test_generate_pdf_returns_bytes():
    """`generate_pdf` must return whatever bytes WeasyPrint writes.

    On systems without the native WeasyPrint dependencies, the
    `conftest.py` stubs the `weasyprint` module. We patch
    `pdf_generator.helpers.HTML` to a deterministic fake that writes
    recognizable bytes — preserving the contract under test without
    requiring native libs.
    """

    fake_pdf_bytes = b"%PDF-1.4\n%fake\n"

    with patch("pdf_generator.helpers.HTML") as HTMLCls:
        # `HTML(string=...)` returns an object whose `write_pdf(target=...)`
        # writes to the supplied BytesIO.
        instance = MagicMock()
        HTMLCls.return_value = instance

        def fake_write_pdf(target=None):
            target.write(fake_pdf_bytes)

        instance.write_pdf.side_effect = fake_write_pdf

        out = generate_pdf("<html></html>")

    assert isinstance(out, bytes)
    assert out == fake_pdf_bytes
    # BytesIO is seeked back to 0 before reading — we must have consumed
    # from position 0.
    HTMLCls.assert_called_once()


def test_generate_pdf_propagates_weasyprint_errors():
    """If weasyprint raises, `generate_pdf` re-raises."""

    with patch("pdf_generator.helpers.HTML") as HTMLCls:
        HTMLCls.return_value.write_pdf.side_effect = RuntimeError("weasyprint boom")
        with pytest.raises(RuntimeError):
            generate_pdf("<html></html>")


# ──────────────────────────────────────────────
# send_document
# ──────────────────────────────────────────────


def test_send_document_posts_to_telegram_and_returns_result():
    """`send_document` must POST the file bytes and return the `result` payload."""

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "ok": True,
        "result": {"message_id": 1, "document": {"file_id": "abc"}},
    }

    with patch(
        "pdf_generator.helpers.requests.post", return_value=fake_response
    ) as post:
        result = send_document(
            chat_id="999",
            token="TOK",
            file_bytes=b"%PDF-1.4 fake pdf",
        )

    assert result == {"message_id": 1, "document": {"file_id": "abc"}}
    # URL must be the Telegram sendDocument endpoint.
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://api.telegram.org/botTOK/sendDocument"
    # File must be uploaded under the `document` key.
    assert "document" in kwargs["files"]
    filename, content, mime = kwargs["files"]["document"]
    assert filename.startswith("report-999-")
    assert filename.endswith(".pdf")
    assert content == b"%PDF-1.4 fake pdf"
    assert mime == "application/octet-stream"
    # chat_id and caption passed as data.
    assert kwargs["data"]["chat_id"] == "999"
    assert "report" in kwargs["data"]["caption"].lower()


def test_send_document_raises_on_non_200_status():
    fake_response = MagicMock()
    fake_response.status_code = 500

    with patch("pdf_generator.helpers.requests.post", return_value=fake_response):
        with pytest.raises(Exception) as exc_info:
            send_document(chat_id="999", token="TOK", file_bytes=b"x")
    assert "500" in str(exc_info.value)


def test_send_document_raises_when_telegram_returns_not_ok():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"ok": False, "description": "Bad Request"}

    with patch("pdf_generator.helpers.requests.post", return_value=fake_response):
        with pytest.raises(Exception) as exc_info:
            send_document(chat_id="999", token="TOK", file_bytes=b"x")
    assert "Bad Request" in str(exc_info.value)
