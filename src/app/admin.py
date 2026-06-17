from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count, Q, Subquery, OuterRef

from app.choices import MessageRole
from app.forms import BotForm, MCPServerForm
from app.models import Bot, CronJob, Log, MCPServer, Message, Ollama
from app.tasks import manage_conversation_summary, setup_bot_webhook
from utils.formatting import get_admin_link

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

    def has_add_permission(self, request):
        """Allow adding only if no Ollama instance exists."""

        return Ollama.objects.count() == 0

    def save_model(self, request, obj, form, change):
        if change and "num_ctx" in form.changed_data:
            # Trigger summary regeneration for all active bots.
            # No bot_id = task fetches all active bots internally.
            manage_conversation_summary.apply_async(queue="default", countdown=10)

        super().save_model(request, obj, form, change)


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
                    "is_active",
                    "created_at",
                    "updated_at",
                    "last_execution_status",
                    "get_stats",
                )
            },
        ),
        (
            "Model Configuration",
            {
                "fields": (
                    "ollama_model",
                    "embedding_model",
                    "embedding_dimensions",
                    "observed_patterns",
                    "system_prompt",
                )
            },
        ),
        ("Communication", {"fields": ("telegram_bot_token", "telegram_chat_id")}),
    )

    readonly_fields = (
        "created_by",
        "created_at",
        "updated_at",
        "telegram_chat_id",
        "get_stats",
        "last_execution_status",
        "observed_patterns",
    )

    def get_queryset(self, request):
        """Optimize queryset with select_related and prefetch_related for performance."""

        return (
            super()
            .get_queryset(request)
            .filter(created_by_id=request.user.id)
            .select_related("created_by")
            .prefetch_related("bot_logs")
            .annotate(
                # Stats
                messages_count=Count("messages"),
                mcp_active_count=Count(
                    "mcp_servers", filter=Q(mcp_servers__is_active=True)
                ),
                mcp_inactive_count=Count(
                    "mcp_servers", filter=Q(mcp_servers__is_active=False)
                ),
                cron_active_count=Count(
                    "bot_cron_jobs", filter=Q(bot_cron_jobs__is_active=True)
                ),
                cron_inactive_count=Count(
                    "bot_cron_jobs", filter=Q(bot_cron_jobs__is_active=False)
                ),
                logs_success_count=Count(
                    "bot_logs", filter=Q(bot_logs__is_success=True)
                ),
                logs_error_count=Count(
                    "bot_logs", filter=Q(bot_logs__is_success=False)
                ),
                # Latest log data
                last_log_id=Subquery(
                    Log.objects.filter(bot=OuterRef("id")).values("id")[:1]
                ),
                last_log_success=Subquery(
                    Log.objects.filter(bot=OuterRef("id")).values("is_success")[:1]
                ),
            )
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

        # Messages
        msgs_count = getattr(obj, "messages_count", 0)

        # MCP Servers
        mcps_active = getattr(obj, "mcp_active_count", 0)
        mcps_inactive = getattr(obj, "mcp_inactive_count", 0)

        # Cron Jobs
        active_crons = getattr(obj, "cron_active_count", 0)
        inactive_crons = getattr(obj, "cron_inactive_count", 0)

        # Logs
        successes = getattr(obj, "logs_success_count", 0)
        errors = getattr(obj, "logs_error_count", 0)

        return f"""
            <table cellpadding="10" cellspacing="10" style="text-align: center; border: 2px solid #ccc;">
                <thead>
                    <tr>
                        <th>Messages</th>
                        <th colspan="2">Cron Jobs</th>
                        <th colspan="2">MCP Servers</th>
                        <th colspan="2">Logs</th>
                    </tr>
                    <tr>
                        <th></th>
                        <th>Active</th>
                        <th>Inactive</th>
                        <th>Active</th>
                        <th>Inactive</th>
                        <th>Successes</th>
                        <th>Errors</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>{get_admin_link("message", msgs_count, obj)}</td>
                        <td>{get_admin_link("cronjob", active_crons, obj)}</td>
                        <td>{get_admin_link("cronjob", inactive_crons, obj)}</td>
                        <td>{get_admin_link("mcpserver", mcps_active, obj)}</td>
                        <td>{get_admin_link("mcpserver", mcps_inactive, obj)}</td>
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

        if not obj or not hasattr(obj, "last_log_id") or not obj.last_log_id:
            return "-"

        if obj.last_log_success:
            return format_html(
                "<img src='/static/admin/img/icon-yes.svg' alt='Success'>"
            )

        return format_html(
            f"<a href='/admin/app/log/{obj.last_log_id}' target='_blank'>"
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

    form = MCPServerForm
    list_display = ("bot", "name", "is_active", "transport", "created_at")
    list_filter = ("is_active", "transport", "created_at")
    autocomplete_fields = ("bot",)
    readonly_fields = ("created_at", "updated_at")
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

    list_display = ("bot", "role", "get_sender", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("bot__name", "content")
    fields = (
        "bot",
        "role",
        "get_sender",
        "content",
        "created_at",
    )

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
    readonly_fields = (
        "bot",
        "cron_expression",
        "next_run_at",
        "last_run_at",
        "created_at",
        "updated_at",
    )
    fields = (
        "bot",
        "name",
        "description",
        "cron_expression",
        "next_run_at",
        "last_run_at",
        "is_active",
        "created_at",
        "updated_at",
    )

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return True


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
