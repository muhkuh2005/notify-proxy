"""Unit tests for the pure formatting/classification helpers in webhook.py."""
import asyncio

from app.routes.webhook import (
    _camel_to_words,
    _extract_commit,
    _format_message,
    _is_error,
    _resolve_version,
    _short_commit,
    _strip_html,
)


class TestIsError:
    def test_error_status(self):
        assert _is_error({"status": "failed"}) is True
        assert _is_error({"status": "cancelled-by-user"}) is True

    def test_non_error_status(self):
        assert _is_error({"status": "finished"}) is False
        assert _is_error({"status": "success"}) is False

    def test_error_word_in_type(self):
        assert _is_error({"type": "DeploymentFailed"}) is True

    def test_error_word_in_message(self):
        assert _is_error({"message": "container crash detected"}) is True

    def test_clean_payload(self):
        assert _is_error({"type": "DeploymentSuccess", "message": "all good"}) is False


class TestCamelToWords:
    def test_splits_camel_case(self):
        assert _camel_to_words("ServerBackupFinished") == "Server Backup Finished"

    def test_single_word_unchanged(self):
        assert _camel_to_words("Backup") == "Backup"


class TestStripHtml:
    def test_strips_tags_and_keeps_link_text(self):
        html = '<b>Title</b> <code>status</code> <a href="http://x">link</a>'
        assert _strip_html(html) == "Title status link"


class TestFormatMessage:
    def test_application_event(self):
        title, body = _format_message(
            {"status": "finished", "application_name": "web", "project_name": "proj"}
        )
        assert title.startswith("✅")
        assert "proj" in title and "web" in title
        assert "finished" in body

    def test_failed_application_event(self):
        title, _ = _format_message({"status": "failed", "application_name": "api"})
        assert title.startswith("❌")

    def test_server_event(self):
        title, body = _format_message(
            {"server_name": "node1", "type": "ServerBackupFinished"}
        )
        assert "node1" in title
        assert "Server Backup Finished" in body

    def test_coolify_deploy_success_real_payload(self):
        # Real Coolify deployment webhook: no status/type, uses event/success
        # and the real key names project/environment/deployment_url.
        title, body = _format_message(
            {
                "event": "deployment_success",
                "success": True,
                "message": "New version successfully deployed",
                "application_name": "Engine",
                "project": "gentle-season",
                "environment": "production",
                "deployment_url": "https://coolify/deploy/abc",
            }
        )
        assert title.startswith("✅")
        assert "unknown" not in title.lower()
        assert "Engine" in title and "gentle-season" in title
        assert "success" in body
        assert "production" in body

    def test_coolify_deploy_failed_real_payload(self):
        title, _ = _format_message(
            {
                "event": "deployment_failed",
                "success": False,
                "message": "Deployment failed",
                "application_name": "Engine",
            }
        )
        assert title.startswith("❌")
        assert "unknown" not in title.lower()

    def test_deploy_success_via_message_only(self):
        # No event, no success bool — derive from message text.
        title, _ = _format_message(
            {"message": "New version successfully deployed", "application_name": "x"}
        )
        assert title.startswith("✅")

    def test_commit_shown_in_body(self):
        _, body = _format_message(
            {"status": "success", "application_name": "x"}, version=("ab12cd3", True)
        )
        assert "Commit: " in body
        assert "ab12cd3" in body

    def test_version_tag_shown_in_body(self):
        _, body = _format_message(
            {"status": "success", "application_name": "x"}, version=("pr-1253", False)
        )
        assert "Version: " in body
        assert "pr-1253" in body
        assert "Commit:" not in body

    def test_no_version_no_line(self):
        _, body = _format_message({"status": "success", "application_name": "x"})
        assert "Commit:" not in body
        assert "Version:" not in body


class TestCommit:
    def test_short_commit_shortens_long_hex(self):
        assert _short_commit("0123456789abcdef0123456789abcdef") == "01234567"

    def test_short_commit_leaves_tag(self):
        assert _short_commit("v1.2.3") == "v1.2.3"

    def test_extract_from_payload_keys(self):
        assert _extract_commit({"git_commit_sha": "deadbeef0123"}) == "deadbeef"
        assert _extract_commit({"version": "v2.0.0"}) == "v2.0.0"

    def test_extract_skips_head_ref(self):
        assert _extract_commit({"commit": "HEAD"}) is None

    def test_extract_none_when_absent(self):
        assert _extract_commit({"application_name": "x"}) is None

    def test_resolve_prefers_payload_over_api(self, monkeypatch):
        async def boom(deployment_uuid):
            raise AssertionError("API must not be called when payload has commit")
        monkeypatch.setattr("app.services.coolify_sync.get_deployment_info", boom)
        out = asyncio.run(_resolve_version({"git_commit_sha": "feedface0011"}))
        assert out == ("feedface", True)

    def test_resolve_real_commit_from_deployment(self, monkeypatch):
        async def fake(deployment_uuid):
            assert deployment_uuid == "dep-1"
            return {"commit": "00aa11bb22cc", "image_tag": "pr-9"}
        monkeypatch.setattr("app.services.coolify_sync.get_deployment_info", fake)
        out = asyncio.run(_resolve_version({"deployment_uuid": "dep-1"}))
        assert out == ("00aa11bb", True)  # commit wins over tag

    def test_resolve_image_tag_fallback(self, monkeypatch):
        async def fake(deployment_uuid):
            return {"commit": None, "image_tag": "pr-1253"}
        monkeypatch.setattr("app.services.coolify_sync.get_deployment_info", fake)
        out = asyncio.run(_resolve_version({"deployment_uuid": "dep-1"}))
        assert out == ("pr-1253", False)  # is_commit False -> "Version" label

    def test_resolve_skips_noise_tag(self, monkeypatch):
        async def fake(deployment_uuid):
            return {"commit": None, "image_tag": "latest"}
        monkeypatch.setattr("app.services.coolify_sync.get_deployment_info", fake)
        out = asyncio.run(_resolve_version({"deployment_uuid": "dep-1"}))
        assert out is None

    def test_resolve_none_when_nothing(self, monkeypatch):
        async def none(deployment_uuid):
            return None
        monkeypatch.setattr("app.services.coolify_sync.get_deployment_info", none)
        out = asyncio.run(_resolve_version({"application_name": "x"}))
        assert out is None
