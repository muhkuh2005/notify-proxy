import logging

import httpx

logger = logging.getLogger(__name__)


async def send(ntfy_url: str, topic: str, title: str, message: str, token: str | None = None) -> bool:
    # Use ntfy's JSON publishing API (POST to the base URL with topic in the body).
    # The older "Title" HTTP header can't carry non-ASCII (emoji/em-dash) titles —
    # httpx refuses to encode them — so titles like "✅ app: finished" would fail.
    url = ntfy_url.rstrip("/")
    payload = {"topic": topic, "title": title, "message": message}
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if not resp.is_success:
                logger.error("ntfy error %s: %s", resp.status_code, resp.text)
                return False
            return True
    except Exception as exc:
        logger.exception("ntfy send failed: %s", exc)
        return False
