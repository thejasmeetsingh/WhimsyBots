import uuid

from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.core.validators import URLValidator, MinValueValidator
from martor.models import MartorField

from user.models import User
from app.validators import validate_cron_expression, check_scheduling_fields, validate_transport_fields
from app.choices import MCPTransportType, MessageChannel, MessageRole, MessageStatus


class BaseModel(models.Model):
    id = models.UUIDField(default=uuid.uuid4, primary_key=True, unique=True, db_index=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
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


class App(BaseModel):
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
    sms_enabled = models.BooleanField(default=True, verbose_name="SMS enabled",
                                      help_text="Main interface for communication")
    email_enabled = models.BooleanField(default=False, help_text="For sending reports only")

    class Meta:
        verbose_name = "App"
        verbose_name_plural = "Apps"

    def clean(self):
        check_scheduling_fields(self.interval_mins, self.cron_expression)
        return super().clean()

    def __str__(self):
        return self.name


class MCPServer(BaseModel):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="mcp_servers")
    name = models.CharField(max_length=100)
    transport = models.CharField(max_length=1, choices=MCPTransportType.get_values())
    command = models.CharField(max_length=10, null=True, blank=True, help_text="Command to run (python, npx, uv)")
    endpoint = models.URLField(null=True, blank=True, validators=[URLValidator(schemes=["https"])], help_text="Remote MCP server endpoint URL")
    args = ArrayField(base_field=models.CharField(max_length=500), default=list, null=True, blank=True,
                      help_text="Command arguments as array (e.g., ['-y', '@modelcontextprotocol/server-memory'])")
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
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(choices=MessageRole.get_values())
    content = MartorField()
    channel = models.CharField(choices=MessageChannel.get_values())
    status = models.CharField(choices=MessageStatus.get_values(), default=MessageStatus.PENDING.value[0])
    is_report_request = models.BooleanField(default=False)

    def __str__(self):
        return self.role + ": " + self.content[:50]


class AppLog(BaseModel):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="app_logs")
    finished_at = models.DateTimeField(null=True)
    is_success = models.BooleanField(default=True)
    error = models.TextField(null=True, blank=True)
    messages_sent = models.PositiveBigIntegerField(default=0)

    class Meta:
        verbose_name = "Log"
        verbose_name_plural = "Logs"

    def __str__(self):
        return self.app.name + ": " + self.created_at
