import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .database import Base, engine
from .routes.admin import router as admin_router
from .routes.webhook import router as webhook_router

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# Refuse to boot with an unset or default admin password. The admin UI has no
# other auth gate, so a weak password exposes every stored bot token.
if os.environ.get("ADMIN_PASSWORD", "") in ("", "changeme"):
    raise RuntimeError(
        "ADMIN_PASSWORD is unset or still 'changeme'. Set a strong ADMIN_PASSWORD "
        "(env var) before starting notify-proxy."
    )


app = FastAPI(title="notify-proxy", docs_url=None, redoc_url=None)

@app.get("/health")
def health():
    return {"status": "ok"}

Base.metadata.create_all(bind=engine)

# Additive migrations for columns added after initial deploy
with engine.connect() as _conn:
    for _stmt in [
        "ALTER TABLE projects ADD COLUMN coolify_uuid TEXT",
        "ALTER TABLE projects ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN filter_mode TEXT NOT NULL DEFAULT 'all'",
        "ALTER TABLE projects ADD COLUMN coolify_server_uuid TEXT",
        "ALTER TABLE projects ADD COLUMN coolify_project_name TEXT",
        "ALTER TABLE destinations ADD COLUMN bot_id INTEGER REFERENCES bots(id)",
        "ALTER TABLE destinations ADD COLUMN ntfy_topic TEXT",
        "ALTER TABLE destinations ADD COLUMN telegram_chat_label TEXT",
        "ALTER TABLE destinations ADD COLUMN filter_mode TEXT",
        "ALTER TABLE destinations ADD COLUMN last_test_ok INTEGER",
    ]:
        try:
            _conn.execute(text(_stmt))
            _conn.commit()
        except Exception:
            pass

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(admin_router)
app.include_router(webhook_router)
