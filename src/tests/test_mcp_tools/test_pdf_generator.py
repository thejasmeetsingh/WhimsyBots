"""Tests for 'src/mcp_tools/pdf_generator.py'.

The PDF generator tool wraps HTML-to-PDF conversion and Telegram
delivery. We mock 'utils.pdf' to avoid touching WeasyPrint and the
'TelegramClient' to avoid the network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp_tools import pdf_generator
from strings import PDF_GENERATION_SEND_SUCCESS


def _tg():
    return MagicMock(name="TelegramClient")


# ──────────────────────────────────────────────
# generate_and_send_report
# ──────────────────────────────────────────────


def test_returns_raw_contents_when_no_html_detected():
    """If 'extract_html' cannot find any HTML markup in the supplied
    contents, the function returns the original contents untouched.
    """
    client = _tg()
    with (
        patch("mcp_tools.pdf_generator.extract_html", return_value=None),
        patch("mcp_tools.pdf_generator.generate_pdf") as gen_pdf,
    ):
        out = pdf_generator.generate_and_send_report(client=client, contents="just plain text")
    assert out == "just plain text"
    gen_pdf.assert_not_called()
    client.send_document.assert_not_called()


def test_sends_pdf_when_html_detected():
    """Happy path — HTML is detected, PDF bytes are generated, and the
    Telegram client receives a 'send_document' call.
    """
    client = _tg()
    with (
        patch("mcp_tools.pdf_generator.extract_html", return_value="<html></html>"),
        patch(
            "mcp_tools.pdf_generator.generate_pdf",
            return_value=b"%PDF-1.4\n%fake\n",
        ) as gen_pdf,
    ):
        out = pdf_generator.generate_and_send_report(
            client=client, contents="```html\n<html></html>\n```"
        )

    assert out == PDF_GENERATION_SEND_SUCCESS
    gen_pdf.assert_called_once_with("<html></html>")
    client.send_document.assert_called_once_with(file_bytes=b"%PDF-1.4\n%fake\n")


def test_returns_error_string_when_telegram_send_fails():
    """A Telegram send error is caught and returned as a Markdown string."""
    client = _tg()
    client.send_document.side_effect = RuntimeError("telegram unreachable")
    with (
        patch("mcp_tools.pdf_generator.extract_html", return_value="<html></html>"),
        patch(
            "mcp_tools.pdf_generator.generate_pdf",
            return_value=b"%PDF-1.4\n",
        ),
    ):
        out = pdf_generator.generate_and_send_report(client=client, contents="<html></html>")

    assert "Error caught while sending PDF" in out
    assert "telegram unreachable" in out


def test_propagates_pdf_generation_errors():
    """If PDF generation itself raises, the exception bubbles up — there
    is no recovery at this layer.
    """
    client = _tg()
    with (
        patch("mcp_tools.pdf_generator.extract_html", return_value="<html></html>"),
        patch(
            "mcp_tools.pdf_generator.generate_pdf",
            side_effect=RuntimeError("weasyprint boom"),
        ),
    ):
        with pytest.raises(RuntimeError):
            pdf_generator.generate_and_send_report(client=client, contents="<html></html>")
    client.send_document.assert_not_called()
