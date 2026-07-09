from whimsybots.settings.base import *  # noqa

# ──────────────────────────────────────────────
# WeasyPrint stub
# ──────────────────────────────────────────────
# WeasyPrint requires native libraries ('libgobject-2.0-0', etc.) that
# are not guaranteed to be present on every CI worker. The mcp_tools
# module is imported via the app's admin registration, so we stub the
# 'weasyprint' module *before* Django's app registry is populated.
import sys as _sys  # noqa: E402
from unittest.mock import MagicMock as _MagicMock  # noqa: E402

if "weasyprint" not in _sys.modules:
    _sys.modules["weasyprint"] = _MagicMock(name="weasyprint-stub")

# ──────────────────────────────────────────────
# Secret key – the base settings pull SECRET_KEY from the environment,
# which is unset in CI / local dev. Anything that touches
# django.conf.settings (e.g. utils.crypto.get_fernet) needs a
# non-empty key. The value is constant per test run so deterministic
# helpers like get_token_hash remain stable.
# ──────────────────────────────────────────────
SECRET_KEY = "test-secret-key-for-unit-tests-only"

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
    """Drop-in SQLite shim for django.contrib.postgres.fields.ArrayField.

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
    """Drop-in SQLite shim for pgvector.django.VectorField.

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
# Migration 0002_initial.py does `import pgvector.django.vector`; stub that
# sub-module too so the migration can be loaded under the SQLite shim.
sys.modules.setdefault("pgvector.django.vector", MagicMock())
# Some migrations also import from pgvector directly; the stub for the
# parent package above covers that.


# ──────────────────────────────────────────────
# Cache – fakeredis instead of a real Redis
# ──────────────────────────────────────────────
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
