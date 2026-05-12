"""
Database models for WhimsyBots application.

This module defines the core data models:
- Ollama: Language model configuration and settings
- Bot: Individual bot instances with scheduling and communication config
- MCPServer: Model Context Protocol servers integrated with bots
- Message: Conversation message history
- CronJob: Scheduled job definitions for bots
- Log: Execution logs for bot runs
"""

import uuid

from django.db import models
from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField
from django.core.validators import URLValidator
from martor.models import MartorField

from app.validators import (
    validate_cron_expression,
    validate_keep_alive,
    validate_transport_fields,
)
from app.choices import MCPTransportType, MessageIntentType, MessageRole


class BaseModel(models.Model):
    """Abstract base model for all application models."""

    id = models.UUIDField(
        default=uuid.uuid4, primary_key=True, unique=True, db_index=True, editable=False
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Ollama(BaseModel):
    """
    Configuration for the Ollama language model service.

    Stores connection details and model parameters for the Ollama instance.
    Only one instance should exist in the system (enforced via admin).
    """

    endpoint = models.URLField(
        default="http://localhost:11434",
        validators=[URLValidator(schemes=["http", "https"])],
        help_text="Ollama API endpoint URL",
    )
    default_model = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Default model name when not specified per-bot",
    )
    api_key = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Optional API key for Ollama authentication",
    )
    temperature = models.FloatField(
        default=0.7,
        help_text="Controls randomness in generation (0.0-1.0, higher = more random)",
    )
    num_ctx = models.PositiveIntegerField(
        default=4096, help_text="Context window size in tokens"
    )
    keep_alive = models.CharField(
        max_length=10,
        default="10m",
        validators=[validate_keep_alive],
        help_text="How long to keep model loaded between requests. "
        "Use -1 (never unload), 0 (unload immediately), "
        "or a duration like 30s, 10m, 1h.",
    )
    num_predict = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Max tokens to generate (leave it blank = infinite/default)",
    )

    class Meta:
        verbose_name = "Ollama"
        verbose_name_plural = "Ollama"

    def __str__(self):
        return f"Ollama ({self.endpoint})"


class Bot(BaseModel):
    """
    Bot configuration and state.

    Represents a single bot instance with its own scheduling, system prompt,
    and Telegram communication settings.
    """

    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField()
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(
        default=True, help_text="Whether this bot is active and should be scheduled"
    )

    # LLM Configuration
    ollama_model = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Specific Ollama model (overrides default from Ollama config)",
    )
    system_prompt = MartorField(
        null=True,
        blank=True,
        help_text="System prompt for LLM interactions (supports Markdown)",
    )

    # Telegram Communication
    telegram_bot_token = models.CharField(
        max_length=255, unique=True, help_text="Telegram bot API token from BotFather"
    )
    telegram_chat_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        help_text="Telegram chat ID (auto-populated on first message)",
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Bot"
        verbose_name_plural = "Bots"

    def __str__(self):
        return self.name


class MCPServer(BaseModel):
    """
    Model Context Protocol (MCP) server configuration.

    Represents an MCP server that can be used to provide tools/resources
    to a bot. Supports both local (command-based) and remote (HTTP) servers.

    Validation:
        - LOCAL transport requires command field
        - REMOTE transport requires endpoint field
    """

    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="mcp_servers")
    name = models.CharField(max_length=100)
    transport = models.CharField(max_length=1, choices=MCPTransportType.get_values())
    command = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        help_text="Command to run (python, npx, uv, etc.) - required for LOCAL transport",
    )
    endpoint = models.URLField(
        null=True,
        blank=True,
        validators=[URLValidator(schemes=["https"])],
        help_text="Remote MCP server HTTPS endpoint - required for REMOTE transport",
    )
    args = ArrayField(
        base_field=models.CharField(max_length=500),
        default=list,
        null=True,
        blank=True,
        help_text="Command arguments as comma-seperated list (e.g., -y, @modelcontextprotocol/server-memory)",
    )
    secrets = models.JSONField(
        default=dict,
        null=True,
        blank=True,
        help_text="Environment variables (LOCAL) or HTTP headers (REMOTE) as JSON dict",
    )
    is_active = models.BooleanField(
        default=True, help_text="Whether this MCP server is active and should be used"
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "MCP Server"
        verbose_name_plural = "MCP Servers"

    def clean(self):
        """Validate transport-specific fields."""

        validate_transport_fields(self.transport, self.command, self.endpoint)
        return super().clean()

    def __str__(self):
        return self.name


class Message(BaseModel):
    """
    Conversation message in bot interaction history.

    Stores individual messages from users and bot responses for
    conversation history, intent classification, and report generation.
    """

    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=1, choices=MessageRole.get_values())
    intent = models.CharField(
        max_length=2, choices=MessageIntentType.get_values(), null=True, blank=True
    )
    content = models.TextField()

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Message"
        verbose_name_plural = "Messages"

    def __str__(self):
        return f"{self.get_role_display()}: {self.content[:50]}"


class CronJob(BaseModel):
    """
    Scheduled job definition for bot execution.

    Represents a cron-based schedule for automated bot runs. Each job belongs
    to a single bot and defines when that bot should execute using standard
    cron expressions.

    Validation: cron_expression is validated via validate_cron_expression
    """

    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="bot_cron_jobs")
    name = models.CharField(max_length=100)
    description = models.TextField()
    cron_expression = models.CharField(
        max_length=100,
        help_text="Scheduling in standard cron format",
        validators=[validate_cron_expression],
    )
    next_run_at = models.DateTimeField(
        help_text="Calculated timestamp for next execution"
    )
    last_run_at = models.DateTimeField(
        null=True, blank=True, help_text="Timestamp of last execution"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)


class Log(BaseModel):
    """
    Execution log for bot runs.

    Records the success/failure status and details of each bot execution
    for monitoring, debugging, and audit purposes.
    """

    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="bot_logs")
    is_success = models.BooleanField(
        default=True, help_text="Whether the bot execution succeeded"
    )
    description = models.TextField(
        null=True, blank=True, help_text="Error message or execution details"
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Log"
        verbose_name_plural = "Logs"

    def __str__(self):
        status = "✓" if self.is_success else "✗"
        return f"{status} {self.bot.name} - {self.created_at}"
