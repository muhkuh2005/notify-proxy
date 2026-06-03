"""Direct-call coverage for the OAuth login/callback logic in app/routes/auth.py.

The app only mounts Starlette's SessionMiddleware when OAuth is enabled *at
import time*. Under the test suite the app is imported in BASIC mode, so the
callback cannot be driven through a TestClient session. Instead we invoke the
route coroutines directly with a fake request object whose ``.session`` is a
plain dict, drive ``asyncio.run`` for the async routes, and monkeypatch
``auth.oauth.create_client`` to return a fake OAuth client.

Run isolated with a unique db::

    DATABASE_URL=sqlite:////tmp/np_oauth_flow.db RATELIMIT_ENABLED=false \\
        ADMIN_PASSWORD=test-password /tmp/np_venv/bin/python -m pytest \\
        tests/test_oauth_flow.py -q
"""
import asyncio
import types

import pytest

# Importing app.main triggers Base.metadata.create_all against the (unique) db.
import app.main  # noqa: F401  (side effect: create tables)
from app import auth
from app.routes import auth as auth_routes
from app.database import SessionLocal
from app.models import User


# ── Fakes ────────────────────────────────────────────────────────────────────
class _FakeResp:
    """Mimics an httpx-style response with a ``.json()`` method."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Stand-in for an authlib OAuth client.

    ``token`` is the dict returned by ``authorize_access_token``. ``responses``
    maps a request path (e.g. ``"user"``) to the payload returned by ``.get``.
    Set ``raise_token`` to simulate an ``authorize_access_token`` failure.
    """

    def __init__(self, token=None, responses=None, raise_token=False):
        self._token = token if token is not None else {}
        self._responses = responses or {}
        self._raise_token = raise_token
        self.calls = []  # record of get() paths, for assertions

    async def authorize_access_token(self, request):
        if self._raise_token:
            raise RuntimeError("boom: state mismatch")
        return self._token

    async def get(self, path, token=None):
        self.calls.append(path)
        if path not in self._responses:
            raise KeyError(f"unexpected get path: {path}")
        return _FakeResp(self._responses[path])


def _fake_request():
    """A minimal request whose ``.session`` is a plain mutable dict."""
    return types.SimpleNamespace(session={})


def _patch_client(monkeypatch, client):
    """Force ``auth.oauth.create_client`` to yield ``client`` for any provider."""
    monkeypatch.setattr(auth.oauth, "create_client", lambda provider: client)


def _enable_oauth(monkeypatch, providers=("github", "microsoft")):
    monkeypatch.setattr(auth, "ENABLED_PROVIDERS", list(providers))


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def db():
    """A session whose User table is emptied before and after each test."""
    s = SessionLocal()
    s.query(User).delete()
    s.commit()
    try:
        yield s
    finally:
        s.query(User).delete()
        s.commit()
        s.close()


# ── _primary_email ───────────────────────────────────────────────────────────
def test_primary_email_prefers_primary_verified():
    emails = [
        {"email": "secondary@example.com", "primary": False, "verified": True},
        {"email": "main@example.com", "primary": True, "verified": True},
    ]
    assert auth_routes._primary_email(emails) == ("main@example.com", True)


def test_primary_email_falls_back_to_any_verified():
    emails = [
        {"email": "unverified@example.com", "primary": True, "verified": False},
        {"email": "verified@example.com", "primary": False, "verified": True},
    ]
    assert auth_routes._primary_email(emails) == ("verified@example.com", True)


def test_primary_email_unverified_when_none_verified():
    emails = [
        {"email": "only@example.com", "primary": True, "verified": False},
    ]
    assert auth_routes._primary_email(emails) == ("only@example.com", False)


def test_primary_email_empty_list():
    assert auth_routes._primary_email([]) == (None, False)


