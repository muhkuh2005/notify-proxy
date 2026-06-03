"""Authorization enforcement: dependency guards, IDOR, and user management.

Guards are unit-tested directly (with OAuth forced on). Route-level ACL is tested
by overriding the auth dependency to act as a specific user, then hitting the
real route bodies via TestClient.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth
from app.database import SessionLocal, get_db
from app.main import app
from app.models import Bot, Destination, DestinationType, Project, User


@pytest.fixture
def db():
    s = SessionLocal()
    # Route handlers must share this exact session (one SQLite connection),
    # otherwise the test session and the request session deadlock each other.
    app.dependency_overrides[get_db] = lambda: s
    try:
        yield s
    finally:
        app.dependency_overrides.pop(get_db, None)
        s.rollback()
        s.close()


@pytest.fixture
def oauth_on(monkeypatch):
    monkeypatch.setattr(auth, "ENABLED_PROVIDERS", ["github"])
    assert auth.oauth_enabled()


def _req(session: dict):
    return SimpleNamespace(session=session)


def _mk_user(db, role="user", status="approved", sub="s") -> User:
    u = User(provider="github", provider_sub=sub, email=f"{sub}@x", name=sub, role=role, status=status)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _ntfy_bot(db, name, owner_id, visibility="private") -> Bot:
    b = Bot(name=name, type=DestinationType.ntfy, ntfy_url="https://n", owner_id=owner_id, visibility=visibility)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


# ── Dependency guards (OAuth mode) ──────────────────────────────────────────
def test_require_admin_denies_approved_non_admin(db, oauth_on):
    u = _mk_user(db, role="user", status="approved", sub="na1")
    try:
        with pytest.raises(HTTPException) as e:
            auth.require_admin(_req({"uid": u.id}), db, None)
        assert e.value.status_code == 403
    finally:
        db.delete(u); db.commit()


def test_require_admin_allows_admin(db, oauth_on):
    u = _mk_user(db, role="admin", status="approved", sub="ad1")
    try:
        assert auth.require_admin(_req({"uid": u.id}), db, None).id == u.id
    finally:
        db.delete(u); db.commit()


def test_require_user_pending_redirects_to_pending(db, oauth_on):
    u = _mk_user(db, status="pending", sub="pe1")
    try:
        with pytest.raises(HTTPException) as e:
            auth.require_user(_req({"uid": u.id}), db, None)
        assert e.value.status_code == 303
        assert e.value.headers["Location"] == "/pending"
    finally:
        db.delete(u); db.commit()


def test_require_user_blocked_403(db, oauth_on):
    u = _mk_user(db, status="blocked", sub="bl1")
    try:
        with pytest.raises(HTTPException) as e:
            auth.require_user(_req({"uid": u.id}), db, None)
        assert e.value.status_code == 403
    finally:
        db.delete(u); db.commit()


def test_require_user_no_session_redirects_to_login(db, oauth_on):
    with pytest.raises(HTTPException) as e:
        auth.require_user(_req({}), db, None)
    assert e.value.status_code == 303
    assert e.value.headers["Location"] == "/login"


# ── Route-level IDOR / ownership ────────────────────────────────────────────
@pytest.fixture
def as_user(db):
    u = _mk_user(db, role="user", status="approved", sub="attacker")
    app.dependency_overrides[auth.require_user] = lambda: u
    try:
        yield u
    finally:
        app.dependency_overrides.pop(auth.require_user, None)
        db.query(Destination).delete()
        db.query(Bot).delete()
        db.query(Project).delete()
        db.query(User).delete()
        db.commit()


def test_idor_cannot_touch_others_bot(as_user, db):
    other = _mk_user(db, sub="owner1")
    bot = _ntfy_bot(db, "o-bot", owner_id=other.id, visibility="private")
    client = TestClient(app)
    assert client.get(f"/admin/bots/{bot.id}").status_code == 403
    assert client.post(f"/admin/bots/{bot.id}", data={"name": "x"}).status_code == 403
    assert client.post(f"/admin/bots/{bot.id}/delete").status_code == 403
    assert client.post(f"/admin/bots/{bot.id}/visibility", data={"visibility": "global"}).status_code == 403


def test_user_can_edit_own_bot(as_user, db):
    bot = _ntfy_bot(db, "my-bot", owner_id=as_user.id)
    client = TestClient(app)
    assert client.get(f"/admin/bots/{bot.id}").status_code == 200
    r = client.post(f"/admin/bots/{bot.id}", data={"name": "my-bot-2"}, follow_redirects=False)
    assert r.status_code == 303


def test_global_bot_usable_but_not_editable(as_user, db):
    other = _mk_user(db, sub="owner2")
    gbot = _ntfy_bot(db, "g-bot", owner_id=other.id, visibility="global")
    pbot = _ntfy_bot(db, "p-bot", owner_id=other.id, visibility="private")
    proj = Project(name="proj-idor")
    db.add(proj); db.commit(); db.refresh(proj)
    client = TestClient(app)
    # global bot: not editable...
    assert client.get(f"/admin/bots/{gbot.id}").status_code == 403
    # ...but usable as a destination target
    r = client.post(f"/admin/projects/{proj.id}/destinations/add",
                    data={"bot_id": gbot.id, "ntfy_topic": "t"}, follow_redirects=False)
    assert r.status_code == 303
    # someone else's private bot: not usable
    r2 = client.post(f"/admin/projects/{proj.id}/destinations/add",
                     data={"bot_id": pbot.id, "ntfy_topic": "t"})
    assert r2.status_code == 403


def test_idor_cannot_delete_others_destination(as_user, db):
    other = _mk_user(db, sub="owner3")
    proj = Project(name="proj-idor2")
    db.add(proj); db.commit(); db.refresh(proj)
    bot = _ntfy_bot(db, "d-bot", owner_id=other.id, visibility="global")
    dest = Destination(project_id=proj.id, bot_id=bot.id, type=DestinationType.ntfy,
                       ntfy_topic="t", owner_id=other.id, visibility="private")
    db.add(dest); db.commit(); db.refresh(dest)
    client = TestClient(app)
    assert client.post(f"/admin/destinations/{dest.id}/delete").status_code == 403
    assert client.post(f"/admin/destinations/{dest.id}/toggle").status_code == 403
    assert client.post(f"/admin/destinations/{dest.id}/visibility", data={"visibility": "global"}).status_code == 403


# ── User management (admin) ─────────────────────────────────────────────────
@pytest.fixture
def acting_admin(db):
    db.query(User).delete(); db.commit()
    admin = _mk_user(db, role="admin", sub="theadmin")
    app.dependency_overrides[auth.require_admin] = lambda: admin
    try:
        yield admin
    finally:
        app.dependency_overrides.pop(auth.require_admin, None)
        db.query(User).delete(); db.commit()


def test_cannot_block_last_admin(acting_admin, db):
    client = TestClient(app)
    assert client.post(f"/admin/users/{acting_admin.id}/block").status_code == 400
    # second admin -> now allowed
    other_admin = _mk_user(db, role="admin", sub="a2")
    r = client.post(f"/admin/users/{acting_admin.id}/block", follow_redirects=False)
    assert r.status_code == 303


def test_cannot_demote_or_delete_last_admin(acting_admin, db):
    client = TestClient(app)
    assert client.post(f"/admin/users/{acting_admin.id}/demote").status_code == 400
    assert client.post(f"/admin/users/{acting_admin.id}/delete").status_code == 400


def test_approve_promote_demote_flow(acting_admin, db):
    pending = _mk_user(db, role="user", status="pending", sub="pen")
    client = TestClient(app)
    client.post(f"/admin/users/{pending.id}/approve", follow_redirects=False)
    db.refresh(pending); assert pending.status == "approved"
    client.post(f"/admin/users/{pending.id}/promote", follow_redirects=False)
    db.refresh(pending); assert pending.role == "admin" and pending.status == "approved"
    client.post(f"/admin/users/{pending.id}/demote", follow_redirects=False)
    db.refresh(pending); assert pending.role == "user"
