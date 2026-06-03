import logging

import httpx

logger = logging.getLogger(__name__)


async def send(webhook_url: str, text: str) -> bool:
    """Post to a Slack incoming webhook (channel is fixed by the webhook)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={"text": text})
            if not resp.is_success:
                logger.error("slack error %s: %s", resp.status_code, resp.text)
                return False
            return True
    except Exception as exc:
        logger.exception("slack send failed: %s", exc)
        return False
