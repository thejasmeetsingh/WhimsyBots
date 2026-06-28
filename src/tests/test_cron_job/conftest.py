"""Local conftest for 'src/tests/test_cron_job/'.

The 'cron_job' package imports its 'db.py' module at import time to build
a SQLAlchemy engine — which raises 'EnvironmentError' unless DB_NAME,
DB_USER, DB_PASSWORD are set. We pre-set them here so tests in this
directory can simply 'from cron_job import ...' without side effects.
"""

from __future__ import annotations

import os

# Must be set before `cron_job.db` is imported.
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PASSWORD", "test_pw")
os.environ.setdefault("DB_HOST", "localhost")
