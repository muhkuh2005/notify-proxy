"""OAuth login, logout, the pending-approval page, and user management."""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import auth
from ..database import get_db
from ..models import User

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "templates"))


def _primary_email(emails: list[dict]) -> tuple[str | None, bool]:
    """Return (email, verified) from a GitHub /user/emails response."""
    for e in emails:
        if e.get("primary") and e.get("verified"):
            return e.get("email"), True
    for e in emails:
        if e.get("verified"):
            return e.get("email"), True
    if emails:
        return emails[0].get("email"), False
    return None, False


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not auth.oauth_enabled():
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"providers": auth.available_providers()}
    )


@router.get("/logout")
def logout(request: Request):
    if auth.oauth_enabled():
        request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/pending", response_class=HTMLResponse)
def pending_page(request: Request, user: User = Depends(auth.require_login)):
    if user.is_approved:
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(request, "pending.html", {"user": user})


@router.get("/auth/{provider}/login")
async def oauth_login(provider: str, request: Request):
    if not auth.oauth_enabled():
        raise HTTPException(status_code=404)
    client = auth.oauth.create_client(provider)
    if client is None:
        raise HTTPException(status_code=404, detail="unknown provider")
    redirect_uri = f"{auth.BASE_URL}/auth/{provider}/callback" if auth.BASE_URL else request.url_for(
        "oauth_callback", provider=provider
    )
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/auth/{provider}/callback", name="oauth_callback")
async def oauth_callback(provider: str, request: Request, db: Session = Depends(get_db)):
    if not auth.oauth_enabled():
        raise HTTPException(status_code=404)
    client = auth.oauth.create_client(provider)
    if client is None:
        raise HTTPException(status_code=404, detail="unknown provider")

    try:
        token = await client.authorize_access_token(request)
    except Exception as exc:
        logger.warning("oauth callback failed for %s: %s", provider, exc)
        return RedirectResponse(url="/login?error=auth", status_code=303)

    sub = email = name = None
    email_verified = False
    try:
        if provider == "github":
            profile = (await client.get("user", token=token)).json()
            sub = profile.get("id")
            name = profile.get("name") or profile.get("login")
            email = profile.get("email")
            # GitHub only exposes verified addresses as the public profile email.
            email_verified = bool(email)
            if not email:
                emails = (await client.get("user/emails", token=token)).json()
                email, email_verified = _primary_email(emails if isinstance(emails, list) else [])
        elif provider == "microsoft":
            userinfo = token.get("userinfo") or await client.userinfo(token=token)
            sub = userinfo.get("sub")
            name = userinfo.get("name")
            # Only trust `email` when the provider asserts it is verified; never
            # use `preferred_username` for the (privileged) admin-email decision.
            email = userinfo.get("email") or userinfo.get("preferred_username")
            email_verified = userinfo.get("email_verified") is True and bool(userinfo.get("email"))
    except Exception as exc:
        logger.warning("oauth userinfo failed for %s: %s", provider, exc)

    if not sub:
        return RedirectResponse(url="/login?error=userinfo", status_code=303)

    user = auth.upsert_user(db, provider, sub, email, name, email_verified)

    if user.status == "blocked":
        request.session.clear()
        return RedirectResponse(url="/login?error=blocked", status_code=303)

    request.session["uid"] = user.id
    if user.is_approved:
        return RedirectResponse(url="/admin", status_code=303)
    return RedirectResponse(url="/pending", status_code=303)


# ── User management (admin only) ────────────────────────────────────────────
@router.get("/admin/users", response_class=HTMLResponse)
def users_list(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(auth.require_admin),
):
    users = db.query(User).order_by(User.created_at).all()
    return templates.TemplateResponse(request, "users.html", {"users": users, "me": admin})


def _admin_count(db: Session) -> int:
    return db.query(User).filter(User.role == "admin", User.status != "blocked").count()


def _get_user(db: Session, user_id: int) -> User:
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404)
    return u


@router.post("/admin/users/{user_id}/approve")
def user_approve(user_id: int, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    u = _get_user(db, user_id)
    u.status = "approved"
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/block")
def user_block(user_id: int, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    u = _get_user(db, user_id)
    if u.role == "admin" and _admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot block the last admin")
    u.status = "blocked"
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/promote")
def user_promote(user_id: int, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    u = _get_user(db, user_id)
    u.role = "admin"
    u.status = "approved"
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/demote")
def user_demote(user_id: int, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    u = _get_user(db, user_id)
    if u.role == "admin" and _admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot demote the last admin")
    u.role = "user"
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/delete")
def user_delete(user_id: int, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    u = _get_user(db, user_id)
    if u.role == "admin" and _admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last admin")
    db.delete(u)
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)
