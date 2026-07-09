"""Tests for 'src/mcp_tools/web_search.py'.

The web search tool queries DuckDuckGo's HTML endpoint and extracts a
title/URL/snippet per result. 'fetch_and_extract' downloads a URL and
returns its main readable content as markdown.

We mock 'requests' to keep these tests network-free.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from mcp_tools import web_search
from strings import EMPTY_WEB_SEARCH, WEBPAGE_CONTENT_ERROR

# ──────────────────────────────────────────────
# unwrap_ddg_url
# ──────────────────────────────────────────────


def test_unwrap_ddg_url_passthrough_for_non_ddg_link():
    assert web_search.unwrap_ddg_url("https://example.com") == "https://example.com"


def test_unwrap_ddg_url_adds_https_for_protocol_relative():
    assert web_search.unwrap_ddg_url("//example.com") == "https://example.com"


def test_unwrap_ddg_url_extracts_uddg_from_redirect():
    encoded = "https%3A%2F%2Fexample.com%2Fpath%3Fa%3D1"
    href = f"https://duckduckgo.com/l/?uddg={encoded}"
    assert web_search.unwrap_ddg_url(href) == "https://example.com/path?a=1"


def test_unwrap_ddg_url_returns_original_when_no_uddg():
    """A DDG redirect without uddg falls back to the original href."""
    href = "https://duckduckgo.com/l/?something=else"
    assert web_search.unwrap_ddg_url(href) == href


# ──────────────────────────────────────────────
# web_search
# ──────────────────────────────────────────────


def _html_response(rows: list[dict]) -> MagicMock:
    """Build a fake requests.post() response carrying a DuckDuckGo HTML page."""
    from html import escape

    body_parts = []
    for i, row in enumerate(rows, 1):
        body_parts.append(
            f'<div class="result">'
            f'<h2 class="result__title">'
            f'<a href="{escape(row["href"])}">{escape(row["title"])}</a>'
            f"</h2>"
            f'<a class="result__snippet">{escape(row["snippet"])}</a>'
            f"</div>"
        )
    body = "<html><body>" + "".join(body_parts) + "</body></html>"

    resp = MagicMock(name="Response")
    resp.status_code = 200
    resp.text = body
    resp.raise_for_status = MagicMock()
    return resp


def test_web_search_returns_empty_marker_when_no_results():
    resp = _html_response([])
    with patch("mcp_tools.web_search.requests.post", return_value=resp):
        out = web_search.web_search("no results query")
    assert out == EMPTY_WEB_SEARCH.format(query="no results query")


def test_web_search_formats_results_with_title_url_snippet():
    resp = _html_response(
        [
            {
                "title": "An Article",
                "href": "https://example.com/article",
                "snippet": "An interesting summary",
            }
        ]
    )
    with patch("mcp_tools.web_search.requests.post", return_value=resp):
        out = web_search.web_search("test")

    assert "An Article" in out
    assert "https://example.com/article" in out
    assert "An interesting summary" in out
    # Result is numbered.
    assert "1." in out


def test_web_search_caps_results_at_max_search_results():
    rows = [
        {"title": f"T{i}", "href": f"https://example.com/{i}", "snippet": "s"} for i in range(25)
    ]
    resp = _html_response(rows)
    with patch("mcp_tools.web_search.requests.post", return_value=resp):
        out = web_search.web_search("bulk")
    # 20 results max (MAX_SEARCH_RESULTS).
    assert out.count("\n\n") == 19  # 20 entries joined by \n\n ⇒ 19 separators
    # No title beyond T19 should appear.
    assert "T19" in out
    assert "T20" not in out


def test_web_search_caps_max_results_parameter():
    """Callers can request fewer than the default MAX_SEARCH_RESULTS."""
    rows = [
        {"title": f"T{i}", "href": f"https://example.com/{i}", "snippet": "s"} for i in range(10)
    ]
    resp = _html_response(rows)
    with patch("mcp_tools.web_search.requests.post", return_value=resp):
        out = web_search.web_search("bulk", max_results=3)
    assert out.count("\n\n") == 2  # 3 entries
    assert "T2" in out
    assert "T3" not in out


def test_web_search_skips_results_without_title_or_href():
    """A 'result' div missing either the link or the title is skipped."""
    body = (
        "<html><body>"
        '<div class="result">'
        '<a class="result__snippet">no title here</a>'
        "</div>"
        '<div class="result">'
        '<h2 class="result__title"><a href="https://example.com/ok">OK</a></h2>'
        '<a class="result__snippet">good snippet</a>'
        "</div>"
        "</body></html>"
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.text = body
    resp.raise_for_status = MagicMock()

    with patch("mcp_tools.web_search.requests.post", return_value=resp):
        out = web_search.web_search("filter")

    assert "OK" in out
    assert "no title here" not in out


def test_web_search_uses_default_snippet_when_missing():
    body = (
        '<div class="result">'
        '<h2 class="result__title"><a href="https://example.com/x">X</a></h2>'
        "</div>"
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.text = f"<html><body>{body}</body></html>"
    resp.raise_for_status = MagicMock()

    with patch("mcp_tools.web_search.requests.post", return_value=resp):
        out = web_search.web_search("filter")
    assert "no snippet available" in out


def test_web_search_raises_for_status_on_http_error():
    resp = MagicMock()
    resp.status_code = 500
    resp.raise_for_status.side_effect = requests.HTTPError("server error")
    with patch("mcp_tools.web_search.requests.post", return_value=resp):
        with pytest.raises(requests.HTTPError):
            web_search.web_search("anything")


def test_web_search_unwraps_ddg_redirect_links():
    """When the title link is a DDG redirect, the real URL is unwrapped."""
    encoded = "https%3A%2F%2Fexample.com%2Freal"
    resp = _html_response(
        [
            {
                "title": "Target",
                "href": f"https://duckduckgo.com/l/?uddg={encoded}",
                "snippet": "snippet",
            }
        ]
    )
    with patch("mcp_tools.web_search.requests.post", return_value=resp):
        out = web_search.web_search("query")
    assert "https://example.com/real" in out
    assert "duckduckgo.com/l/" not in out


# ──────────────────────────────────────────────
# fetch_and_extract
# ──────────────────────────────────────────────


def test_fetch_and_extract_returns_error_message_on_http_error():
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.HTTPError("not found")
    with patch("mcp_tools.web_search.requests.get", return_value=resp):
        out = web_search.fetch_and_extract("https://example.com/missing")
    assert "Failed to fetch" in out
    assert "HTTPError" in out


def test_fetch_and_extract_returns_error_when_no_readable_content():
    """If 'trafilatura.extract' returns None/empty, the function returns
    the WEBPAGE_CONTENT_ERROR marker.
    """
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html><body>only ads</body></html>"
    resp.raise_for_status = MagicMock()

    with (
        patch("mcp_tools.web_search.requests.get", return_value=resp),
        patch("mcp_tools.web_search.trafilatura.extract", return_value=None),
    ):
        out = web_search.fetch_and_extract("https://example.com/article")

    assert out == WEBPAGE_CONTENT_ERROR.format(url="https://example.com/article")


def test_fetch_and_extract_returns_markdown_with_source_url():
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html><body><p>Hello</p></body></html>"
    resp.raise_for_status = MagicMock()

    with (
        patch("mcp_tools.web_search.requests.get", return_value=resp),
        patch(
            "mcp_tools.web_search.trafilatura.extract",
            return_value="Some readable text.",
        ),
    ):
        out = web_search.fetch_and_extract("https://example.com/article")

    assert "**Source:** https://example.com/article" in out
    assert "Some readable text." in out


def test_fetch_and_extract_truncates_long_content():
    long_text = "x" * 5000
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html><body>long</body></html>"
    resp.raise_for_status = MagicMock()

    with (
        patch("mcp_tools.web_search.requests.get", return_value=resp),
        patch(
            "mcp_tools.web_search.trafilatura.extract",
            return_value=long_text,
        ),
    ):
        out = web_search.fetch_and_extract("https://example.com/long")

    # Body is truncated to CONTENT_CHAR_LIMIT (3000).
    assert "content truncated" in out
    # And the URL header is preserved.
    assert "https://example.com/long" in out


def test_fetch_and_extract_does_not_truncate_short_content():
    short = "hello world"
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html><body>short</body></html>"
    resp.raise_for_status = MagicMock()

    with (
        patch("mcp_tools.web_search.requests.get", return_value=resp),
        patch(
            "mcp_tools.web_search.trafilatura.extract",
            return_value=short,
        ),
    ):
        out = web_search.fetch_and_extract("https://example.com/short")

    assert "content truncated" not in out
    assert short in out
