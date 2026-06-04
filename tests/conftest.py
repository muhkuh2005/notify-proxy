"""Test environment setup.

These env vars must be set *before* `app` is imported, because
`app.database` reads `DATABASE_URL` and `app.main` enforces `ADMIN_PASSWORD`
at import time.
"""
import os
import tempfile
import shutil

# Use a temporary directory for cross-platform compatibility
_test_db_dir = tempfile.mkdtemp(prefix="notify_proxy_test_")
_test_db_path = os.path.join(_test_db_dir, "notify_proxy_test.db")

os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("COOLIFY_INCOMING_TOKEN", "test-incoming-token")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_test_db_path}".replace("\\", "/"))
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("RATELIMIT_ENABLED", "false")  # deterministic tests

import pytest


@pytest.fixture(scope="session", autouse=True)
def _clean_db():
    """Start each test session from a fresh SQLite file."""
    # Clean up the database directory before tests
    global _test_db_dir, _test_db_path
    if os.path.exists(_test_db_dir):
        try:
            shutil.rmtree(_test_db_dir)
        except (OSError, PermissionError):
            # If cleanup fails, try to remove individual files
            for p in (_test_db_path, f"{_test_db_path}-wal", f"{_test_db_path}-shm"):
                try:
                    os.remove(p)
                except (FileNotFoundError, PermissionError):
                    pass
    
    # Create fresh directory for this test session
    _test_db_dir = tempfile.mkdtemp(prefix="notify_proxy_test_")
    _test_db_path = os.path.join(_test_db_dir, "notify_proxy_test.db")
    yield
    
    # Cleanup after tests complete
    if os.path.exists(_test_db_dir):
        try:
            shutil.rmtree(_test_db_dir, ignore_errors=True)
        except (OSError, PermissionError):
            pass
