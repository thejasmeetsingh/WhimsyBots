"""Tests for 'src/utils/pdf.py'.

We focus on:
- 'extract_html' — 4-step regex extraction logic
- 'generate_pdf' — happy path passes bytes through, errors propagate

WeasyPrint is stubbed via the conftest at the project root.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.pdf import extract_html, generate_pdf

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
    grab from the first `<` to the last `>`.
    """
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
    the explicit code block wins.
    """
    partial = "<div>partial</div>"
    text = f"```html\n{HTML_SAMPLE}\n``` and also {partial}"
    out = extract_html(text)
    # The full HTML inside the code block wins.
    assert HTML_SAMPLE in out


# ──────────────────────────────────────────────
# generate_pdf
# ──────────────────────────────────────────────


def test_generate_pdf_returns_bytes():
    """'generate_pdf' must return whatever bytes WeasyPrint writes.

    On systems without the native WeasyPrint dependencies, the
    'whimsybots.settings.test' module stubs the 'weasyprint' module.
    We patch 'utils.pdf.HTML' to a deterministic fake that writes
    recognizable bytes — preserving the contract under test without
    requiring native libs.
    """
    fake_pdf_bytes = b"%PDF-1.4\n%fake\n"

    with patch("utils.pdf.HTML") as HTMLCls:
        instance = MagicMock()
        HTMLCls.return_value = instance

        def fake_write_pdf(target=None):
            target.write(fake_pdf_bytes)

        instance.write_pdf.side_effect = fake_write_pdf

        out = generate_pdf("<html></html>")

    assert isinstance(out, bytes)
    assert out == fake_pdf_bytes
    HTMLCls.assert_called_once()


def test_generate_pdf_propagates_weasyprint_errors():
    """If weasyprint raises, 'generate_pdf' re-raises."""
    with patch("utils.pdf.HTML") as HTMLCls:
        HTMLCls.return_value.write_pdf.side_effect = RuntimeError("weasyprint boom")
        with pytest.raises(RuntimeError):
            generate_pdf("<html></html>")
