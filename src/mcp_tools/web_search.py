"""Web search utilities for MCP tool usage.

This module provides DuckDuckGo web search integration and readable
webpage extraction for tool-enabled assistants.

Features:
- Search the web and format results with titles, URLs, and snippets
- Resolve DuckDuckGo redirect links to actual target URLs
- Fetch page content and extract readable markdown output
"""

from urllib.parse import parse_qs, unquote, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

from strings import EMPTY_WEB_SEARCH, WEBPAGE_CONTENT_ERROR

MAX_SEARCH_RESULTS = 20
CONTENT_CHAR_LIMIT = 3000
DEFAULT_TIMEOUT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    "Chrome/124.0 Safari/537.36"
}


def unwrap_ddg_url(href: str) -> str:
    """DDG's HTML results wrap real links in a redirect — pull the actual URL out."""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        query = parse_qs(urlparse(href).query)
        target = query.get("uddg", [None])[0]
        if target:
            return unquote(target)
    return href


def web_search(query: str, max_results: int = 20) -> str:
    """Search the web via DuckDuckGo.

    Args:
        query (str): Search query
        max_results (int): max results to fetch, Default is 20

    Returns:
        a list of results with title, URL, and a short snippet for each.

    Does NOT fetch full page content,
    Use 'fetch_and_extract' on a specific URL from these results if you need more detail.
    """
    max_results = min(max_results, MAX_SEARCH_RESULTS)

    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        timeout=DEFAULT_TIMEOUT,
        headers=HEADERS,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select(".result")[:max_results]

    if not rows:
        return EMPTY_WEB_SEARCH.format(query=query)

    formatted = []
    for i, row in enumerate(rows, 1):
        title_el = row.select_one(".result__title a")
        snippet_el = row.select_one(".result__snippet")
        if not title_el or not title_el.get("href"):
            continue

        title = title_el.get_text(strip=True)
        url = unwrap_ddg_url(title_el["href"])
        snippet = snippet_el.get_text(strip=True) if snippet_el else "(no snippet available)"

        formatted.append(f"{i}. **{title}**\n   URL: {url}\n   {snippet}")

    return "\n\n".join(formatted)


def fetch_and_extract(url: str) -> str:
    """Fetch a specific URL and extract its main readable content as markdown.

    Args:
        url (str): URL to extract content from

    Returns:
        Webpage contents in markdown format stripping navigation, ads, and boilerplate.

    Use this after 'web_search' when a result's snippet looks worth reading in full.
    """
    try:
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
        resp.raise_for_status()
    except Exception as e:
        return f"Failed to fetch {url}: {type(e).__name__}"

    text = trafilatura.extract(
        resp.text, include_comments=False, include_tables=False, output_format="markdown"
    )
    if not text:
        return WEBPAGE_CONTENT_ERROR.format(url=url)

    text = text.strip()
    truncated = len(text) > CONTENT_CHAR_LIMIT
    text = text[:CONTENT_CHAR_LIMIT]

    result = f"**Source:** {url}\n\n{text}"
    if truncated:
        result += "\n\n*[content truncated]*"
    return result
