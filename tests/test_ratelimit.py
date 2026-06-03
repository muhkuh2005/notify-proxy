"""Rate-limit window logic + middleware 429 behavior."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.ratelimit import WEBHOOK_LIMIT, RateLimitMiddleware, _allow, _client_ip, _hits


def test_allow_sliding_window():
    _hits.clear()
    for i in range(3):
        assert _allow("k", 3, 1000.0 + i * 0.1) is True
    assert _allow("k", 3, 1000.5) is False          # 4th within the 60s window
    assert _allow("k", 3, 1000.0 + 61) is True       # window slid → old hits expired


def test_client_ip_prefers_forwarded_for():
    req = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
                          client=SimpleNamespace(host="9.9.9.9"))
    assert _client_ip(req) == "1.2.3.4"
    req2 = SimpleNamespace(headers={}, client=SimpleNamespace(host="9.9.9.9"))
    assert _client_ip(req2) == "9.9.9.9"


def test_middleware_returns_429_over_limit():
    _hits.clear()

    async def ep(request):
        return PlainTextResponse("ok")

    sub = Starlette(routes=[Route("/webhook/x", ep, methods=["GET"]),
                            Route("/other", ep, methods=["GET"])])
    sub.add_middleware(RateLimitMiddleware)
    client = TestClient(sub)

    for _ in range(WEBHOOK_LIMIT):
        assert client.get("/webhook/x").status_code == 200
    assert client.get("/webhook/x").status_code == 429
    # unlimited path is never throttled
    assert client.get("/other").status_code == 200
