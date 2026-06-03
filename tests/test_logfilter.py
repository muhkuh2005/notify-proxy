"""The uvicorn access-log filter drops /health probe lines only."""
import logging

from app.main import _SuppressHealthAccessLog


def _rec(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1", "GET", path, "1.1", 200), None,
    )


def test_health_lines_dropped():
    assert _SuppressHealthAccessLog().filter(_rec("/health")) is False


def test_other_lines_kept():
    f = _SuppressHealthAccessLog()
    assert f.filter(_rec("/webhook/coolify/x")) is True
    assert f.filter(_rec("/admin")) is True


def test_non_access_record_kept():
    # records without the expected args tuple pass through untouched
    rec = logging.LogRecord("app", logging.INFO, "", 0, "plain message", None, None)
    assert _SuppressHealthAccessLog().filter(rec) is True
