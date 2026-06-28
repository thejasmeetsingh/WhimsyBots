"""Tests for `src/app/choices.py` (Tier 1 — pure logic)."""

from __future__ import annotations

import pytest

from app.choices import MCPTransportType, MessageRole


# ──────────────────────────────────────────────
# BaseChoices.get_values
# ──────────────────────────────────────────────


def test_message_role_get_values_returns_all_value_tuples():
    # `get_values` returns the full `(code, label)` tuples — Django's
    # choices field can consume either bare codes or (code, label) tuples,
    # and the implementation uses the latter for richer admin display.
    assert MessageRole.get_values() == (
        ("S", "System"),
        ("U", "User"),
        ("A", "Assistant"),
    )


def test_mcp_transport_type_get_values_returns_all_value_tuples():
    assert MCPTransportType.get_values() == (
        ("R", "Remote"),
        ("L", "Local"),
    )


def test_get_values_returns_tuple_type():
    # Django's choices field expects an iterable; verify the tuple shape.
    assert isinstance(MessageRole.get_values(), tuple)
    assert isinstance(MCPTransportType.get_values(), tuple)


def test_get_values_returns_three_entries_for_message_role():
    assert len(MessageRole.get_values()) == 3


def test_get_values_returns_two_entries_for_mcp_transport_type():
    assert len(MCPTransportType.get_values()) == 2


# ──────────────────────────────────────────────
# BaseChoices.get_readable
# ──────────────────────────────────────────────


def test_message_role_get_readable_resolves_user():
    assert MessageRole.get_readable("U") == "User"


def test_message_role_get_readable_resolves_assistant():
    assert MessageRole.get_readable("A") == "Assistant"


def test_message_role_get_readable_resolves_system():
    assert MessageRole.get_readable("S") == "System"


def test_mcp_transport_type_get_readable_resolves_local():
    assert MCPTransportType.get_readable("L") == "Local"


def test_mcp_transport_type_get_readable_resolves_remote():
    assert MCPTransportType.get_readable("R") == "Remote"


def test_get_readable_unknown_code_raises_value_error():
    # Anything not in the enum must raise — never silently return junk.
    with pytest.raises(ValueError):
        MessageRole.get_readable("Z")


def test_get_readable_empty_code_raises_value_error():
    with pytest.raises(ValueError):
        MessageRole.get_readable("")


def test_get_readable_uses_first_element_of_value_tuple():
    # Sanity-check that the first element of each value tuple is what
    # get_readable matches against — guards against accidental reordering.
    assert MCPTransportType.LOCAL.value[0] == "L"
    assert MCPTransportType.REMOTE.value[0] == "R"
