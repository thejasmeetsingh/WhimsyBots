from django.contrib import admin
from django.urls import path, include


admin.AdminSite.site_header = "WhimsyBots"
admin.AdminSite.site_title = "WhimsyBots"


urlpatterns = [
    path("admin/", admin.site.urls),
    path("martor/", include("martor.urls"))
]
