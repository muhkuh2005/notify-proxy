import logging
import time

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

_getme_cache: dict[str, tuple[dict, float]] = {}  # token -> (result, expires_monotonic)


async def resolve_chat_id(bot_token: str, username: str) -> str | None:
    """Resolve @username or chat name to numeric chat ID via getChat."""
    url = f"{TELEGRAM_API}/bot{bot_token}/getChat"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params={"chat_id": username})
            if resp.is_success:
                data = resp.json()
                if data.get("ok"):
                    return str(data["result"]["id"])
            logger.warning("getChat failed for %s: %s %s", username, resp.status_code, resp.text)
    except Exception as exc:
        logger.exception("resolve_chat_id failed: %s", exc)
    return None


async def get_me(bot_token: str) -> dict | None:
    cached = _getme_cache.get(bot_token)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    url = f"{TELEGRAM_API}/bot{bot_token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.is_success:
                data = resp.json()
                if data.get("ok"):
                    result = data["result"]
                    if len(_getme_cache) >= 50:
                        oldest = min(_getme_cache, key=lambda k: _getme_cache[k][1])
                        del _getme_cache[oldest]
                    _getme_cache[bot_token] = (result, time.monotonic() + 60)
                    return result
    except Exception as exc:
        logger.exception("getMe failed: %s", exc)
    return None


async def get_updates(bot_token: str) -> list[dict]:
    url = f"{TELEGRAM_API}/bot{bot_token}/getUpdates"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params={"limit": 100, "timeout": 0})
            if resp.is_success:
                data = resp.json()
                if data.get("ok"):
                    return data["result"]
    except Exception as exc:
        logger.exception("getUpdates failed: %s", exc)
    return []


async def send(bot_token: str, chat_id: str, text: str) -> bool:
    ok, _ = await send_detail(bot_token, chat_id, text)
    return ok


async def send_detail(bot_token: str, chat_id: str, text: str) -> tuple[bool, str | None]:
    """Send a message; return ``(ok, error)``. On failure ``error`` is Telegram's
    own ``description`` (e.g. "Forbidden: bot was blocked by the user",
    "Bad Request: chat not found") so callers can show the real cause instead of
    guessing. ``send`` wraps this for the common bool-only case."""
    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            if not resp.is_success:
                logger.error("Telegram error %s: %s", resp.status_code, resp.text)
                desc = ""
                try:
                    desc = resp.json().get("description", "")
                except Exception:
                    pass
                return False, f"{resp.status_code} {desc or resp.text}".strip()
            return True, None
    except Exception as exc:
        logger.exception("Telegram send failed: %s", exc)
        return False, str(exc)
