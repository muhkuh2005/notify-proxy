import logging

import httpx

logger = logging.getLogger(__name__)


async def send(
    ntfy_url: str,
    topic: str,
    title: str,
    message: str,
    token: str | None = None,
    priority: int | None = None,
    tags: list[str] | None = None,
    click: str | None = None,
    markdown: bool = False,
) -> bool:
    # ntfy JSON publishing API (POST to the base URL with topic in the body).
    # The body is UTF-8, so unicode titles (emoji/em-dash) work — unlike the
    # older "Title" HTTP header which httpx can't encode.
    url = ntfy_url.rstrip("/")
    payload: dict = {"topic": topic, "title": title, "message": message}
    if priority:
        payload["priority"] = priority
    if tags:
        payload["tags"] = tags
    if click:
        payload["click"] = click
    if markdown:
        payload["markdown"] = True
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
