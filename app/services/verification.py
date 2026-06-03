import os
import secrets
from datetime import timedelta

from ..util import now_utc

VERIFICATION_BOT_TOKEN = os.environ.get("VERIFICATION_BOT_TOKEN", "")

_pending: dict[str, dict] = {}  # code -> {dest_id, label, expires}


def _sweep() -> None:
    now = now_utc()
    expired = [c for c, e in _pending.items() if e["expires"] < now]
    for c in expired:
        del _pending[c]


def is_configured() -> bool:
    return bool(VERIFICATION_BOT_TOKEN)


def create(dest_id: int, label: str) -> str:
    _sweep()
    now = now_utc()
    code = secrets.token_hex(3).upper()
    _pending[code] = {
        "dest_id": dest_id,
        "label": label,
        "expires": now + timedelta(minutes=10),
    }
    return code


def get_by_dest(dest_id: int) -> tuple[str, dict] | None:
    _sweep()
    for code, entry in list(_pending.items()):
        if entry["dest_id"] == dest_id:
            return code, entry
    return None


def remove(code: str) -> None:
    _pending.pop(code, None)
