"""Integration tests against the FastAPI app via TestClient."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unknown_project_webhook_404():
    resp = client.post("/webhook/does-not-exist", json={"status": "finished"})
    assert resp.status_code == 404


def test_coolify_webhook_wrong_token_404():
    resp = client.post("/webhook/coolify/wrong-token", json={"status": "finished"})
    assert resp.status_code == 404


def test_coolify_webhook_empty_ping_ignored():
    resp = client.post("/webhook/coolify/test-incoming-token", json={})
    assert resp.status_code == 200
    assert resp.json() == {"routed": False, "reason": "empty ping"}


def test_admin_requires_auth():
    resp = client.get("/admin")
    assert resp.status_code == 401


def test_admin_accepts_valid_basic_auth():
    resp = client.get("/admin", auth=("admin", "test-password"))
    assert resp.status_code == 200
