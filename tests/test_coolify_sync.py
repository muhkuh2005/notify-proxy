"""Tests for app.services.coolify_sync.

Covers:
- is_configured() reflecting module-level COOLIFY_BASE_URL / COOLIFY_TOKEN
  (these are read at import time, so we monkeypatch the module attributes).
- sync_projects(db): creating Project rows from Coolify applications/servers,
  resolving coolify_project_name via the projects-detail endpoint, idempotent
  re-sync (update / skip, no duplicates), and graceful handling of empty and
  error responses.

The Coolify HTTP API is mocked with respx. coolify_sync builds request URLs from
the module-level COOLIFY_BASE_URL, so every test patches it to a known base and
mocks the matching endpoints:
    GET /api/v1/applications
    GET /api/v1/servers
    GET /api/v1/projects
    GET /api/v1/projects/{uuid}
"""
import asyncio

import httpx
import pytest
import respx

from app.database import Base, SessionLocal, engine
from app.models import Project
from app.services import coolify_sync

# Ensure tables exist for this unique test DB without importing app.main
# (which would enforce ADMIN_PASSWORD side effects and create_all anyway).
Base.metadata.create_all(bind=engine)

BASE = "https://cf.example.test"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def configured(monkeypatch):
    """Point the module at a known base URL + token for the duration of a test."""
    monkeypatch.setattr(coolify_sync, "COOLIFY_BASE_URL", BASE)
    monkeypatch.setattr(coolify_sync, "COOLIFY_TOKEN", "tok-123")
    return BASE


@pytest.fixture
def db():
    """Real SessionLocal session; Project rows are wiped before and after."""
    s = SessionLocal()
    s.query(Project).delete()
    s.commit()
    try:
        yield s
    finally:
        s.query(Project).delete()
        s.commit()
        s.close()


def _mock_endpoints(apps=None, servers=None, projects=None, project_details=None):
    """Register the four Coolify GET endpoints with respx.

    project_details maps {uuid: detail_json}; unspecified uuids return 404.
    """
    apps = apps if apps is not None else []
    servers = servers if servers is not None else []
    projects = projects if projects is not None else []
    project_details = project_details or {}

    respx.get(f"{BASE}/api/v1/applications").mock(return_value=httpx.Response(200, json=apps))
    respx.get(f"{BASE}/api/v1/servers").mock(return_value=httpx.Response(200, json=servers))
    respx.get(f"{BASE}/api/v1/projects").mock(return_value=httpx.Response(200, json=projects))
    for uuid, detail in project_details.items():
        respx.get(f"{BASE}/api/v1/projects/{uuid}").mock(
            return_value=httpx.Response(200, json=detail)
        )
    # Any project-detail URL not explicitly mocked -> 404 (handled gracefully).
    respx.get(url__regex=rf"{BASE}/api/v1/projects/.+").mock(
        return_value=httpx.Response(404, json={})
    )


# ── is_configured ─────────────────────────────────────────────────────────────
def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setattr(coolify_sync, "COOLIFY_BASE_URL", BASE)
    monkeypatch.setattr(coolify_sync, "COOLIFY_TOKEN", "tok")
    assert coolify_sync.is_configured() is True


def test_is_configured_false_without_token(monkeypatch):
    monkeypatch.setattr(coolify_sync, "COOLIFY_BASE_URL", BASE)
    monkeypatch.setattr(coolify_sync, "COOLIFY_TOKEN", "")
    assert coolify_sync.is_configured() is False


def test_is_configured_false_without_base_url(monkeypatch):
    monkeypatch.setattr(coolify_sync, "COOLIFY_BASE_URL", "")
    monkeypatch.setattr(coolify_sync, "COOLIFY_TOKEN", "tok")
    assert coolify_sync.is_configured() is False


# ── sync_projects: creation ───────────────────────────────────────────────────
def test_sync_creates_application_with_project_name(configured, db):
    apps = [{"uuid": "app-uuid-1", "name": "web-frontend", "environment_id": 7}]
    projects = [{"uuid": "proj-uuid-1"}]
    details = {
        "proj-uuid-1": {"name": "Acme", "environments": [{"id": 7}]},
    }
    with respx.mock:
        _mock_endpoints(apps=apps, projects=projects, project_details=details)
        result = _run(coolify_sync.sync_projects(db))

    assert result["created"] == ["web-frontend"]
    assert result["updated"] == []
    assert result["skipped"] == []

    db.expire_all()
    p = db.query(Project).filter(Project.coolify_uuid == "app-uuid-1").one()
    assert p.name == "web-frontend"
    assert p.coolify_project_name == "Acme"


def test_sync_creates_server_project(configured, db):
    servers = [{"uuid": "srv-uuid-1", "name": "node-01"}]
    with respx.mock:
        _mock_endpoints(servers=servers)
        result = _run(coolify_sync.sync_projects(db))

    assert result["created"] == ["node-01 (server)"]
    db.expire_all()
    p = db.query(Project).filter(Project.coolify_server_uuid == "srv-uuid-1").one()
    assert p.name == "node-01 (server)"
    assert p.coolify_uuid is None


def test_sync_app_falls_back_to_id_when_no_uuid(configured, db):
    # No 'uuid' key -> falls back to 'id'; no environment_id -> empty project name.
    apps = [{"id": "legacy-id-9", "name": "legacy-app"}]
    with respx.mock:
        _mock_endpoints(apps=apps)
        result = _run(coolify_sync.sync_projects(db))

    assert result["created"] == ["legacy-app"]
    db.expire_all()
    p = db.query(Project).filter(Project.coolify_uuid == "legacy-id-9").one()
    assert p.name == "legacy-app"
    assert p.coolify_project_name is None


