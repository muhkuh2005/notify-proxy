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
from ..services import coolify_sync

logger = logging.getLogger(__name__)
router = APIRouter()

COOLIFY_INCOMING_TOKEN: str | None = os.environ.get("COOLIFY_INCOMING_TOKEN") or None


_ERROR_STATUSES = {"failed", "failed-with-errors", "error", "cancelled", "cancelled-by-user"}
_ERROR_WORDS = {"fail", "failed", "error", "critical", "crash", "down", "timeout"}


def _derive_status(payload: dict[str, Any], event_type: str = "") -> str:
    """Normalise a Coolify payload to a status word.

    Coolify deployment webhooks carry NO ``status`` and NO ``type`` field.
    They expose ``event`` ("deployment_success" / "deployment_failed") plus a
    ``success`` bool. Older/other shapes may still send ``status``/``type``, so
    fall through every known signal before giving up with "unknown".
    """
    explicit = payload.get("status")
    if explicit:
        return str(explicit)

    event = str(payload.get("event") or event_type or "")
    low = event.lower()
    if low.endswith(("_success", "_succeeded", "_finished")):
        return "success"
    if low.endswith(("_failed", "_failure", "_error")):
        return "failed"

    success = payload.get("success")
    if isinstance(success, bool):
        return "success" if success else "failed"

    message = str(payload.get("message", "")).lower()
    if any(w in message for w in ("success", "succeeded", "deployed", "finished")):
        return "success"
    if any(w in message for w in ("fail", "error")):
        return "failed"

    return event or "unknown"


def _is_error(payload: dict[str, Any]) -> bool:
    if _derive_status(payload).lower() in _ERROR_STATUSES:
        return True
    if str(payload.get("status", "")).lower() in _ERROR_STATUSES:
        return True
    event_type = str(payload.get("type") or payload.get("event") or "").lower()
    message = str(payload.get("message", "")).lower()
    combined = f"{event_type} {message}"
    return any(w in combined for w in _ERROR_WORDS)


def _camel_to_words(s: str) -> str:
    import re
    return re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)


_COMMIT_KEYS = ("commit_sha", "git_commit_sha", "commit", "sha", "revision", "version", "tag")


def _short_commit(value: str) -> str:
    """Shorten a long hex SHA to 8 chars; leave tags/versions untouched."""
    s = str(value).strip()
    if len(s) >= 12 and all(c in "0123456789abcdefABCDEF" for c in s):
        return s[:8]
    return s


def _extract_commit(payload: dict[str, Any]) -> str | None:
    """Pull a commit/version straight from the payload, if Coolify sent one."""
    for key in _COMMIT_KEYS:
        v = payload.get(key)
        if v and str(v).strip().lower() != "head":
            return _short_commit(v)
    return None


_NOISE_TAGS = {"latest", "main", "master", "head", "stable", "edge", ""}


async def _resolve_version(payload: dict[str, Any]) -> tuple[str, bool] | None:
    """Resolve a version marker for the notice as ``(text, is_commit)``.

    Order: commit from payload (free) -> Coolify deployment record by
    deployment_uuid (real SHA for git-push deploys) -> the deployment's docker
    image tag as a fallback "version" (webhook/API deploys store commit "HEAD").
    Noise tags like latest/main are ignored. Returns None when nothing useful.
    """
    direct = _extract_commit(payload)
    if direct:
        return direct, True
    deployment_uuid = payload.get("deployment_uuid") or ""
    if deployment_uuid:
        info = await coolify_sync.get_deployment_info(deployment_uuid)
        if info:
            if info.get("commit"):
                return _short_commit(info["commit"]), True
            tag = info.get("image_tag", "")
            if tag.lower() not in _NOISE_TAGS:
                return tag, False
    return None


