import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "whimsybots.settings.base")

task = Celery("whimsybots.settings.base")
task.config_from_object("django.conf:settings", force=True, namespace="CELERY")
task.autodiscover_tasks(["app"])
