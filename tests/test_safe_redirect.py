"""The redirect sanitizer must only ever emit local, relative paths."""
import pytest

from app.routes.admin import _safe_redirect


def _location(path: str) -> str:
    return _safe_redirect(path).headers["location"]


def test_allows_local_path():
    assert _location("/admin/projects/5?saved=1") == "/admin/projects/5?saved=1"


@pytest.mark.parametrize(
    "evil",
    [
        "https://evil.com",
        "http://evil.com/path",
        "//evil.com",
        "/\\evil.com",
        "\\\\evil.com",
        "javascript:alert(1)",
        "evil.com",
    ],
)
def test_blocks_external_targets(evil):
    assert _location(evil) == "/admin"