def _format_message(payload: dict[str, Any], version: tuple[str, bool] | None = None, fallback_name: str = "") -> tuple[str, str]:
    """Return (title, body) from a Coolify webhook payload.

    ``version`` is an already-resolved ``(text, is_commit)`` pair (see
    :func:`_resolve_version`); when present it is added as a body line labelled
    "Commit" or "Version".
    """
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

    # Application-level events.
    # Coolify deployment webhooks use real keys: event/success/message,
    # application_name, project, environment, deployment_url, fqdn.
    # Keep the older guessed keys as fallbacks for other event shapes.
    status = _derive_status(payload, event_type)
    app_name = payload.get("application_name") or payload.get("name") or fallback_name or "unknown app"
    app_url = (payload.get("deployment_url") or payload.get("fqdn")
               or payload.get("application_url") or payload.get("url", ""))
    project = payload.get("project") or payload.get("project_name", "")
    env = payload.get("environment") or payload.get("environment_name", "")

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
    if version:
        text, is_commit = version
        parts.append(f"{'Commit' if is_commit else 'Version'}: <code>{text}</code>")
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


_RETRY_ATTEMPTS = max(1, int(os.environ.get("NOTIFY_RETRY_ATTEMPTS", "3")))


class PermanentSendError(Exception):
    """A send failed for a reason that retrying can never fix (bad chat id,
    blocked bot). Raised by a factory to stop retries and disable the dest."""


# Telegram errors that mean the destination is dead, not flaky.
_TG_PERMANENT = (
    "chat not found", "bot was blocked", "user is deactivated",
    "can't initiate conversation", "chat_id is empty", "peer_id_invalid",
    "group chat was upgraded",
)


def _tg_permanent(err: str | None) -> bool:
    e = (err or "").lower()
    return any(p in e for p in _TG_PERMANENT)


def _telegram_factory(bot_token: str, chat_id: str, body: str):
    """Send factory that raises PermanentSendError on a dead-destination error
    so the dispatcher disables the dest instead of retrying it every webhook."""
    async def _send() -> bool:
        ok, err = await telegram.send_detail(bot_token, chat_id, body)
        if not ok and _tg_permanent(err):
            raise PermanentSendError(err or "telegram permanent error")
        return ok
    return _send


async def _retry_send(label: str, factory) -> bool:
    """Call an async send factory up to _RETRY_ATTEMPTS times with exponential
    backoff. On exhaustion, log an ERROR so dropped notifications are findable.
    PermanentSendError is never retried — it propagates to the caller."""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            if await factory():
                if attempt > 1:
                    logger.info("notify recovered on attempt %d/%d: %s", attempt, _RETRY_ATTEMPTS, label)
                return True
            logger.warning("notify attempt %d/%d failed: %s", attempt, _RETRY_ATTEMPTS, label)
        except PermanentSendError:
            raise
        except Exception as exc:
            logger.warning("notify attempt %d/%d errored (%s): %s", attempt, _RETRY_ATTEMPTS, exc, label)
        if attempt < _RETRY_ATTEMPTS:
            await asyncio.sleep(min(2 ** (attempt - 1), 10))
    logger.error("notify DROPPED after %d attempts: %s", _RETRY_ATTEMPTS, label)
    return False


def _dest_target(dest) -> str:
    return (dest.telegram_chat_label or dest.telegram_chat_id or dest.ntfy_topic
            or dest.mattermost_target or dest.email_to or "-")


