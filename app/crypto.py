"""Transparent at-rest encryption for secret columns.

Opt-in via `TOKEN_ENCRYPTION_KEY` (a Fernet key). With no key set, values are
stored as plaintext — fully backward compatible. When a key is set, reading a
value that isn't valid ciphertext (i.e. a legacy plaintext row) returns it
unchanged, so existing rows migrate lazily the next time they're written.

Generate a key:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

_cache: dict[str, Fernet] = {}


def _fernet() -> Fernet | None:
    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    if key not in _cache:
        _cache[key] = Fernet(key.encode())
    return _cache[key]


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        f = _fernet()
        return f.encrypt(value.encode()).decode() if f else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        f = _fernet()
        if f is None:
            return value
        try:
            return f.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            return value  # legacy plaintext row
