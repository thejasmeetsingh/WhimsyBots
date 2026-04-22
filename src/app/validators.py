"""
Custom Django validators for WhimsyBots models.

This module provides field-level validation functions for:
- Cron expression syntax validation
- Bot scheduling configuration validation
- MCP server transport type validation
"""

from croniter import croniter
from django.core.exceptions import ValidationError

from app.choices import MCPTransportType


def validate_cron_expression(value: str) -> None:
    """
    Validate that a string is a valid cron expression.
    
    Ensures the cron expression follows standard cron syntax and can be
    parsed by croniter without errors.
    
    Args:
        value (str): Cron expression string to validate
        
    Raises:
        ValidationError: If expression is empty or invalid
        
    Valid examples:
        "0 9 * * *"           # 9 AM every day
        "0 0 * * 0"           # Midnight every Sunday
        "*/15 * * * *"        # Every 15 minutes
        "0 */4 * * *"         # Every 4 hours
        
    Note:
        This validator is called at the model level when saving a Bot.
        The field is required if cron scheduling is chosen over intervals.
    """

    if not value:
        raise ValidationError("Cron expression should not be empty.")

    if not croniter.is_valid(value):
        raise ValidationError(
            "Invalid cron expression. Please provide a valid cron expression "
            "(e.g., '0 9 * * *' for 9 AM daily)."
        )


def check_scheduling_fields(interval_val: int, cron_val: str) -> None:
    """
    Validate that bot scheduling is configured correctly.
    
    A bot must have EITHER interval-based OR cron-based scheduling,
    but NOT both or neither. This function ensures exactly one is provided.
    
    Args:
        interval_val (int | None): Interval in minutes
        cron_val (str | None): Cron expression string
        
    Raises:
        ValidationError: If neither or both scheduling options are provided
        
    Valid cases:
        - interval_val=30, cron_val=None  ✓ Interval-based scheduling
        - interval_val=None, cron_val="0 9 * * *"  ✓ Cron-based scheduling
        
    Invalid cases:
        - interval_val=None, cron_val=None  ✗ No scheduling
        - interval_val=30, cron_val="0 9 * * *"  ✗ Both provided
        
    Note:
        This validator is called in the Bot model's clean() method before saving.
    """

    if interval_val is None and cron_val is None:
        raise ValidationError(
            "Either interval or cron expression must be provided for scheduling."
        )

    elif interval_val is not None and cron_val is not None:
        raise ValidationError(
            "Only one of interval or cron expression can be provided. "
            "Choose either interval-based (every X minutes) or cron-based scheduling."
        )


def validate_transport_fields(transport_type: str, cmd: str, endpoint: str) -> None:
    """
    Validate that MCP server transport configuration is complete.
    
    Ensures that required fields are provided based on the transport type:
    - LOCAL transport must have a command
    - REMOTE transport must have an HTTPS endpoint
    
    Args:
        transport_type (str): Transport type identifier ('L' for local, 'R' for remote)
        cmd (str | None): Command to execute (required for LOCAL)
        endpoint (str | None): HTTPS URL (required for REMOTE)
        
    Raises:
        ValidationError: If required field is missing for the chosen transport
        
    Valid configurations:
        - LOCAL: {'transport': 'L', 'command': 'python', 'endpoint': None}  ✓
        - REMOTE: {'transport': 'R', 'command': None, 'endpoint': 'https://...'}  ✓
        
    Invalid configurations:
        - LOCAL: {'transport': 'L', 'command': None}  ✗ Missing command
        - REMOTE: {'transport': 'R', 'endpoint': None}  ✗ Missing endpoint
        - LOCAL: {'transport': 'L', 'command': 'python', 'endpoint': 'https://...'}  ✗ Conflicting
        
    Note:
        This validator is called in the MCPServer model's clean() method before saving.
        The transport type is a required choice field with predefined values.
    """

    if transport_type == MCPTransportType.LOCAL.value[0] and not cmd:
        raise ValidationError(
            "Command must be provided for LOCAL transport type. "
            "Specify the executable (e.g., 'python', 'npx', 'uv')."
        )

    elif transport_type == MCPTransportType.REMOTE.value[0] and not endpoint:
        raise ValidationError(
            "HTTPS endpoint must be provided for REMOTE transport type. "
            "Specify the URL of the remote MCP server."
        )