# ── oauth_callback: github ───────────────────────────────────────────────────
def test_callback_github_first_user_becomes_admin(monkeypatch, db):
    # No admin-email allowlist -> the first-ever user is bootstrapped to admin.
    monkeypatch.setattr(auth, "ADMIN_EMAILS", set())
    _enable_oauth(monkeypatch)
    client = _FakeClient(
        token={"access_token": "t"},
        responses={"user": {"id": 4242, "login": "octocat", "name": "Octo Cat",
                            "email": "octo@example.com"}},
    )
    _patch_client(monkeypatch, client)

    req = _fake_request()
    resp = asyncio.run(auth_routes.oauth_callback("github", req, db))

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"  # admin -> straight to /admin
    user = db.query(User).filter(User.provider == "github", User.provider_sub == "4242").first()
    assert user is not None
    assert user.role == "admin" and user.status == "approved"
    assert req.session["uid"] == user.id
    # Public profile email present -> the /user/emails endpoint is never hit.
    assert client.calls == ["user"]


def test_callback_github_second_user_is_pending(monkeypatch, db):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", set())
    _enable_oauth(monkeypatch)

    # Seed an existing (first) user so the new login is NOT the first-ever.
    db.add(User(provider="github", provider_sub="1", email="first@example.com",
                name="First", role="admin", status="approved"))
    db.commit()

    client = _FakeClient(
        token={"access_token": "t"},
        responses={"user": {"id": 999, "login": "newbie", "name": "New Bie",
                            "email": "new@example.com"}},
    )
    _patch_client(monkeypatch, client)

    req = _fake_request()
    resp = asyncio.run(auth_routes.oauth_callback("github", req, db))

    assert resp.status_code == 303
    assert resp.headers["location"] == "/pending"
    user = db.query(User).filter(User.provider_sub == "999").first()
    assert user.role == "user" and user.status == "pending"
    assert req.session["uid"] == user.id


def test_callback_github_no_profile_email_uses_emails_endpoint(monkeypatch, db):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", set())
    _enable_oauth(monkeypatch)
    client = _FakeClient(
        token={"access_token": "t"},
        responses={
            "user": {"id": 7, "login": "noemail", "name": "No Email", "email": None},
            "user/emails": [
                {"email": "fallback@example.com", "primary": True, "verified": True},
            ],
        },
    )
    _patch_client(monkeypatch, client)

    req = _fake_request()
    resp = asyncio.run(auth_routes.oauth_callback("github", req, db))

    assert resp.status_code == 303
    user = db.query(User).filter(User.provider_sub == "7").first()
    assert user.email == "fallback@example.com"
    # Both endpoints were consulted because the profile carried no email.
    assert client.calls == ["user", "user/emails"]


def test_callback_blocked_user_clears_session(monkeypatch, db):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", set())
    _enable_oauth(monkeypatch)

    # Pre-create a blocked user for this provider+sub.
    db.add(User(provider="github", provider_sub="55", email="blk@example.com",
                name="Blocked", role="user", status="blocked"))
    db.commit()

    client = _FakeClient(
        token={"access_token": "t"},
        responses={"user": {"id": 55, "login": "blk", "name": "Blocked",
                            "email": "blk@example.com"}},
    )
    _patch_client(monkeypatch, client)

    req = _fake_request()
    req.session["stale"] = "value"  # ensure clear() actually wipes pre-existing data
    resp = asyncio.run(auth_routes.oauth_callback("github", req, db))

    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=blocked"
    assert req.session == {}  # session cleared, no uid set


def test_callback_token_failure_redirects_to_login_error_auth(monkeypatch, db):
    _enable_oauth(monkeypatch)
    client = _FakeClient(raise_token=True)
    _patch_client(monkeypatch, client)

    req = _fake_request()
    resp = asyncio.run(auth_routes.oauth_callback("github", req, db))

    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=auth"
    assert "uid" not in req.session


def test_callback_missing_sub_redirects_to_userinfo_error(monkeypatch, db):
    _enable_oauth(monkeypatch)
    # Profile carries no id -> sub is falsy -> userinfo error branch.
    client = _FakeClient(
        token={"access_token": "t"},
        responses={"user": {"login": "noid", "name": "No Id", "email": "x@example.com"}},
    )
    _patch_client(monkeypatch, client)

    req = _fake_request()
    resp = asyncio.run(auth_routes.oauth_callback("github", req, db))

    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=userinfo"
    assert "uid" not in req.session


def test_callback_unknown_provider_404(monkeypatch, db):
    from fastapi import HTTPException

    _enable_oauth(monkeypatch)
    # create_client returns None for an unregistered/unknown provider.
    monkeypatch.setattr(auth.oauth, "create_client", lambda provider: None)

    req = _fake_request()
    with pytest.raises(HTTPException) as ei:
        asyncio.run(auth_routes.oauth_callback("nope", req, db))
    assert ei.value.status_code == 404


