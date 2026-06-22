from whimsybots.settings.base import *  # noqa

# ──────────────────────────────────────────────
# Database – SQLite with PostgreSQL field shims
# ──────────────────────────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Make django.contrib.postgres's ArrayField work on SQLite.
# django-test-without-migrations / unittest shims aren't needed;
# the simplest approach is to swap the backend at import time.

# 1. ArrayField → stored as JSON text in SQLite
#    Override only if you're using ArrayField directly in models.
#    If you import from django.contrib.postgres.fields, patch it:
from django.contrib.postgres import fields as pg_fields  # noqa: E402
from django.db.models import JSONField  # noqa: E402


class _ArrayField(JSONField):
    """
    Drop-in SQLite shim for django.contrib.postgres.fields.ArrayField.
    Stores the array as a JSON array; supports most query patterns used in tests.
    """

    def __init__(self, base_field, size=None, **kwargs):
        self.base_field = base_field
        self.size = size
        super().__init__(**kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        args = [self.base_field] + list(args)
        if self.size is not None:
            kwargs["size"] = self.size
        return name, path, args, kwargs


pg_fields.ArrayField = _ArrayField


# 2. VectorField (pgvector) → stored as JSON text in SQLite
#    pgvector's VectorField won't even import cleanly without psycopg2, so we
#    provide a full shim before any app code tries to import it.
import sys  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


class _VectorField(JSONField):
    """
    Drop-in SQLite shim for pgvector.django.VectorField.
    Stores the vector as a JSON array of floats.
    """

    def __init__(self, dimensions=None, **kwargs):
        self.dimensions = dimensions
        super().__init__(**kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.dimensions is not None:
            kwargs["dimensions"] = self.dimensions
        return name, path, args, kwargs


# Stub out the entire pgvector.django module so imports don't fail
_pgvector_module = MagicMock()
_pgvector_module.VectorField = _VectorField
_pgvector_module.HnswIndex = MagicMock()
_pgvector_module.IvfflatIndex = MagicMock()
sys.modules.setdefault("pgvector", MagicMock())
sys.modules["pgvector.django"] = _pgvector_module


# ──────────────────────────────────────────────
# Cache – fakeredis instead of a real Redis
# ──────────────────────────────────────────────
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
