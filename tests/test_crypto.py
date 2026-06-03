"""At-rest encryption of secret columns (EncryptedText)."""
from cryptography.fernet import Fernet
from sqlalchemy import text as sqltext

from app.crypto import EncryptedText
from app.database import SessionLocal
from app.models import Bot, DestinationType


def test_roundtrip_with_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    t = EncryptedText()
    enc = t.process_bind_param("secret-token", None)
    assert enc != "secret-token"                               # stored as ciphertext
    assert t.process_result_value(enc, None) == "secret-token" # decrypts on read
    assert t.process_result_value("legacy-plain", None) == "legacy-plain"  # legacy passthrough


def test_passthrough_without_key(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    t = EncryptedText()
    assert t.process_bind_param("x", None) == "x"
    assert t.process_result_value("x", None) == "x"
    assert t.process_bind_param(None, None) is None


def test_bot_token_is_ciphertext_in_db(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db = SessionLocal()
    try:
        bot = Bot(name="enc-bot", type=DestinationType.telegram, telegram_bot_token="1234:SECRET")
        db.add(bot); db.commit()
        bid = bot.id
        db.expire_all()
        # ORM read transparently decrypts
        assert db.get(Bot, bid).telegram_bot_token == "1234:SECRET"
        # raw column is ciphertext — the plaintext secret is not present
        raw = db.execute(sqltext("SELECT telegram_bot_token FROM bots WHERE id=:i"), {"i": bid}).scalar()
        assert raw != "1234:SECRET" and "SECRET" not in raw
    finally:
        db.query(Bot).filter(Bot.name == "enc-bot").delete()
        db.commit()
        db.close()