def test_callback_disabled_oauth_404(monkeypatch, db):
    from fastapi import HTTPException

    monkeypatch.setattr(auth, "ENABLED_PROVIDERS", [])  # oauth_enabled() -> False
    req = _fake_request()
    with pytest.raises(HTTPException) as ei:
        asyncio.run(auth_routes.oauth_callback("github", req, db))
    assert ei.value.status_code == 404


# ── oauth_callback: microsoft ────────────────────────────────────────────────
def test_callback_microsoft_creates_user_from_token_userinfo(monkeypatch, db):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", set())
    _enable_oauth(monkeypatch)
    # Microsoft puts the OIDC claims under token["userinfo"]; no .get() call needed.
    client = _FakeClient(
        token={
            "access_token": "t",
            "userinfo": {
                "sub": "ms-sub-123",
                "email": "user@corp.example.com",
                "email_verified": True,
                "name": "Corp User",
            },
        },
    )
    _patch_client(monkeypatch, client)

    req = _fake_request()
    resp = asyncio.run(auth_routes.oauth_callback("microsoft", req, db))

    assert resp.status_code == 303
    user = db.query(User).filter(User.provider == "microsoft",
                                 User.provider_sub == "ms-sub-123").first()
    assert user is not None
    assert user.email == "user@corp.example.com"
    assert user.name == "Corp User"
    assert req.session["uid"] == user.id
    # token already carried userinfo -> no HTTP fetch performed.
    assert client.calls == []


def test_callback_microsoft_admin_email_grants_admin(monkeypatch, db):
    # A provider-verified email on the allowlist bootstraps an admin even though
    # other users may already exist.
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"boss@corp.example.com"})
    _enable_oauth(monkeypatch)
    db.add(User(provider="github", provider_sub="1", email="someone@example.com",
                name="Someone", role="user", status="approved"))
    db.commit()

    client = _FakeClient(
        token={
            "userinfo": {
                "sub": "ms-boss",
                "email": "boss@corp.example.com",
                "email_verified": True,
                "name": "The Boss",
            },
        },
    )
    _patch_client(monkeypatch, client)

    req = _fake_request()
    resp = asyncio.run(auth_routes.oauth_callback("microsoft", req, db))

    assert resp.headers["location"] == "/admin"
    user = db.query(User).filter(User.provider_sub == "ms-boss").first()
    assert user.role == "admin" and user.status == "approved"


# ── login_page / logout ──────────────────────────────────────────────────────
def test_login_page_redirects_to_admin_when_oauth_disabled(monkeypatch):
    monkeypatch.setattr(auth, "ENABLED_PROVIDERS", [])  # oauth_enabled() -> False
    req = _fake_request()
    resp = auth_routes.login_page(req)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"


def test_login_page_renders_providers_when_oauth_enabled(monkeypatch):
    from starlette.requests import Request

    _enable_oauth(monkeypatch)
    # available_providers() filters by registered credentials; force a known list
    # so we don't depend on env-registered OAuth clients.
    monkeypatch.setattr(auth, "available_providers", lambda: ["github", "microsoft"])

    # A real Starlette Request is needed because the template renderer reads
    # request scope; build a minimal ASGI scope.
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/login",
        "headers": [],
        "query_string": b"",
        "app": app.main.app,
    }
    request = Request(scope)
    resp = auth_routes.login_page(request)

    assert resp.status_code == 200
    body = resp.body.decode()
    assert "github" in body
    assert "microsoft" in body


def test_logout_clears_session_and_redirects(monkeypatch):
    _enable_oauth(monkeypatch)
    req = _fake_request()
    req.session["uid"] = 123
    resp = auth_routes.logout(req)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert req.session == {}  # cleared


def test_logout_basic_mode_does_not_touch_session(monkeypatch):
    # In basic mode the request has no usable session; logout must not call
    # .clear() (oauth_enabled() is False) and should still redirect to /login.
    monkeypatch.setattr(auth, "ENABLED_PROVIDERS", [])
    req = types.SimpleNamespace()  # deliberately *no* .session attribute
    resp = auth_routes.logout(req)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
