"""Local conftest for `src/tests/test_pdf_generator/`.

Two pre-import tasks are required so that simply importing
`pdf_generator.helpers` doesn't blow up on Windows CI workers:

1. WeasyPrint requires native libraries (`libgobject-2.0-0`, etc.) that
   aren't guaranteed to be installed. We stub the `weasyprint` module
   with a `MagicMock`; tests that need real PDF generation patch
   `pdf_generator.helpers.HTML` directly.

2. The `pdf_generator` package builds a SQLAlchemy engine at import
   time, which raises `EnvironmentError` if DB_* env vars are unset.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

# 1. Stub weasyprint before pdf_generator.helpers is imported.
if "weasyprint" not in sys.modules:
    sys.modules["weasyprint"] = MagicMock(name="weasyprint-stub")

# 2. Provide default DB env vars.
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PASSWORD", "test_pw")
os.environ.setdefault("DB_HOST", "localhost")
