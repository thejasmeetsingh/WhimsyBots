"""Tests for `src/pdf_generator/server.py` (Tier 6 — MCP tool implementations).

We test the single `@mcp.tool()`-decorated async function
`generate_and_send_report`. The orchestration is:

1. Parse `bot_id` as UUID (returns error string if bad).
2. Extract HTML from the contents (returns raw contents if no HTML).
3. Generate PDF bytes from the HTML.
4. Fetch bot credentials from DB.
5. Decrypt the token.
6. Send the PDF to Telegram.

We mock `extract_html`, `generate_pdf`, `decrypt_token`, `send_document`,
and the DB session.
"""

from __future__ import annotations

import base64
import hashlib
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


BOT_ID = "11111111-1111-1111-1111-111111111111"
FAKE_PDF = b"%PDF-1.4 fake"


def _encrypt(plaintext: str, secret: str) -> str:
    key = hashlib.sha256(secret.encode()).digest()
    encoded_key = base64.urlsafe_b64encode(key)
    return Fernet(encoded_key).encrypt(plaintext.encode()).decode()


def _patch_session_with_row(*, row):
    """Patch `pdf_generator.server.get_session` to yield a fake session
    whose `execute(...).fetchone()` returns `row`."""

    session = MagicMock(name="AsyncSession")
    session.execute = AsyncMock(
        return_value=MagicMock(fetchone=MagicMock(return_value=row))
    )
    session.close = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        try:
            yield session
        finally:
            await session.close()

    return patch("pdf_generator.server.get_session", _ctx), session


# ──────────────────────────────────────────────
# generate_and_send_report
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_and_send_report_invalid_bot_uuid_returns_error():
    from pdf_generator.server import generate_and_send_report

    out = await generate_and_send_report("bad-uuid", "anything")
    assert "## Error" in out
    assert "bot_id" in out


@pytest.mark.asyncio
async def test_generate_and_send_report_no_html_returns_raw_contents():
    """If `extract_html` cannot find HTML in the contents, the function
    returns the original contents (unchanged) without sending anything."""

    from pdf_generator.server import generate_and_send_report

    contents = "I don't have any HTML here, sorry."
    with (
        patch("pdf_generator.server.extract_html", return_value=None),
        patch("pdf_generator.server.generate_pdf") as gen_pdf,
        patch("pdf_generator.server.send_document") as send,
    ):
        out = await generate_and_send_report(BOT_ID, contents)

    assert out == contents
    gen_pdf.assert_not_called()
    send.assert_not_called()


@pytest.mark.asyncio
async def test_generate_and_send_report_bot_not_found_returns_uuid_string():
    """If the DB row lookup yields `None`, the function returns the
    stringified UUID without crashing."""

    from pdf_generator.server import generate_and_send_report

    contents = "```html\n<html></html>\n```"

    ctx, _ = _patch_session_with_row(row=None)
    with (
        ctx,
        patch("pdf_generator.server.extract_html", return_value="<html></html>"),
        patch("pdf_generator.server.generate_pdf", return_value=FAKE_PDF),
        patch("pdf_generator.server.decrypt_token") as decrypt,
        patch("pdf_generator.server.send_document") as send,
    ):
        out = await generate_and_send_report(BOT_ID, contents)

    # The function returns the bot_uuid stringified (uuid.UUID.__str__).
    assert out == BOT_ID
    decrypt.assert_not_called()
    send.assert_not_called()


@pytest.mark.asyncio
async def test_generate_and_send_report_happy_path_sends_pdf():
    """Full happy-path: HTML extracted → PDF generated → token decrypted →
    document sent."""

    from pdf_generator.server import generate_and_send_report

    secret = "shhh"
    encrypted_token = _encrypt("BOT-TOKEN", secret)

    contents = "```html\n<html><body>report</body></html>\n```"
    html = "<html><body>report</body></html>"

    row = MagicMock(name="Row")
    row._asdict.return_value = {
        "id": BOT_ID,
        "telegram_bot_token": encrypted_token,
        "telegram_chat_id": "999",
    }

    ctx, _ = _patch_session_with_row(row=row)

    with (
        ctx,
        patch.dict(os.environ, {"SECRET_KEY": secret}),
        patch("pdf_generator.server.extract_html", return_value=html),
        patch("pdf_generator.server.generate_pdf", return_value=FAKE_PDF) as gen_pdf,
        patch(
            "pdf_generator.server.decrypt_token", return_value="BOT-TOKEN"
        ) as decrypt,
        patch("pdf_generator.server.send_document") as send,
    ):
        out = await generate_and_send_report(BOT_ID, contents)

    assert "PDF generated and sent" in out
    gen_pdf.assert_called_once_with(html)
    decrypt.assert_called_once_with(encrypted_token)
    send.assert_called_once_with(chat_id="999", token="BOT-TOKEN", file_bytes=FAKE_PDF)


@pytest.mark.asyncio
async def test_generate_and_send_report_logs_and_returns_error_on_exception():
    """A generic exception during processing is caught, logged, and
    returned as an error string (does not raise)."""

    from pdf_generator.server import generate_and_send_report

    contents = "```html\n<html></html>\n```"

    session = MagicMock(name="AsyncSession")
    session.execute = AsyncMock(side_effect=RuntimeError("db boom"))
    session.close = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        try:
            yield session
        finally:
            await session.close()

    with (
        patch("pdf_generator.server.get_session", _ctx),
        patch("pdf_generator.server.extract_html", return_value="<html></html>"),
        patch("pdf_generator.server.generate_pdf", return_value=FAKE_PDF),
    ):
        out = await generate_and_send_report(BOT_ID, contents)

    assert "Error caught" in out
    assert "db boom" in out


@pytest.mark.asyncio
async def test_generate_and_send_report_does_not_send_when_pdf_fails():
    """If PDF generation raises, no document is sent and the error
    propagates as a caught string."""

    from pdf_generator.server import generate_and_send_report

    contents = "```html\n<html></html>\n```"

    with (
        patch("pdf_generator.server.extract_html", return_value="<html></html>"),
        patch(
            "pdf_generator.server.generate_pdf",
            side_effect=RuntimeError("pdf boom"),
        ),
        patch("pdf_generator.server.send_document") as send,
    ):
        with pytest.raises(RuntimeError):
            await generate_and_send_report(BOT_ID, contents)
    send.assert_not_called()
