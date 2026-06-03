# notify-proxy

A small self-hosted webhook router that turns [Coolify](https://coolify.io) (and generic) webhook events into **Telegram**, **[ntfy](https://ntfy.sh)**, **[Mattermost](https://mattermost.com)**, **Slack**, **Discord** and **Email** notifications — with a web admin UI for managing bots, projects and per-destination routing.

Point Coolify's notification webhook at one URL, then fan events out to whichever chats/topics you want, with per-project and per-destination filtering (all events / errors only / off).

## Features

- **Central Coolify router** — one incoming webhook, routed to the right project by `application_uuid`, app name, or `server_uuid`, with a configurable default catch-all.
- **Per-project direct webhooks** — each project also gets its own tokenized URL for non-Coolify sources.
- **Multiple notifiers** — Telegram, ntfy, Mattermost (REST API v4; DMs/channels), Slack & Discord (incoming webhooks), and Email (SMTP); credentials are stored once per *bot* and reused across destinations.
- **Filtering** — `all`, `errors_only`, or `off`, configurable per project and overridable per destination.
- **Telegram chat-ID verification** — resolve `@usernames` to chat IDs and verify private chats via a dedicated verification bot.
- **Admin UI** — HTTP Basic Auth, or **OAuth login** (GitHub / Microsoft365) with admin approval and per-resource ownership; sync projects directly from the Coolify API.

## Quick start

```bash
cp .env.example .env
# edit .env — ADMIN_PASSWORD is REQUIRED (the app refuses to start without it)
docker compose up -d
```

The admin UI is served on port `8000` (put it behind your own reverse proxy / TLS). Data is persisted in the `/data` volume (SQLite).

> The app **refuses to boot** if `ADMIN_PASSWORD` is unset or still `changeme`. Set a strong one.

## Configuration

All configuration is via environment variables:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ADMIN_USER` | no | `admin` | Admin UI username (HTTP Basic) |
| `ADMIN_PASSWORD` | **yes** | — | Admin UI password; app won't start if unset/`changeme` |
| `DATABASE_URL` | no | `sqlite:////data/notify-proxy.db` | SQLAlchemy database URL |
| `TOKEN_ENCRYPTION_KEY` | no | — | Fernet key; encrypts secret columns (tokens, webhook URLs, SMTP pw) at rest |
| `RATELIMIT_ENABLED` | no | `true` | In-memory per-IP rate limiting on login + webhooks |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `COOLIFY_BASE_URL` | no | — | Coolify instance URL, enables "Sync from Coolify" |
| `COOLIFY_TOKEN` | no | — | Coolify API token (for sync) |
| `COOLIFY_INCOMING_TOKEN` | no | — | Shared token Coolify must include in the webhook URL |
| `VERIFICATION_BOT_TOKEN` | no | — | Telegram bot used for chat-ID verification (also settable in the Settings UI) |
| `NOTIFY_PROXY_IMAGE` | no | `ghcr.io/your-org/notify-proxy:main` | Image reference used by `docker-compose.yaml` |
| `OAUTH_ENABLED_PROVIDERS` | no | — | Comma list of `github,microsoft`. When set, OAuth replaces Basic Auth |
| `SESSION_SECRET` | with OAuth | — | Long random string signing the session cookie (required in OAuth mode) |
| `BASE_URL` | with OAuth | — | Public URL; OAuth redirect = `{BASE_URL}/auth/{provider}/callback` |
| `OAUTH_ADMIN_EMAILS` | no | — | Emails auto-promoted to admin; if empty, the **first** login becomes admin |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | for GitHub | — | GitHub OAuth app credentials |
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` | for MS | — | Entra/Azure AD app credentials |
| `MICROSOFT_TENANT` | no | `common` | Tenant id, or `common` / `organizations` |

## Authentication

Two modes, selected automatically:

- **Basic Auth** (default): single admin via `ADMIN_USER` / `ADMIN_PASSWORD`.
- **OAuth** (when `OAUTH_ENABLED_PROVIDERS` is set): users sign in with GitHub or
  Microsoft365. **A successful login does not grant access** — new users land in a
  *pending* state until an admin approves them. Admins can approve/block/promote
  users under **Users**.

  Bootstrapping the first admin: any email in `OAUTH_ADMIN_EMAILS` becomes
  admin+approved on login. If that list is empty, the **first** user to log in
  becomes admin — so with public providers (e.g. GitHub), **always set
  `OAUTH_ADMIN_EMAILS`** to avoid a stranger claiming admin first.

  Each OAuth user owns the bots/destinations they create (`private` by default).
  Marking a resource `global` lets other approved users *use* it (and, for bots,
  send through its token); editing stays with the owner. Admins can do everything.

  Register the OAuth app's redirect URI as `{BASE_URL}/auth/<provider>/callback`.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check (`{"status":"ok"}`) |
| `POST` | `/webhook/coolify/{token}` | Central Coolify router; `token` must equal `COOLIFY_INCOMING_TOKEN` |
| `POST` | `/webhook/{token}` | Per-project direct webhook (token shown in the project's admin page) |
| `GET` | `/admin` | Admin UI (HTTP Basic Auth) |

### Wiring up Coolify

In Coolify → **Team Settings → Notifications → Webhook**, set the URL to:

```
https://<your-host>/webhook/coolify/<COOLIFY_INCOMING_TOKEN>
```

Then open the admin UI, **Sync from Coolify**, and attach Telegram/ntfy destinations to each project.

## Tech stack

FastAPI · SQLAlchemy 2 · SQLite · Jinja2 · httpx. Runs as a single container (`uvicorn`).

## Development

```bash
pip install -r requirements.txt
ADMIN_PASSWORD=dev uvicorn app.main:app --reload
```

## Security notes

- **Set `COOLIFY_INCOMING_TOKEN`.** The `/webhook/coolify/{token}` endpoint is only enabled when this token is configured; requests must include it. Leave it unset and the endpoint stays disabled (returns 404).
- **Bot tokens are stored unencrypted** in the SQLite database. Protect the `/data` volume and keep the database off shared storage.
- **Put the admin UI behind TLS** (reverse proxy). HTTP Basic Auth sends credentials in every request.

## License

[MIT](LICENSE) © 2026 Pascal Borkenhagen
