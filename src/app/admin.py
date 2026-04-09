from django.contrib import admin

from app.models import (
    Ollama,
    App,
    MCPServer,
    Message,
    AppRunLog
)
from app.forms import OllamaForm, AppForm


@admin.register(Ollama)
class OllamaAdmin(admin.ModelAdmin):
    list_display = ("default_model",)
    form = OllamaForm

    def has_add_permission(self, request):
        return Ollama.objects.count() == 0


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    form = AppForm
    list_display = ("name", "created_by", "is_active")
    search_fields = ("name", "created_by__email", "created_by__first_name")
    list_filter = ("is_active", "sms_enabled", "email_enabled")

    readonly_fields = ("created_by", "created_at", "next_run_at", "last_run_at")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(created_by_id=request.user.id).select_related("created_by")

    def get_fieldsets(self, request, obj = ...):
        return (
            ("General Information", {"fields": ("created_by", "name", "description", "ollama_model", "is_active", "created_at")}),
            ("Scheduling", {"fields": ("interval_mins", "cron_expressions", "next_run_at", "last_run_at")}),
            ("System Prompt", {"fields": ("system_prompt",)}),
            ("Communication", {"fields": ("sms_enabled", "email_enabled")})
        )
    
    def save_model(self, request, obj, form, change):
        obj.created_by = request.user
        return super().save_model(request, obj, form, change)



@admin.register(MCPServer)
class MCPServerAdmin(admin.ModelAdmin):
    list_display = ("app", "name", "is_active", "created_at")
    list_filter = ("is_active", "transport")
    autocomplete_fields = ("app",)
    readonly_fields = ("created_at",)
    search_fields = ("name", "app__name")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(app__created_by_id=request.user.id).select_related("app")

    def get_fieldsets(self, request, obj = ...):
        return (
            (None, {"fields": (
                "app",
                "name",
                "transport",
                "command",
                "args",
                "secrets",
                "is_active",
                "created_at"
            )}),
        )


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("app", "role", "channel", "status", "is_report_request")
    list_filter = ("is_report_request",)
    search_fields = ("app__name",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(app__created_by_id=request.user.id).select_related("app")

    def get_fieldsets(self, request, obj = ...):
        return (
            (None, {"filds": (
                "app",
                "role",
                "content",
                "channel",
                "status",
                "is_report_request",
                "created_at"
            )}),
        )

    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj = ...):
        return False
    
    def has_delete_permission(self, request, obj = ...):
        return False


@admin.register(AppRunLog)
class AppRunLogAdmin(admin.ModelAdmin):
    list_display = ("app", "is_success")
    list_filter = ("is_success",)
    search_fields = ("app__name",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(app__created_by_id=request.user.id).select_related("app")

    def get_fieldsets(self, request, obj = ...):
        return (
            (None, {"fields": (
                "app",
                "finished_at",
                "is_success",
                "error",
                "messages_sent",
                "created_at"
            )}),
        )
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj = ...):
        return False
    
    def has_delete_permission(self, request, obj = ...):
        return False
