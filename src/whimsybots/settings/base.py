"""Base Django settings for the WhimsyBots project.

The test settings module ('whimsybots.settings.test') imports from
this file and overrides values needed for the in-memory SQLite-backed
test environment. Production deployments also extend this file with
real DB credentials, webhook URLs, and production-grade caching.
"""

import logging.config
import os
from pathlib import Path

from celery.schedules import crontab
from django.utils.log import DEFAULT_LOGGING

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = ["*"]


# Application definition

INSTALLED_APPS = [
    "app",
    "martor",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "whimsybots.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "whimsybots.wsgi.application"


# Database

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DB_NAME,
        "USER": DB_USER,
        "PASSWORD": DB_PASSWORD,
        "HOST": DB_HOST,
        "PORT": 5432,
    }
}


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = "/static/"
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "whimsybots/static"),
]
STATIC_ROOT = os.path.join(BASE_DIR, "static")
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

LOGIN_URL = "/admin/login/"


# Martor Config

MARTOR_THEME = "bootstrap"
MARTOR_ENABLE_CONFIGS = {
    "emoji": "true",
    "imgur": "false",
    "mention": "false",
    "jquery": "true",
    "living": "false",
    "spellcheck": "false",
    "hljs": "true",
}

MARTOR_TOOLBAR_BUTTONS = [
    "bold",
    "italic",
    "horizontal",
    "heading",
    "pre-code",
    "blockquote",
    "unordered-list",
    "ordered-list",
    "emoji",
    "toggle-maximize",
    "help",
]

CSRF_COOKIE_HTTPONLY = False
CSRF_TRUSTED_ORIGINS = [
    "https://localhost:8000",
    "http://localhost:8000",
]


# Logging Config

LOGGING_CONFIG = None
logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
                "format": (
                    "%(asctime)s - %(module)s.%(funcName)s - %(name)s "
                    "- %(levelname)s - %(message)s - %(exc_text)s"
                ),
            },
            "django.server": DEFAULT_LOGGING["formatters"]["django.server"],
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            },
            "django.server": DEFAULT_LOGGING["handlers"]["django.server"],
        },
        "loggers": {
            "": {
                "level": "INFO",
                "handlers": ["console"],
            },
            "django.db.backends": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            },
            "django.server": DEFAULT_LOGGING["loggers"]["django.server"],
        },
    }
)


# Celery
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND")

# Define queues
CELERY_QUEUES = {
    "beat": {"exchange": "beat", "routing_key": "beat"},
    "default": {"exchange": "default", "routing_key": "default"},
}

# Route Beat-scheduled tasks to "beat" queue
CELERY_TASK_ROUTES = {
    "app.tasks.cron_job_poller": {"queue": "beat"},
    # All other tasks (process_inbound_message, generate_report, etc.) go to 'default' queue
}

# Default queue for unmapped tasks
CELERY_DEFAULT_QUEUE = "default"
CELERY_DEFAULT_EXCHANGE = "default"
CELERY_DEFAULT_ROUTING_KEY = "default"

CELERY_BEAT_SCHEDULE = {
    # Handles scheduled cron job runs (journaling prompts, briefings, etc.)
    # Runs every minute — latency acceptable for scheduled outbound messages
    "cron-job-poller": {
        "task": "app.tasks.cron_job_poller",
        "schedule": crontab(minute="*"),
        "options": {"queue": "beat"},
    },
}


# Webhook configuration
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://localhost:8000")


# Cache configuration
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("CACHE_LOCATION"),
    },
}
