"""Tests for `src/utils/text.py` (Tier 1 — pure logic).

split_message is the highest-value single function in the utils
layer: it is what makes Telegram's 4096-character limit invisible to
every bot response. The test suite below covers the six-priority split
strategy and the code-fence preservation logic.
"""

from __future__ import annotations


from utils.text import (
    _find_split_point,
    _get_unclosed_fence,
    _needs_fence_prefix,
    split_message,
)


# ──────────────────────────────────────────────
# split_message — basic shape
# ──────────────────────────────────────────────


def test_split_message_returns_single_chunk_when_under_limit():
    text = "hello world"
    assert split_message(text) == [text]


def test_split_message_returns_single_chunk_exactly_at_limit():
    text = "x" * 4096
    chunks = split_message(text)
    assert chunks == [text]


def test_split_message_chunks_all_under_limit():
    text = ("line " * 2000).strip()  # ~10000 chars
    chunks = split_message(text, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert len(chunks) > 1


# ──────────────────────────────────────────────
# split_message — split priority order
# ──────────────────────────────────────────────


def test_split_prefers_paragraph_boundary_over_line_boundary():
    # Single newlines appear before double newlines — the algorithm
    # should prefer the paragraph break.
    text = "para one.\npara two.\n\n" + ("x" * 4100)
    chunks = split_message(text, limit=100)

    # The first chunk should end at the paragraph boundary, not the line.
    first = chunks[0]
    assert first.endswith("para two.")


def test_split_prefers_line_boundary_over_sentence_boundary():
    text = "first sentence. " + ("more " * 30).strip() + "\n" + ("x" * 100)
    chunks = split_message(text, limit=60)
    # The first chunk must end at the newline, not mid-sentence.
    assert not chunks[0].endswith("more")


def test_split_prefers_sentence_boundary_over_word_boundary():
    text = "alpha. beta gamma delta epsilon " * 20
    chunks = split_message(text, limit=80)
    # "alpha. " ends a sentence — the splitter should pick that over
    # the trailing space inside the window.
    for c in chunks[:-1]:
        # Each (non-final) chunk should terminate cleanly with a sentence
        # end rather than splitting a multi-letter word across chunks.
        assert not c.rstrip().endswith((" gamma", " delta", " epsilon"))


def test_split_falls_back_to_word_boundary():
    # No sentence terminators in the window → fall back to a space.
    text = ("abcdef " * 1500).strip()  # ~10497 chars, no ".!?"
    chunks = split_message(text, limit=500)
    assert all(len(c) <= 500 for c in chunks)


def test_split_hard_cut_when_no_natural_boundary():
    # A single long token with no whitespace at all forces the
    # priority-6 hard cut. Every chunk must still respect the limit.
    text = "x" * 10_000
    chunks = split_message(text, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text  # nothing dropped


# ──────────────────────────────────────────────
# split_message — code fence preservation
# ──────────────────────────────────────────────


def test_split_preserves_complete_code_block():
    # A fence that *fits* entirely in one chunk should be passed through
    # untouched (the fence-opener + closing-backticks stay together).
    inner = "print('hi')\n" * 50
    text = f"```python\n{inner}```"
    chunks = split_message(text, limit=4096)
    assert chunks == [text]


def test_split_reopens_code_block_across_chunks_without_lang_tag():
    # Bare ``` (no language) is re-opened in the next chunk to keep
    # the rendered block contiguous on the Telegram side.
    body = "print('line')\n" * 800  # well over the 4096 limit
    text = f"```\n{body}```"

    chunks = split_message(text, limit=4096)
    assert len(chunks) >= 2

    # First chunk must close the block that was opened at the top.
    assert chunks[0].rstrip().endswith("```")

    # Every subsequent chunk (except possibly the last) must reopen
    # the fence so the rendered output stays contiguous.
    for chunk in chunks[1:]:
        assert chunk.lstrip().startswith("```")


def test_split_reopens_code_block_preserving_language_tag():
    # When the opening fence carried a language tag (```python), the
    # re-opener in subsequent chunks must keep that tag.
    body = "print('line')\n" * 800
    text = f"```python\n{body}```"

    chunks = split_message(text, limit=4096)
    assert len(chunks) >= 2

    # First chunk closes the block.
    assert chunks[0].rstrip().endswith("```")

    # Subsequent chunks reopen with the SAME language tag.
    for chunk in chunks[1:]:
        assert chunk.lstrip().startswith("```python")


def test_split_reopens_code_block_preserving_full_lang_spec():
    # Tags like ```python py=3.11 should be preserved verbatim.
    body = "x = 1\n" * 800
    text = f"```python py=3.11\n{body}```"

    chunks = split_message(text, limit=4096)
    assert len(chunks) >= 2
    for chunk in chunks[1:]:
        assert chunk.lstrip().startswith("```python py=3.11")


def test_split_final_chunk_closes_open_fence():
    # If we exit the while-loop with an open fence, the final chunk
    # must include the closing ``` so the block renders correctly.
    body = "x = 1\n" * 1000  # ~6000 chars ⇒ multiple chunks
    text = f"```python\n{body}"  # no closing fence — never gets one

    chunks = split_message(text, limit=4096)
    assert len(chunks) >= 2
    assert chunks[-1].rstrip().endswith("```")


# ──────────────────────────────────────────────
# _find_split_point — direct coverage of priority order
# ──────────────────────────────────────────────


def test_find_split_point_prefers_closing_fence():
    window = "abc\n```def"
    assert _find_split_point(window) == window.index("\n```") + 4


def test_find_split_point_prefers_paragraph_boundary():
    window = "abc\n\ndef\nghi"  # paragraph before line
    assert _find_split_point(window) == window.index("\n\n") + 2


def test_find_split_point_prefers_line_boundary():
    window = "abc\ndef"  # only a single newline available
    assert _find_split_point(window) == window.index("\n") + 1


def test_find_split_point_prefers_sentence_terminators():
    window = "first. second"
    # ". " is the first match — must beat the space-only fallback.
    assert _find_split_point(window) == window.index(". ") + 2


def test_find_split_point_falls_back_to_word_boundary():
    window = "noseparators here"
    assert _find_split_point(window) == window.rfind(" ") + 1


def test_find_split_point_hard_cuts_when_no_boundary():
    window = "nospacesatall"
    assert _find_split_point(window) == len(window)


# ──────────────────────────────────────────────
# _get_unclosed_fence — direct coverage
# ──────────────────────────────────────────────


def test_get_unclosed_fence_returns_none_when_no_fence():
    assert _get_unclosed_fence("just plain text", None) is None


def test_get_unclosed_fence_detects_open_block():
    chunk = "```python\nprint('hi')\nprint('there')"
    assert _get_unclosed_fence(chunk, None) == "```python"


def test_get_unclosed_fence_clears_after_close():
    chunk = "```python\nprint('hi')\n```"
    assert _get_unclosed_fence(chunk, None) is None


def test_get_unclosed_fence_tracks_already_open_block():
    # If we entered the chunk with an open fence and the chunk never
    # closes it, the open fence is preserved.
    chunk = "more code lines\nstill inside"
    assert _get_unclosed_fence(chunk, "```python") == "```python"


def test_get_unclosed_fence_closes_block_within_chunk():
    chunk = "more code\n```\nplain text"
    # Was open at the start, closes inside the chunk ⇒ None at end.
    assert _get_unclosed_fence(chunk, "```python") is None


# ──────────────────────────────────────────────
# _needs_fence_prefix — direct coverage
# ──────────────────────────────────────────────


def test_needs_fence_prefix_true_for_plain_chunk():
    assert _needs_fence_prefix("print('hi')", "```python") is True


def test_needs_fence_prefix_false_when_chunk_already_starts_with_fence():
    # Avoid double-prefixing when split happens exactly at a fence boundary.
    assert _needs_fence_prefix("```python\nbody", "```python") is False


def test_needs_fence_prefix_tolerates_leading_whitespace():
    # Leading whitespace shouldn't trick the prefix check.
    assert _needs_fence_prefix("   ```python\nbody", "```python") is False
