from django.contrib import admin
from django.utils.html import format_html

from app.choices import MessageRole
from app.models import CronJob, Ollama, Bot, MCPServer, Message, Log
from app.forms import OllamaForm, BotForm
from app.tasks import setup_bot_webhook
from app.utils import get_admin_link


admin.AdminSite.site_header = "WhimsyBots"
admin.AdminSite.site_title = "WhimsyBots"


# Base Admin Classes
class BaseUserFilteredAdmin(admin.ModelAdmin):
    """
    Base admin class for models filtered by the current user.
    Automatically filters related objects to show only those owned by the requesting user.
    """

    list_per_page = 20
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        """Filter queryset to show only objects created by the current user."""

        qs = super().get_queryset(request)
        return qs.filter(bot__created_by_id=request.user.id).select_related(
            "bot__created_by"
        )


class BaseReadOnlyUserFilteredAdmin(BaseUserFilteredAdmin):
    """
    Base admin class for read-only models filtered by the current user.
    Inherits user filtering and adds read-only permissions.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Ollama)
class OllamaAdmin(admin.ModelAdmin):
    """
    Admin interface for Ollama configuration.
    Limited to a single instance to prevent configuration conflicts.
    """

    list_display = ("default_model",)
    form = OllamaForm

    def has_add_permission(self, request):
        """Allow adding only if no Ollama instance exists."""

        return Ollama.objects.count() == 0


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    """
    Admin interface for Bot management.
    Displays bot statistics, execution status, scheduling, and communication settings.
    Each user only sees bots they created.
    """

    form = BotForm
    list_display = ("name", "created_by", "is_active", "last_execution_status")
    search_fields = ("name", "created_by__username", "created_by__first_name")
    list_filter = ("is_active", "created_at")
    list_per_page = 20

    fieldsets = (
        (
            "General Information",
            {
                "fields": (
                    "created_by",
                    "name",
                    "description",
                    "ollama_model",
                    "is_active",
                    "created_at",
                    "updated_at",
                    "last_execution_status",
                    "get_stats",
                )
            },
        ),
        ("System Prompt", {"fields": ("system_prompt",)}),
        ("Communication", {"fields": ("telegram_bot_token", "telegram_chat_id")}),
    )

    readonly_fields = (
        "created_by",
        "created_at",
        "updated_at",
        "telegram_chat_id",
        "get_stats",
        "last_execution_status",
    )

    def get_queryset(self, request):
        """Optimize queryset with select_related and prefetch_related for performance."""

        return (
            super()
            .get_queryset(request)
            .filter(created_by_id=request.user.id)
            .select_related("created_by")
        )

    def _render_stats_table(self, obj):
        """
        Render the statistics table HTML for a bot.

        Args:
            obj: Bot instance

        Returns:
            HTML-formatted statistics table showing messages, MCP servers, and logs.
        """

        if not obj:
            return "-"

        msgs_count = obj.messages.count()
        active = obj.mcp_servers.filter(is_active=True).count()
        inactive = obj.mcp_servers.filter(is_active=False).count()
        successes = obj.bot_logs.filter(is_success=True).count()
        errors = obj.bot_logs.filter(is_success=False).count()

        return f"""
            <table cellpadding="10" cellspacing="10" style="text-align: center; border: 2px solid #ccc;">
                <thead>
                    <tr>
                        <th>Messages</th>
                        <th colspan="2">MCP Servers</th>
                        <th colspan="2">Logs</th>
                    </tr>
                    <tr>
                        <th></th>
                        <th>Active</th>
                        <th>Inactive</th>
                        <th>Successes</th>
                        <th>Errors</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>{get_admin_link("message", msgs_count, obj)}</td>
                        <td>{get_admin_link("mcpserver", active, obj)}</td>
                        <td>{get_admin_link("mcpserver", inactive, obj)}</td>
                        <td>{get_admin_link("log", successes, obj)}</td>
                        <td>{get_admin_link("log", errors, obj)}</td>
                    </tr>
                </tbody>
            </table>
        """

    @admin.display(description="Stats")
    def get_stats(self, obj=None):
        """Display formatted stats table for the bot."""

        return format_html(self._render_stats_table(obj))

    @admin.display(description="Last Execution Status")
    def last_execution_status(self, obj=None):
        """Display the last execution status with appropriate icon and link."""

        if not obj:
            return "-"

        last_log = obj.bot_logs.first()
        if not last_log:
            return "-"

        if last_log.is_success:
            return format_html(
                "<img src='/static/admin/img/icon-yes.svg' alt='Success'>"
            )

        return format_html(
            f"<a href='/admin/app/log/{str(last_log.id)}' target='_blank'>"
            f"<img src='/static/admin/img/icon-no.svg' alt='Failed'> View More</a>"
        )

    def save_model(self, request, obj, form, change):
        """
        Set the current user as the bot creator, calculate next run time,
        and set up Telegram webhook when bot is created or token is changed.
        """

        # Associate current user with bot object
        obj.created_by = request.user

        # Save the object first to get the ID and persist changes
        super().save_model(request, obj, form, change)

        # Set up webhook when:
        # 1. Creating a new bot (change=False) and telegram_bot_token is provided
        # 2. Updating existing bot and telegram_bot_token has changed
        should_setup_webhook = False

        if not change and obj.telegram_bot_token:
            # New bot being created with a token
            should_setup_webhook = True
        elif change and obj.telegram_bot_token:
            # Existing bot - check if token changed
            if "telegram_bot_token" in form.changed_data:
                should_setup_webhook = True
        print("should_setup_webhook: ", should_setup_webhook)
        if should_setup_webhook:
            # Queue webhook setup task
            setup_bot_webhook.apply_async(
                queue="default", countdown=10, kwargs={"bot_id": str(obj.id)}
            )


@admin.register(MCPServer)
class MCPServerAdmin(BaseUserFilteredAdmin):
    """
    Admin interface for MCP Server configuration.
    Allows creation and management of Model Context Protocol servers per bot.
    """

    list_display = ("bot", "name", "is_active", "transport", "created_at")
    list_filter = ("is_active", "transport", "created_at")
    autocomplete_fields = ("bot",)
    readonly_fields = ("created_at",)
    search_fields = ("name", "bot__name")
    fields = (
        "bot",
        "name",
        "transport",
        "command",
        "endpoint",
        "args",
        "secrets",
        "is_active",
        "created_at",
        "updated_at",
    )


@admin.register(Message)
class MessageAdmin(BaseReadOnlyUserFilteredAdmin):
    """
    Admin interface for viewing bot messages.
    Messages are read-only and cannot be created, modified, or deleted via admin.
    """

    list_display = ("bot", "role", "get_sender", "intent", "created_at")
    list_filter = ("role", "intent", "created_at")
    search_fields = ("bot__name", "content")
    fields = ("bot", "role", "get_sender", "content", "intent", "created_at")

    @admin.display(description="Sender")
    def get_sender(self, obj=None):
        """Display message sender name"""

        if not obj:
            return "-"

        if obj.role == MessageRole.USER.value[0]:
            return obj.bot.created_by.username
        return obj.bot.ollama_model


@admin.register(CronJob)
class CronJobAdmin(BaseReadOnlyUserFilteredAdmin):
    """
    Admin interface for viewing cron job schedules.
    Cron jobs are read-only and cannot be created, modified, or deleted via admin.
    Displays scheduling information including cron expressions and execution timestamps.
    """

    list_display = ("bot", "cron_expression", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("bot__name",)
    fields = (
        "bot",
        "name",
        "cron_expression",
        "next_run_at",
        "last_run_at",
        "is_active",
        "created_at",
        "updated_at",
    )


@admin.register(Log)
class LogAdmin(BaseReadOnlyUserFilteredAdmin):
    """
    Admin interface for viewing bot execution logs.
    Logs are read-only and cannot be created, modified, or deleted via admin.
    """

    list_display = ("bot", "is_success", "created_at")
    list_filter = ("is_success", "created_at")
    search_fields = ("bot__name", "description")
    fields = ("bot", "is_success", "description", "created_at")
