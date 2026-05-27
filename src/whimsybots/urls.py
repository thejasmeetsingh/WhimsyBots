from django.contrib import admin
from django.urls import include, path
from django.views.decorators.csrf import csrf_exempt

from whimsybots.views import TelegramWebhook

urlpatterns = [
    path("admin/", admin.site.urls),
    path("martor/", include("martor.urls")),
    path("webhook/<str:token>/", csrf_exempt(TelegramWebhook.as_view())),
]
