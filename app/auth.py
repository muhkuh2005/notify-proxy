"""Authentication & authorization.

Two modes, chosen by env:

- **OAuth mode** (when `OAUTH_ENABLED_PROVIDERS` is set): users log in via GitHub
  or Microsoft365. A successful login only *authenticates* — access requires an
  `approved` status. Admins approve/block users.
- **Basic mode** (default / fallback): the legacy single admin via HTTP Basic
  (`ADMIN_USER` / `ADMIN_PASSWORD`). Treated as an all-powerful admin.

Authorization helpers (`can_edit` / `can_view`) implement per-resource ownership:
an admin may touch everything; a regular user may edit only resources they own,
and may view/use resources they own or that are marked `global`.
"""
import logging
import os
import secrets as _secrets

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .util import now_utc

logger = logging.getLogger(__name__)

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ENABLED_PROVIDERS = [p.strip().lower() for p in os.environ.get("OAUTH_ENABLED_PROVIDERS", "").split(",") if p.strip()]
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("OAUTH_ADMIN_EMAILS", "").split(",") if e.strip()}
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")


def oauth_enabled() -> bool:
    return bool(ENABLED_PROVIDERS)


# ── Authlib provider registration ───────────────────────────────────────────
oauth = OAuth()


def _register_providers() -> None:
    if "github" in ENABLED_PROVIDERS and os.environ.get("GITHUB_CLIENT_ID"):
        oauth.register(
            name="github",
            client_id=os.environ["GITHUB_CLIENT_ID"],
            client_secret=os.environ.get("GITHUB_CLIENT_SECRET"),
            access_token_url="https://github.com/login/oauth/access_token",
            authorize_url="https://github.com/login/oauth/authorize",
            api_base_url="https://api.github.com/",
            client_kwargs={"scope": "read:user user:email"},
        )
    if "microsoft" in ENABLED_PROVIDERS and os.environ.get("MICROSOFT_CLIENT_ID"):
        tenant = os.environ.get("MICROSOFT_TENANT", "common")
        oauth.register(
            name="microsoft",
            client_id=os.environ["MICROSOFT_CLIENT_ID"],
            client_secret=os.environ.get("MICROSOFT_CLIENT_SECRET"),
            server_metadata_url=f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )


_register_providers()


def available_providers() -> list[str]:
    """Providers that are both enabled and have credentials registered."""
    return [p for p in ENABLED_PROVIDERS if oauth.create_client(p) is not None]


# ── User upsert from provider userinfo ──────────────────────────────────────
def upsert_user(
    db: Session,
    provider: str,
    sub: str,
    email: str | None,
    name: str | None,
    email_verified: bool = False,
) -> User:
    sub = str(sub)
    u = db.query(User).filter(User.provider == provider, User.provider_sub == sub).first()
    # The admin-email bootstrap is a privilege decision, so it must key off a
    # provider-*verified* email — never an unverified or attacker-supplied one.
    is_admin_email = bool(email) and email_verified and email.lower() in ADMIN_EMAILS

    if u is None:
        first_ever = db.query(User).count() == 0
        make_admin = is_admin_email or (not ADMIN_EMAILS and first_ever)
        u = User(
            provider=provider,
            provider_sub=sub,
            email=email,
            name=name,
            role="admin" if make_admin else "user",
            status="approved" if make_admin else "pending",
        )
        db.add(u)
    else:
        if email:
            u.email = email
        if name:
            u.name = name
        if is_admin_email:
            u.role = "admin"
            if u.status == "pending":
                u.status = "approved"
    u.last_login = now_utc()
    db.commit()
    db.refresh(u)
    return u


# ── Basic-auth fallback (single admin) ──────────────────────────────────────
_basic = HTTPBasic(auto_error=False)


def _basic_admin() -> User:
    """A transient (unsaved) admin user representing the Basic-auth operator."""
    return User(id=None, provider="basic", provider_sub="admin", name=ADMIN_USER, role="admin", status="approved")


def _check_basic(credentials: HTTPBasicCredentials | None) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": 'Basic realm="notify-proxy admin"'},
    )
    if credentials is None or not ADMIN_PASSWORD:
        raise unauthorized
    ok_user = _secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    ok_pass = _secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not (ok_user and ok_pass):
        raise unauthorized
    return _basic_admin()


def _session_user(request: Request, db: Session) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    return db.query(User).filter(User.id == uid).first()


def _redirect(path: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": path})


# ── FastAPI dependencies ────────────────────────────────────────────────────
def require_login(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> User:
    """Any authenticated principal (used for the 'pending approval' page)."""
    if not oauth_enabled():
        return _check_basic(credentials)
    u = _session_user(request, db)
    if u is None:
        raise _redirect("/login")
    return u


def require_user(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> User:
    """An approved user (or admin). Pending -> /pending, blocked -> 403."""
    if not oauth_enabled():
        return _check_basic(credentials)
    u = _session_user(request, db)
    if u is None:
        raise _redirect("/login")
    if u.status == "blocked":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account blocked")
    if u.is_approved:
        return u
    raise _redirect("/pending")


def require_admin(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> User:
    if not oauth_enabled():
        return _check_basic(credentials)
    u = _session_user(request, db)
    if u is None:
        raise _redirect("/login")
    if not u.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return u


# ── Authorization helpers (per-resource ownership) ──────────────────────────
def can_edit(user: User, owner_id: int | None) -> bool:
    return user.is_admin or (owner_id is not None and owner_id == user.id)


def can_view(user: User, owner_id: int | None, visibility: str | None) -> bool:
    return user.is_admin or visibility == "global" or (owner_id is not None and owner_id == user.id)