# ── sync_projects: idempotency (update / skip, no duplicates) ─────────────────
def test_sync_is_idempotent_no_duplicates(configured, db):
    apps = [{"uuid": "app-uuid-2", "name": "api-service", "environment_id": 1}]
    servers = [{"uuid": "srv-uuid-2", "name": "host-a"}]
    projects = [{"uuid": "proj-uuid-2"}]
    details = {"proj-uuid-2": {"name": "Platform", "environments": [{"id": 1}]}}

    with respx.mock:
        _mock_endpoints(apps=apps, servers=servers, projects=projects, project_details=details)
        first = _run(coolify_sync.sync_projects(db))
    assert sorted(first["created"]) == ["api-service", "host-a (server)"]

    # Second run with identical data: everything already present & unchanged.
    with respx.mock:
        _mock_endpoints(apps=apps, servers=servers, projects=projects, project_details=details)
        second = _run(coolify_sync.sync_projects(db))

    assert second["created"] == []
    assert sorted(second["skipped"]) == ["api-service", "host-a (server)"]

    db.expire_all()
    assert db.query(Project).filter(Project.coolify_uuid == "app-uuid-2").count() == 1
    assert db.query(Project).filter(Project.coolify_server_uuid == "srv-uuid-2").count() == 1
    assert db.query(Project).count() == 2


def test_sync_updates_existing_project_by_name(configured, db):
    # Pre-existing project sharing the app name but lacking the coolify_uuid.
    db.add(Project(name="orphan-app"))
    db.commit()

    apps = [{"uuid": "app-uuid-3", "name": "orphan-app", "environment_id": 5}]
    projects = [{"uuid": "proj-uuid-3"}]
    details = {"proj-uuid-3": {"name": "TeamX", "environments": [{"id": 5}]}}
    with respx.mock:
        _mock_endpoints(apps=apps, projects=projects, project_details=details)
        result = _run(coolify_sync.sync_projects(db))

    assert result["updated"] == ["orphan-app"]
    assert result["created"] == []

    db.expire_all()
    p = db.query(Project).filter(Project.name == "orphan-app").one()
    assert p.coolify_uuid == "app-uuid-3"
    assert p.coolify_project_name == "TeamX"


def test_sync_updates_server_uuid_on_existing_named_project(configured, db):
    # A project already named "host-b (server)" but without server uuid gets adopted.
    db.add(Project(name="host-b (server)"))
    db.commit()

    servers = [{"uuid": "srv-uuid-4", "name": "host-b"}]
    with respx.mock:
        _mock_endpoints(servers=servers)
        result = _run(coolify_sync.sync_projects(db))

    assert result["updated"] == ["host-b (server)"]
    db.expire_all()
    p = db.query(Project).filter(Project.name == "host-b (server)").one()
    assert p.coolify_server_uuid == "srv-uuid-4"


# ── sync_projects: empty / error responses ────────────────────────────────────
def test_sync_empty_responses_creates_nothing(configured, db):
    with respx.mock:
        _mock_endpoints()  # all endpoints return []
        result = _run(coolify_sync.sync_projects(db))

    assert result == {"created": [], "updated": [], "skipped": []}
    db.expire_all()
    assert db.query(Project).count() == 0


def test_sync_project_detail_404_skips_name_resolution(configured, db):
    # Project listed but its detail endpoint 404s -> env map empty,
    # so the app is still created but without a coolify_project_name.
    apps = [{"uuid": "app-uuid-5", "name": "lonely-app", "environment_id": 9}]
    projects = [{"uuid": "proj-uuid-5"}]
    with respx.mock:
        # project_details intentionally omitted -> regex fallback returns 404.
        _mock_endpoints(apps=apps, projects=projects)
        result = _run(coolify_sync.sync_projects(db))

    assert result["created"] == ["lonely-app"]
    db.expire_all()
    p = db.query(Project).filter(Project.coolify_uuid == "app-uuid-5").one()
    assert p.coolify_project_name is None


def test_sync_raises_on_non_200_applications(configured, db):
    # applications endpoint errors -> _get raises (raise_for_status) before any
    # DB mutation, so no rows are created.
    with respx.mock:
        respx.get(f"{BASE}/api/v1/applications").mock(return_value=httpx.Response(500, json={}))
        respx.get(f"{BASE}/api/v1/servers").mock(return_value=httpx.Response(200, json=[]))
        respx.get(f"{BASE}/api/v1/projects").mock(return_value=httpx.Response(200, json=[]))
        with pytest.raises(httpx.HTTPStatusError):
            _run(coolify_sync.sync_projects(db))

    db.expire_all()
    assert db.query(Project).count() == 0


def test_sync_handles_data_wrapped_list_shape(configured, db):
    # Coolify may wrap results as {"data": [...]}; _get unwraps the "data" key.
    apps = {"data": [{"uuid": "app-uuid-6", "name": "wrapped-app"}]}
    with respx.mock:
        respx.get(f"{BASE}/api/v1/applications").mock(return_value=httpx.Response(200, json=apps))
        respx.get(f"{BASE}/api/v1/servers").mock(return_value=httpx.Response(200, json={"data": []}))
        respx.get(f"{BASE}/api/v1/projects").mock(return_value=httpx.Response(200, json={"data": []}))
        result = _run(coolify_sync.sync_projects(db))

    assert result["created"] == ["wrapped-app"]
    db.expire_all()
    assert db.query(Project).filter(Project.coolify_uuid == "app-uuid-6").count() == 1
