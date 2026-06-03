import os
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, selectinload

from ..auth import can_edit, can_view, require_admin, require_user
from ..database import get_db
from ..models import Bot, Destination, DestinationType, FilterMode, Project, User
from ..notifiers import discord, mattermost, slack
from ..notifiers import email as email_notifier
from ..notifiers import telegram as telegram_notifier
from ..services import coolify_sync, settings_store, verification

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "templates"))

COOLIFY_INCOMING_TOKEN = os.environ.get("COOLIFY_INCOMING_TOKEN", "")


def _safe_redirect(path: str, status_code: int = 303) -> RedirectResponse:
    """Redirect only to a local, relative path — never an absolute/external URL.

    Defense in depth: every admin redirect target is server-constructed and
    already local, but this guarantees it regardless of future refactors.

    Structured as an early-return guard so the value only reaches the redirect
    on the verified-safe branch (also the form static analysers recognise as a
    sanitizing barrier).
    """
    parsed = urlparse(path)
    if (
        parsed.scheme
        or parsed.netloc
        or "\\" in path  # browsers normalise backslashes to slashes
        or not path.startswith("/")
        or path.startswith("//")
    ):
        return RedirectResponse(url="/admin", status_code=status_code)
    return RedirectResponse(url=path, status_code=status_code)


def _ensure_can_edit(user: User, owner_id: int | None) -> None:
    if not can_edit(user, owner_id):
        raise HTTPException(status_code=403, detail="Not allowed for this resource")


def _visible_bots(db: Session, user: User) -> list[Bot]:
    bots = db.query(Bot).order_by(Bot.name).all()
    return [b for b in bots if can_view(user, b.owner_id, b.visibility)]


@router.get("/", response_class=RedirectResponse)
def root():
    return _safe_redirect("/admin")


# ── Projects ──────────────────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
def admin_index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    projects = (
        db.query(Project)
        .options(selectinload(Project.destinations))
        .order_by(Project.name)
        .all()
    )
    return templates.TemplateResponse(request, "index.html", {
        "projects": projects,
        "coolify_sync_enabled": coolify_sync.is_configured(),
        "coolify_incoming_token": COOLIFY_INCOMING_TOKEN,
        "user": user,
    })


@router.get("/admin/projects/new", response_class=HTMLResponse)
def project_new(request: Request, user: User = Depends(require_admin)):
    return templates.TemplateResponse(request, "project_edit.html", {
        "project": None,
        "destinations": [],
        "bots": [],
        "user": user,
    })


