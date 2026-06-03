"""Simple in-memory sliding-window rate limiting.

Per-process and per-IP — adequate for a single-instance deployment. Applied to
login/OAuth and webhook paths to blunt brute-force and spam. Disable or tune via
env (RATELIMIT_ENABLED, RATELIMIT_LOGIN_PER_MIN, RATELIMIT_WEBHOOK_PER_MIN).
"""
import os
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

WINDOW = 60.0
LOGIN_LIMIT = int(os.environ.get("RATELIMIT_LOGIN_PER_MIN", "10"))
WEBHOOK_LIMIT = int(os.environ.get("RATELIMIT_WEBHOOK_PER_MIN", "120"))

_hits: dict[str, deque] = defaultdict(deque)


def enabled() -> bool:
    return os.environ.get("RATELIMIT_ENABLED", "true").lower() not in ("false", "0", "no")


def _client_ip(request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _allow(key: str, limit: int, now: float) -> bool:
    dq = _hits[key]
    cutoff = now - WINDOW
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/login" or path.startswith("/auth/"):
            bucket, limit = "login", LOGIN_LIMIT
        elif path.startswith("/webhook/"):
            bucket, limit = "webhook", WEBHOOK_LIMIT
        else:
            return await call_next(request)

        if not _allow(f"{bucket}:{_client_ip(request)}", limit, time.monotonic()):
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        return await call_next(request)
