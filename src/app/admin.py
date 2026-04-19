from django.contrib import admin
from django.utils.html import format_html

from app.models import (
    Ollama,
    Bot,
    MCPServer,
    Message,
    Log
)
from app.forms import OllamaForm, BotForm
from app.utils import calculate_next_run_at, get_admin_link


@admin.register(Ollama)
class OllamaAdmin(admin.ModelAdmin):
    list_display = ("default_model",)
    form = OllamaForm

    def has_add_permission(self, request):
        return Ollama.objects.count() == 0


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    form = BotForm
    list_display = ("name", "created_by", "is_active")
    search_fields = ("name", "created_by__email", "created_by__first_name")
    list_filter = ("is_active",)
    fieldsets = (
        ("General Information", {"fields": (
            "created_by",
            "name",
            "description",
            "ollama_model",
            "is_active",
            "created_at",
            "last_execution_status",
            "get_stats",
        )}),
        ("Scheduling", {"fields": ("interval_mins", "cron_expression", "next_run_at", "last_run_at")}),
        ("System Prompt", {"fields": ("system_prompt",)}),
        ("Communication", {"fields": ("telegram_bot_token",)})
    )

    readonly_fields = (
        "created_by",
        "created_at",
        "next_run_at",
        "last_run_at",
        "telegram_bot_token",
        "get_stats",
        "last_execution_status"
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            created_by_id=request.user.id
        ).select_related("created_by").prefetch_related(
            "mcp_servers",
            "messages",
            "bot_logs"
        )

    @admin.display(description="Stats")
    def get_stats(self, obj=None):
        convo_msgs = reports = active = inactive = successes = errors = 0

        if obj:
            convo_msgs = obj.messages.filter(is_report_request=False).count()
            reports = obj.messages.filter(is_report_request=True).count()

            active = obj.mcp_servers.filter(is_active=True).count()
            inactive = obj.mcp_servers.filter(is_active=False).count()

            successes = obj.bot_logs.filter(is_success=True).count()
            errors = obj.bot_logs.filter(is_success=False).count()

        result = f"""
            <table cellpadding="10" cellspacing="10" style="text-align: center; border: 2px solid #ccc;">
                <thead>
                    <tr>
                        <th colspan="2">Messages</th>
                        <th colspan="2">MCP Servers</th>
                        <th colspan="2">Logs</th>
                    </tr>
                    <tr>
                        <th>Coversations</th>
                        <th>Reports</th>
                        <th>Active</th>
                        <th>Inactive</th>
                        <th>Successes</th>
                        <th>Errors</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>{get_admin_link("message", convo_msgs, "bot_id", obj)}</td>
                        <td>{get_admin_link("message", reports, "bot_id", obj)}</td>
                        <td>{get_admin_link("mcpserver", active, "bot_id", obj)}</td>
                        <td>{get_admin_link("mcpserver", inactive, "bot_id", obj)}</td>
                        <td>{get_admin_link("log", successes, "bot_id", obj)}</td>
                        <td>{get_admin_link("log", errors, "bot_id", obj)}</td>
                    </tr>
                </tbody>
            </table>
        """

        return format_html(result)
    
    @admin.display(description="Last Execution Status")
    def last_execution_status(self, obj=None):
        if not obj:
            return "-"
        
        last_log = obj.bot_logs.order_by("-created_at").first()
        if not last_log:
            return "-"
        
        if last_log.is_success:
            return format_html("<img src='/static/admin/img/icon-yes.svg' alt='True'>")

        return format_html(f"<a href='/admin/app/log/{str(last_log.id)}' target='_blank'><img src='/static/admin/img/icon-no.svg' alt='False'> View More</a>")

    def save_model(self, request, obj, form, change):
        # Associate current user with the object
        obj.created_by = request.user

        # Calculate the next run time based on the scheduling fields
        obj.next_run_at = calculate_next_run_at(obj.interval_mins, obj.cron_expression)

        return super().save_model(request, obj, form, change)


@admin.register(MCPServer)
class MCPServerAdmin(admin.ModelAdmin):
    list_display = ("bot", "name", "is_active", "created_at")
    list_filter = ("is_active", "transport")
    autocomplete_fields = ("bot",)
    readonly_fields = ("created_at",)
    search_fields = ("name", "bot__name")
    fieldsets = (
        (None, {"fields": (
            "bot",
            "name",
            "transport",
            "command",
            "endpoint",
            "args",
            "secrets",
            "is_active",
            "created_at"
        )}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(bot__created_by_id=request.user.id).select_related("bot")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("bot", "role", "channel", "status", "is_report_request")
    list_filter = ("is_report_request",)
    search_fields = ("bot__name",)
    fieldsets = (
        (None, {"fields": (
            "bot",
            "role",
            "content",
            "channel",
            "status",
            "is_report_request",
            "created_at"
        )}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(bot__created_by_id=request.user.id).select_related("bot")

    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj = ...):
        return False
    
    def has_delete_permission(self, request, obj = ...):
        return False


@admin.register(Log)
class LogAdmin(admin.ModelAdmin):
    list_display = ("bot", "is_success")
    list_filter = ("is_success",)
    search_fields = ("bot__name",)
    fieldsets = (
        (None, {"fields": (
            "bot",
            "finished_at",
            "is_success",
            "error",
            "messages_sent",
            "created_at"
        )}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(bot__created_by_id=request.user.id).select_related("bot")
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj = ...):
        return False
    
    def has_delete_permission(self, request, obj = ...):
        return False
