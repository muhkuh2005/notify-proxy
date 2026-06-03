import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Destination, DestinationType, FilterMode, Project
from ..notifiers import telegram, ntfy, mattermost, slack, discord
from ..notifiers import email as email_notifier

logger = logging.getLogger(__name__)
router = APIRouter()

COOLIFY_INCOMING_TOKEN: str | None = os.environ.get("COOLIFY_INCOMING_TOKEN") or None


_ERROR_STATUSES = {"failed", "failed-with-errors", "error", "cancelled", "cancelled-by-user"}
_ERROR_WORDS = {"fail", "failed", "error", "critical", "crash", "down", "timeout"}


def _is_error(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status", "")).lower()
    if status in _ERROR_STATUSES:
        return True
    event_type = str(payload.get("type", "")).lower()
    message = str(payload.get("message", "")).lower()
    combined = f"{event_type} {message}"
    return any(w in combined for w in _ERROR_WORDS)


def _camel_to_words(s: str) -> str:
    import re
    return re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)


def _format_message(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (title, body) from a Coolify webhook payload."""
    logger.debug("payload keys: %s | full: %s", list(payload.keys()), payload)

    event_type = payload.get("type", "")
    message = payload.get("message", "")

    # Server-level events (docker cleanup, backups, etc.) have server_name/server_uuid
    # but no application_name/application_uuid
    is_server_event = bool(
        payload.get("server_name") or payload.get("server_uuid")
    ) and not payload.get("application_name") and not payload.get("application_uuid")

    if is_server_event:
        server = payload.get("server_name") or payload.get("server_uuid", "server")
        url = payload.get("url", "")
        event_label = _camel_to_words(event_type) if event_type else message or "Server event"

        succeeded = any(w in event_label.lower() for w in ("success", "succeeded", "finished"))
        failed = any(w in event_label.lower() for w in ("fail", "error"))
        emoji = "✅" if succeeded else ("❌" if failed else "ℹ️")

        title = f"{emoji} {server}: {event_label}"
        parts = [f"<b>{server}</b>  {emoji}"]
        if event_label and event_label != message:
            parts.append(f"<code>{event_label}</code>")
        if url:
            parts.append(f'<a href="{url}">{url}</a>')
        if message:
            parts.append(f"\n{message}")
        return title, "\n".join(parts)

    # Application-level events
    status = payload.get("status", event_type or "unknown")
    app_name = payload.get("application_name") or payload.get("name") or "unknown app"
    app_url = payload.get("application_url") or payload.get("url", "")
    project = payload.get("project_name", "")
    env = payload.get("environment_name", "")

    status_emoji = {
        "finished": "✅",
        "success": "✅",
        "failed": "❌",
        "failed-with-errors": "❌",
        "error": "❌",
        "cancelled": "⚠️",
        "cancelled-by-user": "⚠️",
        "in_progress": "🔄",
        "running": "🔄",
    }.get(str(status).lower(), "ℹ️")

    display_name = f"{project} — {app_name}" if project else app_name
    title = f"{status_emoji} {display_name}: {status}"
    parts = [f"<b>{display_name}</b>  {status_emoji} <code>{status}</code>"]
    if env:
        parts.append(f"Env: {env}")
    if app_url:
        parts.append(f'URL: <a href="{app_url}">{app_url}</a>')
    if message:
        parts.append(f"\n{message}")

    return title, "\n".join(parts)


@router.post("/webhook/{token}")
async def receive_webhook(token: str, request: Request, db: Session = Depends(get_db)):
    project = (
        db.query(Project)
        .options(selectinload(Project.destinations).selectinload(Destination.bot))
        .filter(Project.token == token)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="unknown token")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    logger.info("webhook project=%s payload_keys=%s", project.name, list(payload.keys()))
    title, body = _format_message(payload)

    results = await _dispatch(project, payload, db)
    return {"project": project.name, "results": results}


def _strip_html(text: str) -> str:
    import re
    text = text.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
    text = re.sub(r'<a href="[^"]*">([^<]*)</a>', r"\1", text)
    return re.sub(r"<[^>]+>", "", text)


def _to_markdown(text: str) -> str:
    """Convert the internal HTML-ish body to Markdown (for Mattermost)."""
    import re
    text = text.replace("<b>", "**").replace("</b>", "**")
    text = text.replace("<code>", "`").replace("</code>", "`")
    text = re.sub(r'<a href="([^"]*)">([^<]*)</a>', r"[\2](\1)", text)
    return re.sub(r"<[^>]+>", "", text)


async def _dispatch(project: Project, payload: dict, db: Session) -> list[dict]:
    title, body = _format_message(payload)
    is_err = _is_error(payload)

    async def _send_dest(dest) -> dict:
        effective = dest.filter_mode if dest.filter_mode is not None else project.filter_mode
        if effective == FilterMode.off:
            logger.debug("project=%s dest=%s filter=off — dropped", project.name, dest.id)
            return {"dest_id": dest.id, "type": dest.type, "ok": None, "skipped": True}
        if effective == FilterMode.errors_only and not is_err:
            logger.debug("project=%s dest=%s filter=errors_only — not an error, dropped", project.name, dest.id)
            return {"dest_id": dest.id, "type": dest.type, "ok": None, "skipped": True}
        ok = False
        bot = dest.bot

        if bot and dest.type == DestinationType.telegram:
            if bot.telegram_bot_token and dest.telegram_chat_id:
                ok = await telegram.send(bot.telegram_bot_token, dest.telegram_chat_id, body)
        elif bot and dest.type == DestinationType.ntfy:
            if bot.ntfy_url and dest.ntfy_topic:
                ok = await ntfy.send(bot.ntfy_url, dest.ntfy_topic, title, _strip_html(body), bot.ntfy_token)
        elif bot and dest.type == DestinationType.mattermost:
            if bot.mattermost_url and bot.mattermost_token and dest.mattermost_channel_id:
                ok = await mattermost.send(
                    bot.mattermost_url, bot.mattermost_token,
                    dest.mattermost_channel_id, _to_markdown(body),
                )
        elif bot and dest.type == DestinationType.slack:
            if bot.slack_url:
                ok = await slack.send(bot.slack_url, _to_markdown(body))
        elif bot and dest.type == DestinationType.discord:
            if bot.discord_url:
                ok = await discord.send(bot.discord_url, _to_markdown(body))
        elif bot and dest.type == DestinationType.email:
            if bot.smtp_host and bot.smtp_from and dest.email_to:
                ok = await email_notifier.send(
                    bot.smtp_host, bot.smtp_port, bot.smtp_user, bot.smtp_password,
                    bot.smtp_from, bot.smtp_use_tls, dest.email_to, title, _strip_html(body),
                )
        # Legacy fallback: inline credentials on destination
        elif dest.type == DestinationType.telegram and dest.telegram_bot_token and dest.telegram_chat_id:
            ok = await telegram.send(dest.telegram_bot_token, dest.telegram_chat_id, body)
        elif dest.type == DestinationType.ntfy and dest.ntfy_url and dest.ntfy_topic:
            ok = await ntfy.send(dest.ntfy_url, dest.ntfy_topic, title, _strip_html(body), dest.ntfy_token)

        return {"dest_id": dest.id, "type": dest.type, "ok": ok}

    active = [dest for dest in project.destinations if dest.enabled]
    all_results = await asyncio.gather(*[_send_dest(d) for d in active], return_exceptions=True)
    return [r for r in all_results if isinstance(r, dict) and not r.get("skipped")]


@router.post("/webhook/coolify/{token}")
async def receive_coolify_webhook(token: str, request: Request, db: Session = Depends(get_db)):
    # Require a configured incoming token. Without one, this central endpoint
    # would accept any token and act as an open relay for anyone who knows the URL.
    if not COOLIFY_INCOMING_TOKEN or token != COOLIFY_INCOMING_TOKEN:
        raise HTTPException(status_code=404, detail="unknown token")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    app_uuid = payload.get("application_uuid", "")
    app_name = payload.get("application_name", payload.get("name", ""))
    server_uuid = payload.get("server_uuid", "")

    # Coolify sends periodic empty pings (server_uuid only, no type/status/app fields).
    # These carry no event data and would always route to default or get dropped — skip silently.
    if not app_uuid and not app_name and not payload.get("type") and not payload.get("status"):
        logger.debug("coolify ping server_uuid=%s — empty payload, ignored", server_uuid)
        return {"routed": False, "reason": "empty ping"}

    logger.info("coolify webhook app_uuid=%s app_name=%s server_uuid=%s", app_uuid, app_name, server_uuid)

    # Route by application UUID first
    project = None
    _eager = selectinload(Project.destinations).selectinload(Destination.bot)
    if app_uuid:
        project = db.query(Project).options(_eager).filter(Project.coolify_uuid == app_uuid).first()
    # Fallback: match by app name
    if not project and app_name:
        project = db.query(Project).options(_eager).filter(Project.name == app_name).first()
    # Server events: route by server_uuid
    if not project and server_uuid and not app_uuid:
        project = db.query(Project).options(_eager).filter(Project.coolify_server_uuid == server_uuid).first()

    if not project:
        project = db.query(Project).options(_eager).filter(Project.is_default == True).first()  # noqa: E712
        if project:
            logger.info("coolify webhook: no match for uuid=%s name=%s — using default project %s", app_uuid, app_name, project.name)
        else:
            logger.warning("coolify webhook: no project for uuid=%s name=%s, no default set — dropped", app_uuid, app_name)
            return {"routed": False, "reason": "no matching project and no default configured"}

    results = await _dispatch(project, payload, db)
    return {"routed": True, "project": project.name, "results": results}