async def _dispatch(project: Project, payload: dict, db: Session) -> list[dict]:
    version = await _resolve_version(payload)
    title, body = _format_message(payload, version, fallback_name=project.name)
    is_err = _is_error(payload)

    async def _send_dest(dest) -> dict:
        effective = dest.filter_mode if dest.filter_mode is not None else project.filter_mode
        if effective == FilterMode.off:
            logger.debug("project=%s dest=%s filter=off — dropped", project.name, dest.id)
            return {"dest_id": dest.id, "type": dest.type, "ok": None, "skipped": True}
        if effective == FilterMode.errors_only and not is_err:
            logger.debug("project=%s dest=%s filter=errors_only — not an error, dropped", project.name, dest.id)
            return {"dest_id": dest.id, "type": dest.type, "ok": None, "skipped": True}
        bot = dest.bot
        factory = None

        if bot and dest.type == DestinationType.telegram:
            if bot.telegram_bot_token and dest.telegram_chat_id:
                factory = _telegram_factory(bot.telegram_bot_token, dest.telegram_chat_id, body)
        elif bot and dest.type == DestinationType.ntfy:
            if bot.ntfy_url and dest.ntfy_topic:
                prio = dest.ntfy_priority or (4 if is_err else 3)
                tags = ["rotating_light"] if is_err else ["white_check_mark"]
                click = (payload.get("fqdn") or payload.get("application_url")
                         or payload.get("deployment_url") or payload.get("url") or None)
                factory = lambda: ntfy.send(
                    bot.ntfy_url, dest.ntfy_topic, title, _to_markdown(body), bot.ntfy_token,
                    priority=prio, tags=tags, click=click, markdown=True,
                )
        elif bot and dest.type == DestinationType.mattermost:
            if bot.mattermost_url and bot.mattermost_token and dest.mattermost_channel_id:
                factory = lambda: mattermost.send(
                    bot.mattermost_url, bot.mattermost_token,
                    dest.mattermost_channel_id, _to_markdown(body),
                )
        elif bot and dest.type == DestinationType.slack:
            if bot.slack_url:
                factory = lambda: slack.send(bot.slack_url, _to_markdown(body))
        elif bot and dest.type == DestinationType.discord:
            if bot.discord_url:
                factory = lambda: discord.send(bot.discord_url, _to_markdown(body))
        elif bot and dest.type == DestinationType.email:
            if bot.smtp_host and bot.smtp_from and dest.email_to:
                factory = lambda: email_notifier.send(
                    bot.smtp_host, bot.smtp_port, bot.smtp_user, bot.smtp_password,
                    bot.smtp_from, bot.smtp_use_tls, dest.email_to, title, _strip_html(body),
                )
        # Legacy fallback: inline credentials on destination
        elif dest.type == DestinationType.telegram and dest.telegram_bot_token and dest.telegram_chat_id:
            factory = _telegram_factory(dest.telegram_bot_token, dest.telegram_chat_id, body)
        elif dest.type == DestinationType.ntfy and dest.ntfy_url and dest.ntfy_topic:
            factory = lambda: ntfy.send(dest.ntfy_url, dest.ntfy_topic, title, _strip_html(body), dest.ntfy_token)

        if factory is None:
            logger.warning("project=%s dest=%s type=%s — no usable config, dropped",
                           project.name, dest.id, dest.type)
            return {"dest_id": dest.id, "type": dest.type, "ok": False}

        label = f"project={project.name} dest={dest.id} type={dest.type} target={_dest_target(dest)}"
        try:
            ok = await _retry_send(label, factory)
        except PermanentSendError as exc:
            logger.error("project=%s dest=%s permanently undeliverable (%s) — disabling",
                         project.name, dest.id, exc)
            return {"dest_id": dest.id, "type": dest.type, "ok": False, "disable": True}
        return {"dest_id": dest.id, "type": dest.type, "ok": ok}

    active = [dest for dest in project.destinations if dest.enabled]
    all_results = await asyncio.gather(*[_send_dest(d) for d in active], return_exceptions=True)
    results = [r for r in all_results if isinstance(r, dict)]

    dead = [r["dest_id"] for r in results if r.get("disable")]
    if dead:
        db.query(Destination).filter(Destination.id.in_(dead)).update(
            {Destination.enabled: False}, synchronize_session=False)
        db.commit()

    return [r for r in results if not r.get("skipped")]


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
    if (not app_uuid and not app_name and not payload.get("type")
            and not payload.get("status") and not payload.get("event")
            and payload.get("success") is None):
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
