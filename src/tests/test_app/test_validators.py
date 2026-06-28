"""Tests for 'src/app/validators.py'"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from app.choices import MCPTransportType
from app.validators import (
    validate_cron_expression,
    validate_keep_alive,
    validate_transport_fields,
)

# ──────────────────────────────────────────────
# validate_cron_expression
# ──────────────────────────────────────────────


def test_validate_cron_expression_accepts_classic_daily_at_9():
    # Standard 5-field cron — must not raise.
    validate_cron_expression("0 9 * * *")


def test_validate_cron_expression_accepts_every_15_minutes():
    validate_cron_expression("*/15 * * * *")


def test_validate_cron_expression_accepts_every_4_hours():
    validate_cron_expression("0 */4 * * *")


def test_validate_cron_expression_accepts_midnight_sunday():
    validate_cron_expression("0 0 * * 0")


def test_validate_cron_expression_rejects_empty_string():
    with pytest.raises(ValidationError):
        validate_cron_expression("")


def test_validate_cron_expression_rejects_garbage():
    with pytest.raises(ValidationError):
        validate_cron_expression("not a cron expression")


def test_validate_cron_expression_rejects_incomplete_fields():
    # Only 4 fields — croniter rejects this.
    with pytest.raises(ValidationError):
        validate_cron_expression("0 9 * *")


# ──────────────────────────────────────────────
# validate_transport_fields
# ──────────────────────────────────────────────


def test_validate_transport_fields_local_with_command_passes():
    # LOCAL + command ⇒ OK.
    validate_transport_fields(
        transport_type=MCPTransportType.LOCAL.value[0],
        cmd="python",
        endpoint=None,
    )


def test_validate_transport_fields_local_without_command_raises():
    # LOCAL but no command ⇒ must raise.
    with pytest.raises(ValidationError):
        validate_transport_fields(
            transport_type=MCPTransportType.LOCAL.value[0],
            cmd="",
            endpoint=None,
        )


def test_validate_transport_fields_local_with_empty_string_command_raises():
    # An empty string is falsy — treated the same as missing.
    with pytest.raises(ValidationError):
        validate_transport_fields(
            transport_type=MCPTransportType.LOCAL.value[0],
            cmd="",
            endpoint="https://example.com",
        )


def test_validate_transport_fields_remote_with_endpoint_passes():
    validate_transport_fields(
        transport_type=MCPTransportType.REMOTE.value[0],
        cmd=None,
        endpoint="https://mcp.example.com/sse",
    )


def test_validate_transport_fields_remote_without_endpoint_raises():
    with pytest.raises(ValidationError):
        validate_transport_fields(
            transport_type=MCPTransportType.REMOTE.value[0],
            cmd=None,
            endpoint="",
        )


def test_validate_transport_fields_remote_with_command_does_not_raise():
    # The validator only checks required-field-per-transport. A REMOTE
    # server that also has a stray 'command' should not fail validation
    # (the form layer is responsible for cross-field checks elsewhere).
    validate_transport_fields(
        transport_type=MCPTransportType.REMOTE.value[0],
        cmd="ignored",
        endpoint="https://mcp.example.com",
    )


def test_validate_transport_fields_unknown_type_does_nothing():
    # An unknown transport type triggers neither branch ⇒ no-op success.
    validate_transport_fields(transport_type="X", cmd=None, endpoint=None)


# ──────────────────────────────────────────────
# validate_keep_alive
# ──────────────────────────────────────────────


@pytest.mark.parametrize("value", ["-1", "0", "30s", "10m", "1h", "9999s"])
def test_validate_keep_alive_accepts_supported_formats(value):
    # All four shapes (special values + durations) must validate cleanly.
    validate_keep_alive(value)


def test_validate_keep_alive_rejects_uppercase_unit():
    with pytest.raises(ValidationError):
        validate_keep_alive("10M")


def test_validate_keep_alive_rejects_bare_integer():
    # Bare "10" doesn't match the unit suffix — must be rejected.
    with pytest.raises(ValidationError):
        validate_keep_alive("10")


def test_validate_keep_alive_rejects_negative_duration():
    with pytest.raises(ValidationError):
        validate_keep_alive("-5m")


def test_validate_keep_alive_rejects_empty_string():
    with pytest.raises(ValidationError):
        validate_keep_alive("")


def test_validate_keep_alive_rejects_garbage():
    with pytest.raises(ValidationError):
        validate_keep_alive("forever")


def test_validate_keep_alive_strips_whitespace_before_check():
    # Leading/trailing whitespace should not break validation.
    validate_keep_alive("  10m  ")
