"""Test environment setup.

These env vars must be set *before* `app` is imported, because
`app.database` reads `DATABASE_URL` and `app.main` enforces `ADMIN_PASSWORD`
at import time.
"""
import os

os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("COOLIFY_INCOMING_TOKEN", "test-incoming-token")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/notify_proxy_test.db")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("RATELIMIT_ENABLED", "false")  # deterministic tests

import pytest


@pytest.fixture(scope="session", autouse=True)
def _clean_db():
    """Start each test session from a fresh SQLite file."""
    path = "/tmp/notify_proxy_test.db"
    for p in (path, f"{path}-wal", f"{path}-shm"):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    yield
