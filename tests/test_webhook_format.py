"""Unit tests for the pure formatting/classification helpers in webhook.py."""
from app.routes.webhook import (
    _camel_to_words,
    _format_message,
    _is_error,
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
