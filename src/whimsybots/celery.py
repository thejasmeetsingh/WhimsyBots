"""Celery application bootstrap.

Exposes the 'task' Celery instance configured from Django settings and
autodiscovers tasks under the 'app' package. Import this module via
'from whimsybots.celery import task' to register the decorator.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "whimsybots.settings.base")

task = Celery("whimsybots.settings.base")
task.config_from_object("django.conf:settings", force=True, namespace="CELERY")
task.autodiscover_tasks(["app"])
