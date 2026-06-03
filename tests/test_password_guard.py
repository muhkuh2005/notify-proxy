"""The app must refuse to boot with an unset or default admin password."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _import_app_with(password_env: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": "sqlite:////tmp/notify_proxy_guard.db"}
    env.update(password_env)
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_rejects_changeme():
    result = _import_app_with({"ADMIN_PASSWORD": "changeme"})
    assert result.returncode != 0
    assert "ADMIN_PASSWORD" in result.stderr


def test_rejects_empty():
    result = _import_app_with({"ADMIN_PASSWORD": ""})
    assert result.returncode != 0
    assert "ADMIN_PASSWORD" in result.stderr


def test_accepts_strong_password():
    result = _import_app_with({"ADMIN_PASSWORD": "a-strong-secret"})
    assert result.returncode == 0, result.stderr
