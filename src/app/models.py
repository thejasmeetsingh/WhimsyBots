import uuid

from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.core.validators import URLValidator, MinValueValidator
from martor.models import MartorField

from user.models import User
from app.validators import validate_cron_expression, check_scheduling_fields, validate_transport_fields
from app.choices import MCPTransportType, MessageIntentType, MessageRole


class BaseModel(models.Model):
    id = models.UUIDField(default=uuid.uuid4, primary_key=True, unique=True, db_index=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        abstract = True


class Ollama(BaseModel):
    endpoint = models.URLField(default="http://localhost:11434", validators=[URLValidator(schemes=["http", "https"])])
    default_model = models.CharField(max_length=50, null=True, blank=True)
    api_key = models.CharField(max_length=100, null=True, blank=True)
    temperature = models.FloatField(default=0.7, help_text="Controls randomness in generation (higher = more random)")
    num_ctx = models.PositiveIntegerField(default=4096, help_text="Context length size (number of tokens)")
    num_predict = models.IntegerField(default=-1, help_text="Maximum number of tokens to generate (-1 = infinite)")

    class Meta:
        verbose_name = "Ollama"
        verbose_name_plural = "Ollama"


class Bot(BaseModel):
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # Scheduling
    interval_mins = models.PositiveIntegerField(null=True, blank=True, help_text="Runs every X minutes",
                                                validators=[MinValueValidator(5)])
    cron_expression = models.CharField(max_length=100, null=True, blank=True, help_text="Scheduling in cron format",
                                       validators=[validate_cron_expression])
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)

    # LLM Config
    ollama_model = models.CharField(max_length=50, null=True, blank=True)
    system_prompt = MartorField(null=True, blank=True)

    # Communication
    telegram_bot_token = models.CharField(max_length=255, unique=True)
    telegram_chat_id = models.CharField(max_length=255, unique=True, null=True)

    class Meta:
        verbose_name = "Bot"
        verbose_name_plural = "Bots"

    def clean(self):
        check_scheduling_fields(self.interval_mins, self.cron_expression)
        return super().clean()

    def __str__(self):
        return self.name


class MCPServer(BaseModel):
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="mcp_servers")
    name = models.CharField(max_length=100)
    transport = models.CharField(max_length=1, choices=MCPTransportType.get_values())
    command = models.CharField(max_length=10, null=True, blank=True, help_text="Command to run (python, npx, uv)")
    endpoint = models.URLField(null=True, blank=True, validators=[URLValidator(schemes=["https"])],
                               help_text="Remote MCP server endpoint URL")
    args = ArrayField(base_field=models.CharField(max_length=500), default=list, null=True, blank=True,
                      help_text="Command arguments as comma-seperated string (e.g: -y, @modelcontextprotocol/server-memory")
    secrets = models.JSONField(default=dict, null=True, blank=True,
                               help_text="Environment variables for local MCP server or HTTP headers for remote")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "MCP Server"
        verbose_name_plural = "MCP Servers"
    
    def clean(self):
        validate_transport_fields(self.transport, self.command, self.endpoint)
        return super().clean()

    def __str__(self):
        return self.name


class Message(BaseModel):
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(choices=MessageRole.get_values())
    intent = models.CharField(choices=MessageIntentType.get_values(), null=True)
    content = MartorField()

    def __str__(self):
        return self.role + ": " + self.content[:50]


class Log(BaseModel):
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="bot_logs")
    is_success = models.BooleanField(default=True)
    description = models.TextField(null=True, blank=True)

    class Meta:
        verbose_name = "Log"
        verbose_name_plural = "Logs"

    def __str__(self):
        return self.bot.name + ": " + self.created_at
