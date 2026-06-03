"""Authentication / authorization logic.

The app under the main test session runs in **Basic mode** (no OAuth env), so
existing tests keep working. OAuth-mode wiring is exercised in an isolated
subprocess with the relevant env set.
"""
import os
import subprocess
import sys

from app.auth import can_edit, can_view
from app.models import User

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _user(**kw) -> User:
    return User(**kw)


def test_can_edit():
    admin = _user(id=1, role="admin", status="approved")
    owner = _user(id=2, role="user", status="approved")
    assert can_edit(admin, 99) is True          # admin edits anything
    assert can_edit(owner, 2) is True            # owns it
    assert can_edit(owner, 3) is False           # someone else's
    assert can_edit(owner, None) is False        # unowned/legacy


def test_can_view():
    admin = _user(id=1, role="admin", status="approved")
    user = _user(id=2, role="user", status="approved")
    assert can_view(admin, 5, "private") is True       # admin sees all
    assert can_view(user, 2, "private") is True        # own private
    assert can_view(user, 9, "global") is True         # someone else's global
    assert can_view(user, 9, "private") is False       # someone else's private


def test_upsert_bootstrap_and_pending():
    from app import auth
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        db.query(User).delete()
        db.commit()
        # No OAUTH_ADMIN_EMAILS in this env -> first user becomes admin.
        first = auth.upsert_user(db, "github", "1", "a@example.com", "Alice")
        assert first.role == "admin" and first.status == "approved"
        # Second user is a normal pending user.
        second = auth.upsert_user(db, "github", "2", "b@example.com", "Bob")
        assert second.role == "user" and second.status == "pending"
        # Re-login keeps identity and status.
        again = auth.upsert_user(db, "github", "2", "b@example.com", "Bob")
        assert again.id == second.id and again.status == "pending"
    finally:
        db.query(User).delete()
        db.commit()
        db.close()


def test_admin_email_bootstrap_requires_verified_email(monkeypatch):
    """An OAUTH_ADMIN_EMAILS match must NOT grant admin on an unverified email."""
    from app import auth
    from app.database import SessionLocal

    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"boss@example.com"})
    db = SessionLocal()
    try:
        db.query(User).delete()
        db.commit()
        # Unverified email that matches the allowlist -> stays a pending user.
        u = auth.upsert_user(db, "microsoft", "10", "boss@example.com", "X", email_verified=False)
        assert u.role == "user" and u.status == "pending"

        db.query(User).delete()
        db.commit()
        # Same email, provider-verified -> admin.
        v = auth.upsert_user(db, "microsoft", "11", "boss@example.com", "X", email_verified=True)
        assert v.role == "admin" and v.status == "approved"
    finally:
        db.query(User).delete()
        db.commit()
        db.close()


def test_oauth_mode_wiring():
    """With OAuth enabled: /login renders and /admin redirects unauthenticated users."""
    script = (
        "import os;"
        "os.environ['OAUTH_ENABLED_PROVIDERS']='github';"
        "os.environ['GITHUB_CLIENT_ID']='cid';os.environ['GITHUB_CLIENT_SECRET']='sec';"
        "os.environ['SESSION_SECRET']='x'*48;"
        "os.environ['DATABASE_URL']='sqlite:////tmp/np_oauth_wiring.db';"
        "os.environ.pop('ADMIN_PASSWORD', None);"
        "from fastapi.testclient import TestClient;"
        "from app.main import app;"
        "c=TestClient(app);"
        "assert c.get('/login').status_code==200, 'login page';"
        "r=c.get('/admin', follow_redirects=False);"
        "assert r.status_code in (303,307), r.status_code;"
        "assert r.headers['location']=='/login', r.headers.get('location');"
        "print('OAUTH_OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True
    )
    assert "OAUTH_OK" in result.stdout, result.stderr