@router.post("/admin/projects/new")
def project_create(
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if db.query(Project).filter(Project.name == name).first():
        raise HTTPException(status_code=400, detail="name already exists")
    p = Project(name=name)
    db.add(p)
    db.commit()
    return _safe_redirect(f"/admin/projects/{p.id}")


@router.get("/admin/projects/{project_id}", response_class=HTMLResponse)
def project_edit(
    request: Request,
    project_id: int,
    saved: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404)
    dests = [d for d in p.destinations if can_view(user, d.owner_id, d.visibility)]
    for d in dests:
        d.editable = can_edit(user, d.owner_id)
    return templates.TemplateResponse(request, "project_edit.html", {
        "project": p,
        "destinations": dests,
        "bots": _visible_bots(db, user),
        "saved": saved,
        "user": user,
    })


@router.post("/admin/projects/{project_id}/destinations/add")
async def destination_add(
    project_id: int,
    bot_id: int = Form(...),
    telegram_chat_id: str = Form(""),
    ntfy_topic: str = Form(""),
    ntfy_priority: str = Form(""),
    mattermost_target: str = Form(""),
    email_to: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404)
    bot = db.query(Bot).filter(Bot.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=400, detail="bot not found")
    # May only attach bots you are allowed to use (own or global; admin: any).
    if not can_view(user, bot.owner_id, bot.visibility):
        raise HTTPException(status_code=403, detail="Not allowed to use this bot")

    if bot.type == DestinationType.telegram and not telegram_chat_id.strip():
        raise HTTPException(status_code=400, detail="telegram_chat_id required for Telegram bot")
    if bot.type == DestinationType.ntfy and not ntfy_topic.strip():
        raise HTTPException(status_code=400, detail="ntfy_topic required for ntfy bot")
    if bot.type == DestinationType.mattermost and not mattermost_target.strip():
        raise HTTPException(status_code=400, detail="mattermost_target required for Mattermost bot")
    if bot.type == DestinationType.email and not email_to.strip():
        raise HTTPException(status_code=400, detail="email_to required for Email bot")

    chat_id = telegram_chat_id.strip() or None
    chat_label: str | None = None
    if chat_id and chat_id.startswith("@") and bot.telegram_bot_token:
        resolved = await telegram_notifier.resolve_chat_id(bot.telegram_bot_token, chat_id)
        if resolved:
            chat_label = chat_id
            chat_id = resolved
        else:
            # Check DB cache: same @username already resolved by any other destination
            cached = db.query(Destination).filter(
                Destination.telegram_chat_label == chat_id,
                Destination.telegram_chat_id.isnot(None),
            ).first()
            if cached:
                chat_label = chat_id
                chat_id = cached.telegram_chat_id
                # Fall through — test message will check if this bot needs /start
            else:
                vbot_token = settings_store.get_setting(db, "verification_bot_token")
                if not vbot_token:
                    raise HTTPException(
                        status_code=400,
                        detail="Verification bot not configured — go to Settings and set a token, or use numeric chat ID",
                    )
                dest = Destination(
                    project_id=project_id,
                    bot_id=bot_id,
                    type=bot.type,
                    telegram_chat_id=None,
                    telegram_chat_label=chat_id,
                    enabled=False,
                    owner_id=user.id,
                )
                db.add(dest)
                db.commit()
                code = verification.create(dest.id, chat_id)
                return _safe_redirect(f"/admin/destinations/{dest.id}/verify?code={code}")

    mm_channel_id = None
    if bot.type == DestinationType.mattermost:
        mm_channel_id = await mattermost.resolve_channel_id(
            bot.mattermost_url, bot.mattermost_token,
            mattermost_target.strip(), bot.mattermost_team,
        )
        if not mm_channel_id:
            raise HTTPException(
                status_code=400,
                detail="Could not resolve Mattermost target — check @user/channel name, the bot's team, and token permissions.",
            )

    dest = Destination(
        project_id=project_id,
        bot_id=bot_id,
        type=bot.type,
        telegram_chat_id=chat_id,
        telegram_chat_label=chat_label,
        ntfy_topic=ntfy_topic.strip() or None,
        ntfy_priority=int(ntfy_priority) if ntfy_priority.strip().isdigit() else None,
        mattermost_target=mattermost_target.strip() or None,
        mattermost_channel_id=mm_channel_id,
        email_to=email_to.strip() or None,
        owner_id=user.id,
    )
    db.add(dest)
    db.commit()

    # Test message: verify the bot can actually reach this target
    if bot.type == DestinationType.telegram and bot.telegram_bot_token and dest.telegram_chat_id:
        redirect = await _test_and_redirect(db, dest, bot.telegram_bot_token, f"/admin/projects/{project_id}")
        return redirect

    _TEST_MD = "✅ **notify-proxy** — Destination erfolgreich eingerichtet."
    ok: bool | None = None
    if bot.type == DestinationType.mattermost and dest.mattermost_channel_id:
        ok = await mattermost.send(bot.mattermost_url, bot.mattermost_token, dest.mattermost_channel_id, _TEST_MD)
    elif bot.type == DestinationType.slack and bot.slack_url:
        ok = await slack.send(bot.slack_url, _TEST_MD)
    elif bot.type == DestinationType.discord and bot.discord_url:
        ok = await discord.send(bot.discord_url, _TEST_MD)
    elif bot.type == DestinationType.email and bot.smtp_host and bot.smtp_from and dest.email_to:
        ok = await email_notifier.send(
            bot.smtp_host, bot.smtp_port, bot.smtp_user, bot.smtp_password,
            bot.smtp_from, bot.smtp_use_tls, dest.email_to,
            "notify-proxy test", "Destination erfolgreich eingerichtet.",
        )
    if ok is not None:
        dest.last_test_ok = ok
        db.commit()

    return _safe_redirect(f"/admin/projects/{project_id}?saved=1")


def _dest_for_edit(db: Session, dest_id: int, user: User) -> Destination:
    dest = db.query(Destination).filter(Destination.id == dest_id).first()
    if not dest:
        raise HTTPException(status_code=404)
    _ensure_can_edit(user, dest.owner_id)
    return dest


@router.post("/admin/destinations/{dest_id}/filter")
def destination_set_filter(
    dest_id: int,
    filter_mode: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dest = _dest_for_edit(db, dest_id, user)
    dest.filter_mode = FilterMode(filter_mode) if filter_mode != "inherit" else None
    db.commit()
    return _safe_redirect(f"/admin/projects/{dest.project_id}")


@router.post("/admin/destinations/{dest_id}/visibility")
def destination_set_visibility(
    dest_id: int,
    visibility: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dest = _dest_for_edit(db, dest_id, user)
    dest.visibility = "global" if visibility == "global" else "private"
    db.commit()
    return _safe_redirect(f"/admin/projects/{dest.project_id}")


@router.post("/admin/destinations/{dest_id}/toggle")
async def destination_toggle(
    dest_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dest = _dest_for_edit(db, dest_id, user)
    was_enabled = dest.enabled
    dest.enabled = not dest.enabled
    db.commit()
    if not was_enabled and dest.enabled and dest.type == DestinationType.telegram \
            and dest.bot and dest.bot.telegram_bot_token and dest.telegram_chat_id:
        return await _test_and_redirect(db, dest, dest.bot.telegram_bot_token, f"/admin/projects/{dest.project_id}")
    return _safe_redirect(f"/admin/projects/{dest.project_id}")


@router.post("/admin/destinations/{dest_id}/delete")
def destination_delete(
    dest_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dest = _dest_for_edit(db, dest_id, user)
    pid = dest.project_id
    db.delete(dest)
    db.commit()
    return _safe_redirect(f"/admin/projects/{pid}")


@router.post("/admin/projects/{project_id}/delete")
def project_delete(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404)
    db.delete(p)
    db.commit()
    return _safe_redirect("/admin")


@router.post("/admin/projects/{project_id}/filter")
def project_set_filter(
    project_id: int,
    filter_mode: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404)
    try:
        p.filter_mode = FilterMode(filter_mode)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid filter_mode")
    db.commit()
    return _safe_redirect(f"/admin/projects/{project_id}?saved=1")


@router.post("/admin/projects/{project_id}/set-default")
def project_set_default(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404)
    # Only one default at a time
    db.query(Project).filter(Project.id != project_id).update({Project.is_default: False})
    p.is_default = True
    db.commit()
    return _safe_redirect(f"/admin/projects/{project_id}?saved=1")


@router.post("/admin/projects/{project_id}/unset-default")
def project_unset_default(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404)
    p.is_default = False
    db.commit()
    return _safe_redirect(f"/admin/projects/{project_id}?saved=1")


@router.post("/admin/sync-coolify")
async def sync_coolify(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if not coolify_sync.is_configured():
        raise HTTPException(status_code=400, detail="COOLIFY_BASE_URL or COOLIFY_TOKEN not configured")
    result = await coolify_sync.sync_projects(db)
    total = len(result["created"]) + len(result["updated"])
    return _safe_redirect(f"/admin?synced={total}")


async def _test_and_redirect(db: Session, dest: Destination, bot_token: str, base_url: str) -> RedirectResponse:
    ok = await telegram_notifier.send(
        bot_token, dest.telegram_chat_id,
        "✅ <b>notify-proxy</b> — Destination erfolgreich eingerichtet."
    )
    dest.last_test_ok = ok
    db.commit()
    if ok:
        return _safe_redirect(f"{base_url}?saved=1")
    bot_info = await telegram_notifier.get_me(bot_token) or {}
    bot_user = bot_info.get("username", "")
    return _safe_redirect(f"{base_url}?start_bot={bot_user}&dest={dest.id}")


@router.post("/admin/destinations/{dest_id}/test")
async def destination_test(
    dest_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dest = _dest_for_edit(db, dest_id, user)
    if not dest.bot or not dest.bot.telegram_bot_token or not dest.telegram_chat_id:
        raise HTTPException(status_code=400, detail="no bot or chat_id")
    return await _test_and_redirect(db, dest, dest.bot.telegram_bot_token, f"/admin/projects/{dest.project_id}")


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    vbot_token = settings_store.get_setting(db, "verification_bot_token")
    bot_info = None
    if vbot_token:
        bot_info = await telegram_notifier.get_me(vbot_token)
    return templates.TemplateResponse(request, "settings.html", {
        "vbot_token_set": bool(vbot_token),
        "bot_info": bot_info,
        "saved": request.query_params.get("saved"),
        "user": user,
    })


@router.post("/admin/settings")
def settings_save(
    verification_bot_token: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    token = verification_bot_token.strip()
    if token:
        settings_store.set_setting(db, "verification_bot_token", token)
    return _safe_redirect("/admin/settings?saved=1")


@router.post("/admin/settings/clear-verification-bot")
def settings_clear_vbot(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    settings_store.delete_setting(db, "verification_bot_token")
    return _safe_redirect("/admin/settings?saved=1")


# ── Bots ──────────────────────────────────────────────────────────────────────

@router.get("/admin/bots", response_class=HTMLResponse)
def bots_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    bots = _visible_bots(db, user)
    for b in bots:
        b.editable = can_edit(user, b.owner_id)
    return templates.TemplateResponse(request, "bots.html", {"bots": bots, "user": user})


@router.get("/admin/bots/new", response_class=HTMLResponse)
def bot_new(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse(request, "bot_edit.html", {"bot": None, "user": user})


@router.post("/admin/bots/new")
def bot_create(
    name: str = Form(...),
    bot_type: str = Form(...),
    telegram_bot_token: str = Form(""),
    ntfy_url: str = Form(""),
    ntfy_token: str = Form(""),
    mattermost_url: str = Form(""),
    mattermost_token: str = Form(""),
    mattermost_team: str = Form(""),
    slack_url: str = Form(""),
    discord_url: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: str = Form(""),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    smtp_use_tls: str = Form("true"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    try:
        dtype = DestinationType(bot_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid type")
    if db.query(Bot).filter(Bot.name == name).first():
        raise HTTPException(status_code=400, detail="name already exists")
    bot = Bot(
        name=name,
        type=dtype,
        telegram_bot_token=telegram_bot_token or None,
        ntfy_url=ntfy_url or None,
        ntfy_token=ntfy_token or None,
        mattermost_url=mattermost_url or None,
        mattermost_token=mattermost_token or None,
        mattermost_team=mattermost_team or None,
        slack_url=slack_url or None,
        discord_url=discord_url or None,
        smtp_host=smtp_host or None,
        smtp_port=int(smtp_port) if smtp_port.strip().isdigit() else None,
        smtp_user=smtp_user or None,
        smtp_password=smtp_password or None,
        smtp_from=smtp_from or None,
        smtp_use_tls=(smtp_use_tls != "false"),
        owner_id=user.id,
    )
    db.add(bot)
    db.commit()
    return _safe_redirect("/admin/bots")


def _bot_for_edit(db: Session, bot_id: int, user: User) -> Bot:
    bot = db.query(Bot).filter(Bot.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404)
    _ensure_can_edit(user, bot.owner_id)
    return bot


@router.get("/admin/bots/{bot_id}", response_class=HTMLResponse)
def bot_edit_page(
    request: Request,
    bot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    bot = _bot_for_edit(db, bot_id, user)
    return templates.TemplateResponse(request, "bot_edit.html", {"bot": bot, "user": user})


@router.post("/admin/bots/{bot_id}")
def bot_update(
    bot_id: int,
    name: str = Form(...),
    telegram_bot_token: str = Form(""),
    ntfy_url: str = Form(""),
    ntfy_token: str = Form(""),
    mattermost_url: str = Form(""),
    mattermost_token: str = Form(""),
    mattermost_team: str = Form(""),
    slack_url: str = Form(""),
    discord_url: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: str = Form(""),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    smtp_use_tls: str = Form("true"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    bot = _bot_for_edit(db, bot_id, user)
    bot.name = name
    if bot.type == DestinationType.telegram:
        bot.telegram_bot_token = telegram_bot_token or bot.telegram_bot_token
    elif bot.type == DestinationType.ntfy:
        bot.ntfy_url = ntfy_url or bot.ntfy_url
        if ntfy_token:
            bot.ntfy_token = ntfy_token
    elif bot.type == DestinationType.mattermost:
        bot.mattermost_url = mattermost_url or bot.mattermost_url
        bot.mattermost_team = mattermost_team or bot.mattermost_team
        if mattermost_token:
            bot.mattermost_token = mattermost_token
    elif bot.type == DestinationType.slack:
        bot.slack_url = slack_url or bot.slack_url
    elif bot.type == DestinationType.discord:
        bot.discord_url = discord_url or bot.discord_url
    elif bot.type == DestinationType.email:
        bot.smtp_host = smtp_host or bot.smtp_host
        if smtp_port.strip().isdigit():
            bot.smtp_port = int(smtp_port)
        bot.smtp_user = smtp_user or bot.smtp_user
        if smtp_password:
            bot.smtp_password = smtp_password
        bot.smtp_from = smtp_from or bot.smtp_from
        bot.smtp_use_tls = (smtp_use_tls != "false")
    db.commit()
    return _safe_redirect("/admin/bots")


@router.post("/admin/bots/{bot_id}/visibility")
def bot_set_visibility(
    bot_id: int,
    visibility: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    bot = _bot_for_edit(db, bot_id, user)
    bot.visibility = "global" if visibility == "global" else "private"
    db.commit()
    return _safe_redirect("/admin/bots")


@router.post("/admin/bots/{bot_id}/delete")
def bot_delete(
    bot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    bot = _bot_for_edit(db, bot_id, user)
    db.delete(bot)
    db.commit()
    return _safe_redirect("/admin/bots")


# ── Chat Verification ─────────────────────────────────────────────────────────

@router.get("/admin/destinations/{dest_id}/verify", response_class=HTMLResponse)
async def destination_verify_page(
    request: Request,
    dest_id: int,
    code: str,
    not_found: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dest = _dest_for_edit(db, dest_id, user)
    bot_info = None
    vbot_token = settings_store.get_setting(db, "verification_bot_token")
    if vbot_token:
        bot_info = await telegram_notifier.get_me(vbot_token)
    return templates.TemplateResponse(request, "verify_chat.html", {
        "dest": dest,
        "code": code,
        "bot_info": bot_info,
        "not_found": bool(not_found),
        "user": user,
    })


@router.get("/admin/destinations/{dest_id}/verify-start")
def destination_verify_start(
    dest_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dest = _dest_for_edit(db, dest_id, user)
    if not settings_store.get_setting(db, "verification_bot_token"):
        raise HTTPException(status_code=400, detail="Verification bot not configured — go to Settings")
    existing = verification.get_by_dest(dest_id)
    code = existing[0] if existing else verification.create(dest.id, dest.telegram_chat_label or "")
    return _safe_redirect(f"/admin/destinations/{dest_id}/verify?code={code}")


@router.post("/admin/destinations/{dest_id}/verify-poll")
async def destination_verify_poll(
    dest_id: int,
    code: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dest = _dest_for_edit(db, dest_id, user)

    entry = verification.get_by_dest(dest_id)
    if not entry or entry[0] != code:
        return _safe_redirect(f"/admin/projects/{dest.project_id}")

    updates = await telegram_notifier.get_updates(settings_store.get_setting(db, "verification_bot_token"))

    for update in updates:
        msg = update.get("message") or update.get("channel_post") or {}
        text = msg.get("text", "")
        if code in text.upper():
            chat_id = str(msg["chat"]["id"])
            dest.telegram_chat_id = chat_id
            dest.enabled = True
            db.commit()
            verification.remove(code)

            # Test message from the actual notification bot
            if dest.bot and dest.bot.telegram_bot_token:
                return await _test_and_redirect(db, dest, dest.bot.telegram_bot_token, f"/admin/projects/{dest.project_id}")
            return _safe_redirect(f"/admin/projects/{dest.project_id}?saved=1")

    return _safe_redirect(f"/admin/destinations/{dest_id}/verify?code={code}&not_found=1")
