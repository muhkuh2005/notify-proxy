"""Admin route coverage: projects, settings, bots, destinations (happy + error paths).

Runs in Basic-auth mode (OAuth disabled), so admin routes accept HTTP Basic
`auth=("admin", "test-password")` and the auth guards return a synthetic admin.

CRITICAL PATTERN (see tests/test_authz.py): the `db` fixture overrides
`get_db` to hand the route handler the *same* SQLite session the test holds.
Without this, the test session and the request session deadlock SQLite
("database is locked"). Every test that touches the DB depends on `db`.
"""
from fastapi.testclient import TestClient

import pytest

from app.database import SessionLocal, get_db
from app.main import app
from app.models import Bot, Destination, DestinationType, FilterMode, Project, Setting

AUTH = ("admin", "test-password")


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


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Stub every notifier .send (and telegram.get_me) so no route makes a real
    network call when it sends a 'destination set up' test message."""
    async def ok(*a, **k):
        return True

    async def none(*a, **k):
        return None

    for mod in ("ntfy", "telegram", "mattermost", "slack", "discord", "email"):
        monkeypatch.setattr(f"app.notifiers.{mod}.send", ok)
    monkeypatch.setattr("app.notifiers.telegram.get_me", none)


@pytest.fixture(autouse=True)
def clean(db):
    """Wipe all rows we may create so this file never pollutes other test files."""
    yield
    db.query(Destination).delete()
    db.query(Bot).delete()
    db.query(Project).delete()
    db.query(Setting).delete()
    db.commit()


@pytest.fixture
def client():
    return TestClient(app)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _project(db, name="p", **kw):
    p = Project(name=name, **kw)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _ntfy_bot(db, name="b"):
    b = Bot(name=name, type=DestinationType.ntfy, ntfy_url="https://ntfy.example")
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _ntfy_dest(db, project, bot, topic="topic"):
    d = Destination(
        project_id=project.id,
        bot_id=bot.id,
        type=DestinationType.ntfy,
        ntfy_topic=topic,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


# ── Auth boundary ───────────────────────────────────────────────────────────
def test_admin_index_requires_auth(client, db):
    """No credentials -> 401 with a Basic-auth challenge."""
    r = client.get("/admin")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers


# ── Projects: index + new (GET renders) ─────────────────────────────────────
def test_admin_index_renders(client, db):
    _project(db, "idx-proj")
    r = client.get("/admin", auth=AUTH)
    assert r.status_code == 200
    assert "idx-proj" in r.text


def test_project_new_get_renders(client, db):
    r = client.get("/admin/projects/new", auth=AUTH)
    assert r.status_code == 200


# ── Projects: create (happy + duplicate) ────────────────────────────────────
def test_project_create_redirects(client, db):
    r = client.post(
        "/admin/projects/new",
        data={"name": "created-proj"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/admin/projects/")
    assert db.query(Project).filter(Project.name == "created-proj").first() is not None


def test_project_create_duplicate_name_400(client, db):
    _project(db, "dup-proj")
    r = client.post(
        "/admin/projects/new",
        data={"name": "dup-proj"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 400


# ── Projects: detail GET (happy + 404) ──────────────────────────────────────
def test_project_edit_get_renders(client, db):
    p = _project(db, "detail-proj")
    r = client.get(f"/admin/projects/{p.id}", auth=AUTH)
    assert r.status_code == 200
    assert "detail-proj" in r.text


def test_project_edit_get_missing_404(client, db):
    r = client.get("/admin/projects/999999", auth=AUTH)
    assert r.status_code == 404


# ── Projects: filter (valid + invalid + 404) ────────────────────────────────
def test_project_set_filter_valid(client, db):
    p = _project(db, "filter-proj", filter_mode=FilterMode.all)
    r = client.post(
        f"/admin/projects/{p.id}/filter",
        data={"filter_mode": "errors_only"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(p)
    assert p.filter_mode == FilterMode.errors_only


def test_project_set_filter_invalid_400(client, db):
    p = _project(db, "filter-bad")
    r = client.post(
        f"/admin/projects/{p.id}/filter",
        data={"filter_mode": "nonsense"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_project_set_filter_missing_404(client, db):
    r = client.post(
        "/admin/projects/999999/filter",
        data={"filter_mode": "all"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 404


# ── Projects: set-default / unset-default ───────────────────────────────────
def test_project_set_and_unset_default(client, db):
    other = _project(db, "old-default", is_default=True)
    p = _project(db, "new-default")
    r = client.post(
        f"/admin/projects/{p.id}/set-default", auth=AUTH, follow_redirects=False
    )
    assert r.status_code == 303
    db.refresh(p)
    db.refresh(other)
    assert p.is_default is True
    assert other.is_default is False  # only one default at a time

    r2 = client.post(
        f"/admin/projects/{p.id}/unset-default", auth=AUTH, follow_redirects=False
    )
    assert r2.status_code == 303
    db.refresh(p)
    assert p.is_default is False


def test_project_set_default_missing_404(client, db):
    r = client.post(
        "/admin/projects/999999/set-default", auth=AUTH, follow_redirects=False
    )
    assert r.status_code == 404


# ── Projects: delete ────────────────────────────────────────────────────────
def test_project_delete(client, db):
    p = _project(db, "del-proj")
    pid = p.id
    r = client.post(
        f"/admin/projects/{pid}/delete", auth=AUTH, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"
    assert db.query(Project).filter(Project.id == pid).first() is None


# ── Settings ────────────────────────────────────────────────────────────────
def test_settings_page_renders(client, db):
    r = client.get("/admin/settings", auth=AUTH)
    assert r.status_code == 200


def test_settings_save_token(client, db):
    r = client.post(
        "/admin/settings",
        data={"verification_bot_token": "tok-123"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    row = db.query(Setting).filter(Setting.key == "verification_bot_token").first()
    assert row is not None and row.value == "tok-123"


def test_settings_clear_verification_bot(client, db):
    db.add(Setting(key="verification_bot_token", value="tok-xyz"))
    db.commit()
    r = client.post(
        "/admin/settings/clear-verification-bot",
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        db.query(Setting).filter(Setting.key == "verification_bot_token").first()
        is None
    )


# ── sync-coolify (not configured in tests) ──────────────────────────────────
def test_sync_coolify_not_configured_400(client, db):
    r = client.post("/admin/sync-coolify", auth=AUTH, follow_redirects=False)
    assert r.status_code == 400


# ── Bots: list + new GET render ─────────────────────────────────────────────
def test_bots_list_renders(client, db):
    _ntfy_bot(db, "list-bot")
    r = client.get("/admin/bots", auth=AUTH)
    assert r.status_code == 200
    assert "list-bot" in r.text


def test_bot_new_get_renders(client, db):
    r = client.get("/admin/bots/new", auth=AUTH)
    assert r.status_code == 200


# ── Bots: create (happy + invalid type + duplicate) ─────────────────────────
def test_bot_create_ntfy_redirects(client, db):
    r = client.post(
        "/admin/bots/new",
        data={"name": "new-ntfy", "bot_type": "ntfy", "ntfy_url": "https://n.example"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/bots"
    bot = db.query(Bot).filter(Bot.name == "new-ntfy").first()
    assert bot is not None and bot.type == DestinationType.ntfy


def test_bot_create_invalid_type_400(client, db):
    r = client.post(
        "/admin/bots/new",
        data={"name": "bad-type", "bot_type": "carrier-pigeon"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_bot_create_duplicate_name_400(client, db):
    _ntfy_bot(db, "dup-bot")
    r = client.post(
        "/admin/bots/new",
        data={"name": "dup-bot", "bot_type": "ntfy"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 400


# ── Bots: edit page + update + visibility + delete ──────────────────────────
def test_bot_edit_page_renders(client, db):
    b = _ntfy_bot(db, "edit-bot")
    r = client.get(f"/admin/bots/{b.id}", auth=AUTH)
    assert r.status_code == 200
    assert "edit-bot" in r.text


def test_bot_edit_page_missing_404(client, db):
    r = client.get("/admin/bots/999999", auth=AUTH)
    assert r.status_code == 404


def test_bot_update(client, db):
    b = _ntfy_bot(db, "upd-bot")
    r = client.post(
        f"/admin/bots/{b.id}",
        data={"name": "upd-bot-renamed", "ntfy_url": "https://n2.example"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(b)
    assert b.name == "upd-bot-renamed"
    assert b.ntfy_url == "https://n2.example"


def test_bot_visibility_toggle(client, db):
    b = _ntfy_bot(db, "vis-bot")
    assert b.visibility == "private"
    r = client.post(
        f"/admin/bots/{b.id}/visibility",
        data={"visibility": "global"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(b)
    assert b.visibility == "global"

    r2 = client.post(
        f"/admin/bots/{b.id}/visibility",
        data={"visibility": "private"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r2.status_code == 303
    db.refresh(b)
    assert b.visibility == "private"


def test_bot_delete(client, db):
    b = _ntfy_bot(db, "del-bot")
    bid = b.id
    r = client.post(f"/admin/bots/{bid}/delete", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    assert db.query(Bot).filter(Bot.id == bid).first() is None


# ── Destinations: add (happy + missing topic 400 + bot-not-found 400) ───────
def test_destination_add_ntfy(client, db):
    p = _project(db, "dest-proj")
    b = _ntfy_bot(db, "dest-bot")
    r = client.post(
        f"/admin/projects/{p.id}/destinations/add",
        data={"bot_id": b.id, "ntfy_topic": "my-topic"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    dest = db.query(Destination).filter(Destination.project_id == p.id).first()
    assert dest is not None and dest.ntfy_topic == "my-topic"


def test_destination_add_ntfy_missing_topic_400(client, db):
    p = _project(db, "dest-proj-2")
    b = _ntfy_bot(db, "dest-bot-2")
    r = client.post(
        f"/admin/projects/{p.id}/destinations/add",
        data={"bot_id": b.id, "ntfy_topic": ""},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_destination_add_bot_not_found_400(client, db):
    p = _project(db, "dest-proj-3")
    r = client.post(
        f"/admin/projects/{p.id}/destinations/add",
        data={"bot_id": 999999, "ntfy_topic": "t"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_destination_add_project_not_found_404(client, db):
    b = _ntfy_bot(db, "dest-bot-4")
    r = client.post(
        "/admin/projects/999999/destinations/add",
        data={"bot_id": b.id, "ntfy_topic": "t"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 404


# ── Destinations: filter / toggle / visibility / delete ─────────────────────
def test_destination_set_filter(client, db):
    p = _project(db, "df-proj")
    b = _ntfy_bot(db, "df-bot")
    d = _ntfy_dest(db, p, b)
    r = client.post(
        f"/admin/destinations/{d.id}/filter",
        data={"filter_mode": "errors_only"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(d)
    assert d.filter_mode == FilterMode.errors_only

    # 'inherit' clears the override back to None
    r2 = client.post(
        f"/admin/destinations/{d.id}/filter",
        data={"filter_mode": "inherit"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r2.status_code == 303
    db.refresh(d)
    assert d.filter_mode is None


def test_destination_toggle(client, db):
    p = _project(db, "dt-proj")
    b = _ntfy_bot(db, "dt-bot")
    d = _ntfy_dest(db, p, b)
    assert d.enabled is True
    r = client.post(
        f"/admin/destinations/{d.id}/toggle", auth=AUTH, follow_redirects=False
    )
    assert r.status_code == 303
    db.refresh(d)
    assert d.enabled is False


def test_destination_visibility_toggle(client, db):
    p = _project(db, "dv-proj")
    b = _ntfy_bot(db, "dv-bot")
    d = _ntfy_dest(db, p, b)
    r = client.post(
        f"/admin/destinations/{d.id}/visibility",
        data={"visibility": "global"},
        auth=AUTH,
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(d)
    assert d.visibility == "global"


def test_destination_delete(client, db):
    p = _project(db, "dd-proj")
    b = _ntfy_bot(db, "dd-bot")
    d = _ntfy_dest(db, p, b)
    did = d.id
    r = client.post(
        f"/admin/destinations/{did}/delete", auth=AUTH, follow_redirects=False
    )
    assert r.status_code == 303
    assert db.query(Destination).filter(Destination.id == did).first() is None


def test_destination_delete_missing_404(client, db):
    r = client.post(
        "/admin/destinations/999999/delete", auth=AUTH, follow_redirects=False
    )
    assert r.status_code == 404


# ── root redirect ───────────────────────────────────────────────────────────
def test_root_redirects_to_admin(client, db):
    r = client.get("/", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"
