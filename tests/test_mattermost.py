"""Mattermost notifier logic (pure parts, no live API)."""
import asyncio

from app.models import DestinationType
from app.notifiers.mattermost import _is_raw_id, resolve_channel_id
from app.routes.webhook import _to_markdown


def test_destination_type_registered():
    assert DestinationType.mattermost.value == "mattermost"


def test_is_raw_id():
    assert _is_raw_id("abcdefghij0123456789klmnop")  # 26-char lowercase base32
    assert not _is_raw_id("@user")
    assert not _is_raw_id("town-square")
    assert not _is_raw_id("ABCDEFGHIJ0123456789KLMNOP")  # uppercase
    assert not _is_raw_id("short")


def test_resolve_raw_id_passthrough():
    # A 26-char id is returned directly, before any network call.
    cid = asyncio.run(resolve_channel_id("https://mm", "tok", "abcdefghij0123456789klmnop"))
    assert cid == "abcdefghij0123456789klmnop"


def test_resolve_missing_args_returns_none():
    assert asyncio.run(resolve_channel_id("", "", "")) is None
    assert asyncio.run(resolve_channel_id("https://mm", "tok", "")) is None


def test_to_markdown_converts_tags():
    md = _to_markdown('<b>Title</b> <code>x</code> <a href="http://e">link</a>')
    assert md == "**Title** `x` [link](http://e)"
