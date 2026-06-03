"""Notification dispatch (filter modes) and Coolify webhook routing."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, get_db
from app.main import app
from app.models import Bot, Destination, DestinationType, FilterMode, Project
from app.routes import webhook


@pytest.fixture
def db():
    s = SessionLocal()
    # Route handlers must share this exact session (one SQLite connection).
    app.dependency_overrides[get_db] = lambda: s
    try:
        yield s
    finally:
        app.dependency_overrides.pop(get_db, None)
        s.rollback()
        s.close()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def ok(*a, **k):
        return True
    for mod in ("ntfy", "telegram", "mattermost", "slack", "discord", "email"):
        monkeypatch.setattr(f"app.notifiers.{mod}.send", ok)


@pytest.fixture(autouse=True)
def clean(db):
    yield
    db.query(Destination).delete()
    db.query(Bot).delete()
    db.query(Project).delete()
    db.commit()


def _bot(db, name):
    b = Bot(name=name, type=DestinationType.ntfy, ntfy_url="https://n")
    db.add(b); db.commit(); db.refresh(b)
    return b


def _project(db, name, fm=FilterMode.all, **kw):
    p = Project(name=name, filter_mode=fm, **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _dest(db, p, b, enabled=True, fm=None):
    d = Destination(project_id=p.id, bot_id=b.id, type=DestinationType.ntfy,
                    ntfy_topic="t", enabled=enabled, filter_mode=fm)
    db.add(d); db.commit(); db.refresh(d)
    return d


def _run(pid, payload, db):
    db.expire_all()
    p = db.query(Project).filter(Project.id == pid).first()
    return asyncio.run(webhook._dispatch(p, payload, db))


CLEAN = {"status": "finished"}
ERROR = {"status": "failed"}


# ── Filter modes ────────────────────────────────────────────────────────────
def test_dispatch_all_sends_to_all_enabled(db):
    p = _project(db, "d-all"); b = _bot(db, "d-all-b")
    _dest(db, p, b); _dest(db, p, b)
    assert len(_run(p.id, CLEAN, db)) == 2


def test_dispatch_errors_only_filters_clean(db):
    p = _project(db, "d-eo", fm=FilterMode.errors_only); b = _bot(db, "d-eo-b")
    _dest(db, p, b)
    assert _run(p.id, CLEAN, db) == []
    assert len(_run(p.id, ERROR, db)) == 1


def test_dispatch_off_skips_everything(db):
    p = _project(db, "d-off", fm=FilterMode.off); b = _bot(db, "d-off-b")
    _dest(db, p, b)
    assert _run(p.id, ERROR, db) == []


def test_dispatch_destination_override_beats_project(db):
    p = _project(db, "d-ov", fm=FilterMode.all); b = _bot(db, "d-ov-b")
    d_off = _dest(db, p, b, fm=FilterMode.off)
    d_on = _dest(db, p, b, fm=None)  # inherits project 'all'
    ids = {r["dest_id"] for r in _run(p.id, CLEAN, db)}
    assert d_on.id in ids and d_off.id not in ids


def test_dispatch_disabled_destination_ignored(db):
    p = _project(db, "d-dis"); b = _bot(db, "d-dis-b")
    _dest(db, p, b, enabled=False)
    assert _run(p.id, CLEAN, db) == []


def test_dispatch_slack_discord_email(db):
    p = _project(db, "d-multi")
    slack_bot = Bot(name="d-slack", type=DestinationType.slack, ntfy_url=None, slack_url="https://hooks.slack/x")
    disc_bot = Bot(name="d-disc", type=DestinationType.discord, discord_url="https://discord/x")
    mail_bot = Bot(name="d-mail", type=DestinationType.email, smtp_host="smtp.x", smtp_from="a@x")
    db.add_all([slack_bot, disc_bot, mail_bot]); db.commit()
    db.refresh(slack_bot); db.refresh(disc_bot); db.refresh(mail_bot)
    db.add(Destination(project_id=p.id, bot_id=slack_bot.id, type=DestinationType.slack))
    db.add(Destination(project_id=p.id, bot_id=disc_bot.id, type=DestinationType.discord))
    db.add(Destination(project_id=p.id, bot_id=mail_bot.id, type=DestinationType.email, email_to="to@x"))
    db.commit()
    res = _run(p.id, CLEAN, db)
    assert len(res) == 3 and all(r["ok"] for r in res)


# ── Coolify routing ─────────────────────────────────────────────────────────
def _post_coolify(payload):
    return TestClient(app).post("/webhook/coolify/test-incoming-token", json=payload)


def test_coolify_routes_by_application_uuid(db):
    _project(db, "cf-uuid", coolify_uuid="UUID-A")
    body = _post_coolify({"application_uuid": "UUID-A", "status": "finished"}).json()
    assert body["routed"] and body["project"] == "cf-uuid"


def test_coolify_routes_by_name(db):
    _project(db, "cf-name")
    body = _post_coolify({"application_name": "cf-name", "status": "finished"}).json()
    assert body["project"] == "cf-name"


def test_coolify_routes_by_server_uuid(db):
    _project(db, "cf-srv", coolify_server_uuid="SRV-X")
    body = _post_coolify({"server_uuid": "SRV-X", "status": "finished"}).json()
    assert body["project"] == "cf-srv"


def test_coolify_falls_back_to_default(db):
    db.query(Project).update({Project.is_default: False}); db.commit()
    _project(db, "cf-default", is_default=True)
    body = _post_coolify({"application_uuid": "NOPE", "status": "finished"}).json()
    assert body["routed"] and body["project"] == "cf-default"


def test_coolify_no_match_no_default_dropped(db):
    db.query(Project).update({Project.is_default: False}); db.commit()
    body = _post_coolify({"application_uuid": "NOPE-2", "status": "finished"}).json()
    assert body["routed"] is False
