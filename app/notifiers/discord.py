import logging

import httpx

logger = logging.getLogger(__name__)


async def send(webhook_url: str, content: str) -> bool:
    """Post to a Discord webhook (channel is fixed by the webhook)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Discord rejects empty content and caps at 2000 chars.
            resp = await client.post(webhook_url, json={"content": content[:2000] or "(no content)"})
            if not resp.is_success:
                logger.error("discord error %s: %s", resp.status_code, resp.text)
                return False
            return True
    except Exception as exc:
        logger.exception("discord send failed: %s", exc)
        return False
