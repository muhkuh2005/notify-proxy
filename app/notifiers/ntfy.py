import logging
import httpx

logger = logging.getLogger(__name__)


async def send(ntfy_url: str, topic: str, title: str, message: str, token: str | None = None) -> bool:
    url = f"{ntfy_url.rstrip('/')}/{topic}"
    headers = {"Title": title}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, content=message.encode(), headers=headers)
            if not resp.is_success:
                logger.error("ntfy error %s: %s", resp.status_code, resp.text)
                return False
            return True
    except Exception as exc:
        logger.exception("ntfy send failed: %s", exc)
        return False
