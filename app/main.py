import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from . import auth, ratelimit
from .database import Base, engine
from .routes.admin import router as admin_router
from .routes.auth import router as auth_router
from .routes.webhook import router as webhook_router

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

if auth.oauth_enabled():
    # OAuth mode: a signed session cookie is mandatory; Basic password is not used.
    if not os.environ.get("SESSION_SECRET"):
        raise RuntimeError("SESSION_SECRET must be set when OAUTH_ENABLED_PROVIDERS is configured.")
    if not auth.available_providers():
        raise RuntimeError(
            "OAUTH_ENABLED_PROVIDERS is set but no provider has credentials "
            "(GITHUB_CLIENT_ID / MICROSOFT_CLIENT_ID)."
        )
elif os.environ.get("ADMIN_PASSWORD", "") in ("", "changeme"):
    # Basic mode: refuse to boot with an unset/default password — the admin UI
    # has no other auth gate, so a weak password exposes every stored bot token.
    raise RuntimeError(
        "ADMIN_PASSWORD is unset or still 'changeme'. Set a strong ADMIN_PASSWORD, "
        "or enable OAuth via OAUTH_ENABLED_PROVIDERS."
    )


app = FastAPI(title="notify-proxy", docs_url=None, redoc_url=None)

if ratelimit.enabled():
    app.add_middleware(ratelimit.RateLimitMiddleware)

if auth.oauth_enabled():
    app.add_middleware(
        SessionMiddleware,
        secret_key=os.environ["SESSION_SECRET"],
        same_site="lax",
        https_only=auth.BASE_URL.startswith("https"),
    )

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
        "ALTER TABLE bots ADD COLUMN mattermost_url TEXT",
        "ALTER TABLE bots ADD COLUMN mattermost_token TEXT",
        "ALTER TABLE bots ADD COLUMN mattermost_team TEXT",
        "ALTER TABLE destinations ADD COLUMN mattermost_target TEXT",
        "ALTER TABLE destinations ADD COLUMN mattermost_channel_id TEXT",
        "ALTER TABLE bots ADD COLUMN owner_id INTEGER REFERENCES users(id)",
        "ALTER TABLE bots ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'",
        "ALTER TABLE destinations ADD COLUMN owner_id INTEGER REFERENCES users(id)",
        "ALTER TABLE destinations ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'",
        "ALTER TABLE bots ADD COLUMN slack_url TEXT",
        "ALTER TABLE bots ADD COLUMN discord_url TEXT",
        "ALTER TABLE bots ADD COLUMN smtp_host TEXT",
        "ALTER TABLE bots ADD COLUMN smtp_port INTEGER",
        "ALTER TABLE bots ADD COLUMN smtp_user TEXT",
        "ALTER TABLE bots ADD COLUMN smtp_password TEXT",
        "ALTER TABLE bots ADD COLUMN smtp_from TEXT",
        "ALTER TABLE bots ADD COLUMN smtp_use_tls INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE destinations ADD COLUMN email_to TEXT",
        "ALTER TABLE destinations ADD COLUMN ntfy_priority INTEGER",
    ]:
        try:
            _conn.execute(text(_stmt))
            _conn.commit()
        except Exception:
            pass

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(webhook_router)
