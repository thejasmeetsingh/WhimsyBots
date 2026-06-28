"""Django AppConfig for the 'app' package.

Standard Django application registration; no custom signals or
ready-time hooks.
"""

from django.apps import AppConfig


class AppConfig(AppConfig):
    """Django app config for the 'app' package."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "app"
