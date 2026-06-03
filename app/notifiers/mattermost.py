"""Mattermost notifier via the REST API v4.

Works with any token in the `Authorization: Bearer` header — a personal access
token (messages appear as the token owner) or a dedicated bot-account token
(neutral bot identity). The code path is identical; only the token differs.
"""
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Mattermost object IDs are 26-char lowercase base32 (a-z, 0-9).
_ID_RE = re.compile(r"^[a-z0-9]{26}$")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _is_raw_id(target: str) -> bool:
    return bool(_ID_RE.match(target))


async def resolve_channel_id(
    base_url: str, token: str, target: str, team: str | None = None
) -> str | None:
    """Resolve a target to a channel_id.

    - "@username"      -> direct-message channel between the token owner and the user
    - "channel-name"   -> channel by name within `team`
    - 26-char raw id   -> used as-is
    """
    if not base_url or not token or not target:
        return None
    base = base_url.rstrip("/")
    target = target.strip()

    if _is_raw_id(target):
        return target

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if target.startswith("@"):
                username = target[1:]
                me = await client.get(f"{base}/api/v4/users/me", headers=_headers(token))
                user = await client.get(
                    f"{base}/api/v4/users/username/{username}", headers=_headers(token)
                )
                if not me.is_success or not user.is_success:
                    logger.error(
                        "mattermost user lookup failed: me=%s user=%s",
                        me.status_code, user.status_code,
                    )
                    return None
                dm = await client.post(
                    f"{base}/api/v4/channels/direct",
                    json=[me.json()["id"], user.json()["id"]],
                    headers=_headers(token),
                )
                if dm.is_success:
                    return dm.json()["id"]
                logger.error("mattermost create DM failed %s: %s", dm.status_code, dm.text)
                return None

            # Channel by name — needs a team
            if not team:
                logger.error("mattermost channel '%s' needs a team set on the bot", target)
                return None
            channel = target.lstrip("#~")
            resp = await client.get(
                f"{base}/api/v4/teams/name/{team}/channels/name/{channel}",
                headers=_headers(token),
            )
            if resp.is_success:
                return resp.json()["id"]
            logger.error("mattermost channel lookup failed %s: %s", resp.status_code, resp.text)
            return None
    except Exception as exc:
        logger.exception("mattermost resolve_channel_id failed: %s", exc)
        return None


async def get_me(base_url: str, token: str) -> dict | None:
    """Return the token owner's user object (for connection/credential checks)."""
    if not base_url or not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/api/v4/users/me", headers=_headers(token)
            )
            if resp.is_success:
                return resp.json()
            logger.error("mattermost get_me %s: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.exception("mattermost get_me failed: %s", exc)
    return None


async def send(base_url: str, token: str, channel_id: str, message: str) -> bool:
    url = f"{base_url.rstrip('/')}/api/v4/posts"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={"channel_id": channel_id, "message": message},
                headers=_headers(token),
            )
            if not resp.is_success:
                logger.error("mattermost send %s: %s", resp.status_code, resp.text)
                return False
            return True
    except Exception as exc:
        logger.exception("mattermost send failed: %s", exc)
        return False
