"""Notifier transport tests with mocked HTTP (respx) and SMTP (monkeypatch)."""
import asyncio

import httpx
import respx

from app.notifiers import discord, mattermost, ntfy, slack, telegram
from app.notifiers import email as email_notifier


def _run(coro):
    return asyncio.run(coro)


# ── ntfy ────────────────────────────────────────────────────────────────────
def test_ntfy_success_with_unicode_title():
    # Regression: emoji/em-dash titles must work (they're sent in the JSON body,
    # not an HTTP header which can't carry non-ASCII).
    with respx.mock:
        route = respx.post("https://ntfy.sh").mock(return_value=httpx.Response(200))
        assert _run(ntfy.send("https://ntfy.sh", "alerts", "✅ app — finished", "body")) is True
        import json
        sent = json.loads(route.calls.last.request.content)
        assert sent["topic"] == "alerts" and sent["title"] == "✅ app — finished"


def test_ntfy_includes_priority_tags_click_markdown():
    import json
    with respx.mock:
        route = respx.post("https://ntfy.sh").mock(return_value=httpx.Response(200))
        _run(ntfy.send("https://ntfy.sh", "t", "Ti", "body",
                       priority=4, tags=["rotating_light"], click="https://x", markdown=True))
        sent = json.loads(route.calls.last.request.content)
        assert sent["priority"] == 4
        assert sent["tags"] == ["rotating_light"]
        assert sent["click"] == "https://x"
        assert sent["markdown"] is True


def test_ntfy_failure():
    with respx.mock:
        respx.post("https://ntfy.sh").mock(return_value=httpx.Response(403, text="forbidden"))
        assert _run(ntfy.send("https://ntfy.sh", "alerts", "T", "body")) is False


def test_ntfy_sends_auth_header_with_token():
    with respx.mock:
        route = respx.post("https://ntfy.sh").mock(return_value=httpx.Response(200))
        _run(ntfy.send("https://ntfy.sh", "alerts", "T", "body", token="secret"))
        assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


# ── Slack / Discord ─────────────────────────────────────────────────────────
def test_slack_success_and_failure():
    with respx.mock:
        respx.post("https://hooks.slack.com/x").mock(return_value=httpx.Response(200, text="ok"))
        assert _run(slack.send("https://hooks.slack.com/x", "hi")) is True
    with respx.mock:
        respx.post("https://hooks.slack.com/x").mock(return_value=httpx.Response(500))
        assert _run(slack.send("https://hooks.slack.com/x", "hi")) is False


def test_discord_success_and_failure():
    with respx.mock:
        respx.post("https://discord.com/api/webhooks/x").mock(return_value=httpx.Response(204))
        assert _run(discord.send("https://discord.com/api/webhooks/x", "hi")) is True
    with respx.mock:
        respx.post("https://discord.com/api/webhooks/x").mock(return_value=httpx.Response(400))
        assert _run(discord.send("https://discord.com/api/webhooks/x", "hi")) is False


def test_discord_truncates_long_content():
    with respx.mock:
        route = respx.post("https://discord.com/api/webhooks/x").mock(return_value=httpx.Response(204))
        _run(discord.send("https://discord.com/api/webhooks/x", "A" * 5000))
        assert len(route.calls.last.request.read()) < 4000  # body trimmed to ~2000 chars


# ── Telegram ────────────────────────────────────────────────────────────────
def test_telegram_send():
    with respx.mock:
        respx.route(host="api.telegram.org", method="POST").mock(return_value=httpx.Response(200, json={"ok": True}))
        assert _run(telegram.send("123:abc", "42", "hello")) is True
    with respx.mock:
        respx.route(host="api.telegram.org", method="POST").mock(return_value=httpx.Response(400, json={"ok": False}))
        assert _run(telegram.send("123:abc", "42", "hello")) is False


def test_telegram_resolve_chat_id():
    with respx.mock:
        respx.route(host="api.telegram.org", method="GET").mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {"id": -100123}})
        )
        assert _run(telegram.resolve_chat_id("123:abc", "@chan")) == "-100123"


# ── Mattermost ──────────────────────────────────────────────────────────────
def test_mattermost_send():
    with respx.mock:
        respx.post("https://mm.test/api/v4/posts").mock(return_value=httpx.Response(201, json={"id": "p"}))
        assert _run(mattermost.send("https://mm.test", "tok", "chan", "msg")) is True


def test_mattermost_resolve_dm_channel():
    with respx.mock:
        respx.get("https://mm.test/api/v4/users/me").mock(
            return_value=httpx.Response(200, json={"id": "me0000000000000000000000aa"}))
        respx.get("https://mm.test/api/v4/users/username/max").mock(
            return_value=httpx.Response(200, json={"id": "mx0000000000000000000000aa"}))
        respx.post("https://mm.test/api/v4/channels/direct").mock(
            return_value=httpx.Response(201, json={"id": "dm0000000000000000000000aa"}))
        cid = _run(mattermost.resolve_channel_id("https://mm.test", "tok", "@max"))
        assert cid == "dm0000000000000000000000aa"


def test_mattermost_resolve_channel_needs_team():
    with respx.mock:
        # plain name with no team -> cannot resolve
        assert _run(mattermost.resolve_channel_id("https://mm.test", "tok", "general", team=None)) is None


# ── Email (SMTP) ────────────────────────────────────────────────────────────
class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.tls = False
        self.logged_in = None
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


def test_email_send_success(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    ok = _run(email_notifier.send(
        "smtp.x", 587, "u", "p", "from@x", True, "to@x", "Subj", "Body"))
    assert ok is True
    inst = _FakeSMTP.instances[-1]
    assert inst.tls is True and inst.logged_in == ("u", "p")
    assert inst.sent[0]["To"] == "to@x" and inst.sent[0]["Subject"] == "Subj"


def test_email_send_failure(monkeypatch):
    class _BadSMTP(_FakeSMTP):
        def send_message(self, msg):
            raise OSError("smtp down")
    monkeypatch.setattr("smtplib.SMTP", _BadSMTP)
    assert _run(email_notifier.send("smtp.x", 587, None, None, "from@x", False, "to@x", "S", "B")) is False
