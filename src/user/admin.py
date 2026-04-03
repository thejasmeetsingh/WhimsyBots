from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group

from user.models import User


admin.site.unregister(Group)

@admin.register(User)
class UserAdmin(UserAdmin):
    list_display = ("email", "first_name", "last_name")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)

    readonly_fields = ("date_joined", "last_login")

    def get_fieldsets(self, request, obj = ...):
        return (
            ("User Information", {
                "fields": ("email", "password", "first_name", "last_name", "mobile_number")
            }),
            ("Permissions", {
                "fields": ("is_active", "is_staff", "is_superuser")
            }),
            ("Important Dates", {
                "fields": ("date_joined", "last_login")
            }),
        )
