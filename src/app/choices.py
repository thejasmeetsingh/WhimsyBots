"""Enum-style choice classes used across the application.

Provides a 'BaseChoices' helper that turns small (code, label) tuples
into named enum members plus a couple of classmethod helpers used by
Django models (choices tuples, lookup-by-code).
"""

from enum import Enum


class BaseChoices(Enum):
    """Common base for all application enum-style choice classes."""

    @classmethod
    def get_values(cls):
        """Return the tuple of (code, label) values used by Django fields."""
        return tuple(x.value for x in cls)

    @classmethod
    def get_readable(cls, code: str) -> str:
        """Return the human-readable label for the given single-letter code.

        Args:
            code: Single-letter code as stored in the database.

        Returns:
            Human-readable label for the matching enum member.

        Raises:
            ValueError: If no enum member matches the given code.
        """
        for member in cls:
            if member.value[0] == code:
                return member.value[1]
        raise ValueError(f"Code '{code}' not found in {cls.__name__}")


class MCPTransportType(BaseChoices):
    """Transport modes supported by MCP server connections."""

    REMOTE = ("R", "Remote")
    LOCAL = ("L", "Local")


class MessageRole(BaseChoices):
    """Conversation roles stored on the 'Message' model."""

    SYSTEM = ("S", "System")
    USER = ("U", "User")
    ASSISTANT = ("A", "Assistant")
