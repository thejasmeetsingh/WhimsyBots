from croniter import croniter
from django.core.exceptions import ValidationError

from app.choices import MCPTransportType


def validate_cron_expression(value: str):
    if not value:
        raise ValidationError("cron expressions should not be empty.")

    if not croniter.is_valid(value):
        raise ValidationError("Invalid cron expression. Please provide a valid cron expression.")


def check_scheduling_fields(interval_val, cron_val):
    if interval_val is None and cron_val is None:
        raise ValidationError("Either interval or cron value should be provided.")

    elif interval_val is not None and cron_val is not None:
        raise ValidationError("Only one of interval or cron value can be provided.")


def validate_transport_fields(transport_type, cmd, endpoint):
    if transport_type == MCPTransportType.LOCAL.value[0] and not cmd:
        raise ValidationError("command should be provided for local transport type.")
    
    elif transport_type == MCPTransportType.REMOTE.value[0] and not endpoint:
        raise ValidationError("endpoint should be provided for remote transport type.")
