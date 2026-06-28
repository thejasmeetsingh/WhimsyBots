"""Long-text chunking utilities.

Telegram caps a single message at 4096 characters. 'split_message'
breaks a longer string into pieces while preserving the integrity of
Markdown code fences (```) so the rendered output still looks correct
on the receiving end.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def split_message(text: str, limit: int = 4096) -> List[str]:
    r"""Split 'text' into chunks of at most 'limit' characters.

    Attempts to split at the most natural boundary available, in this
    priority order:

        1. End of a closing code fence (``\\n\\`\\`\\```) — never break
           inside a code block.
        2. Paragraph boundary (double newline).
        3. Line boundary (single newline).
        4. Sentence boundary (``. ``, ``! ``, ``? ``).
        5. Word boundary (space).
        6. Hard cut (fallback — should rarely happen).

    If a split occurs mid-code-block, the next chunk is automatically
    prefixed with a re-opened code fence (preserving the language tag
    if present).

    Args:
        text: Text to split.
        limit: Maximum characters per chunk. Defaults to Telegram's 4096-character limit.

    Returns:
        A list of text chunks, each no longer than 'limit' characters.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    # Tracks the opening line of an unclosed code fence e.g. "```python"
    # so we can re-open it at the start of the next chunk if needed.
    open_fence: Optional[str] = None

    while text:
        # If we're inside a code block, reserve space for the re-opening fence
        # line and a closing fence at the end of this chunk.
        if open_fence:
            # "```python\n" + chunk content + "\n```"
            reserved = len(open_fence) + 1 + 4  # +1 for \n after tag, +4 for \n```
            effective_limit = limit - reserved
        else:
            effective_limit = limit

        if len(text) <= effective_limit:
            # Remaining text fits — close any open fence and we're done.
            chunk = f"{open_fence}\n{text}\n```" if open_fence else text
            chunks.append(chunk)
            break

        # --- Find the best split point within effective_limit ---
        window = text[:effective_limit]
        split_at = _find_split_point(window)

        chunk_text = text[:split_at].rstrip()
        text = text[split_at:].lstrip()

        # --- Code fence tracking ---
        # Check if this chunk contains an unclosed code fence.
        open_fence = _get_unclosed_fence(chunk_text, open_fence)

        # Wrap chunk with fence markers if we were inside a code block.
        if open_fence:
            # Close the fence at the end of this chunk so it renders properly,
            # then re-open it at the start of the next iteration.
            chunk = (
                f"{open_fence}\n{chunk_text}\n```"
                if _needs_fence_prefix(chunk_text, open_fence)
                else f"{chunk_text}\n```"
            )
        else:
            chunk = chunk_text

        chunks.append(chunk)

    return chunks


def _find_split_point(window: str) -> int:
    """Find the best character index to split at within 'window'.

    Scans backwards from the end of 'window' to find the
    highest-priority natural boundary. Falls back to a hard cut at
    'len(window)' if none found.

    Args:
        window: The text slice we are allowed to consume ('len <= limit').

    Returns:
        Index at which to split — exclusive, so 'text[:split_at]'
        is the chunk and 'text[split_at:]' is the remainder.
    """
    # Priority 1: closing code fence on its own line
    idx = window.rfind("\n```")
    if idx != -1:
        return idx + 4  # include the closing ```

    # Priority 2: paragraph boundary
    idx = window.rfind("\n\n")
    if idx != -1:
        return idx + 2

    # Priority 3: line boundary
    idx = window.rfind("\n")
    if idx != -1:
        return idx + 1

    # Priority 4: sentence boundary
    for terminator in (". ", "! ", "? "):
        idx = window.rfind(terminator)
        if idx != -1:
            return idx + len(terminator)

    # Priority 5: word boundary
    idx = window.rfind(" ")
    if idx != -1:
        return idx + 1

    # Priority 6: hard cut (no natural boundary found)
    return len(window)


def _get_unclosed_fence(chunk: str, currently_open: Optional[str]) -> Optional[str]:
    """Determine whether 'chunk' ends inside a Markdown code block.

    Args:
        chunk: The chunk text to scan.
        currently_open: Any fence that was already open before this chunk.

    Returns:
        The opening fence line (e.g. ``"```python"``) if a block is
        still open at the end of the chunk, otherwise None.
    """
    open_fence = currently_open

    for line in chunk.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if open_fence is None:
                # Opening a new fence — capture the whole line (may have a lang tag)
                open_fence = stripped
            else:
                # Closing the open fence
                open_fence = None

    return open_fence


def _needs_fence_prefix(chunk_text: str, open_fence: str) -> bool:
    """Check whether 'chunk_text' already starts with the fence opener.

    Prevents double-prefixing when the split happened exactly at a
    fence boundary.

    Args:
        chunk_text: The chunk content.
        open_fence: The fence opening line (e.g. ``"```python"``).

    Returns:
        True if the prefix should be added.
    """
    return not chunk_text.lstrip().startswith(open_fence)
