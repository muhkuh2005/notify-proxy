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


AUTH = ("admin", "test-password")


def test_new_bot_page_offers_mattermost():
    page = client.get("/admin/bots/new", auth=AUTH)
    assert page.status_code == 200
    assert 'value="mattermost"' in page.text


def test_create_and_edit_mattermost_bot_renders():
    r = client.post(
        "/admin/bots/new",
        auth=AUTH,
        data={
            "name": "mm-test",
            "bot_type": "mattermost",
            "mattermost_url": "https://mm.example.com",
            "mattermost_token": "tok",
            "mattermost_team": "team",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert client.get("/admin/bots", auth=AUTH).status_code == 200
    # the bot's edit page must render the mattermost branch
    bots = client.get("/admin/bots", auth=AUTH).text
    assert "mm-test" in bots


def test_project_edit_renders_mattermost_target_form():
    r = client.post(
        "/admin/projects/new", auth=AUTH, data={"name": "p-mm"}, follow_redirects=False
    )
    assert r.status_code == 303
    page = client.get(r.headers["location"], auth=AUTH)
    assert page.status_code == 200
    assert "target-mattermost" in page.text
