# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Instead, use GitHub's [private vulnerability reporting](https://github.com/muhkuh2005/notify-proxy/security/advisories/new)
("Security" tab → "Report a vulnerability"). You'll get a response as soon as
possible, and we'll coordinate a fix and disclosure with you.

## Supported versions

This project is rolling-release; only the latest `main` is supported. Please
make sure you're running the current image before reporting.

## Operational hardening

notify-proxy handles bot tokens and routes notifications. When self-hosting:

- **Set a strong `ADMIN_PASSWORD`** (Basic mode) — the app refuses to start otherwise.
- **Set `COOLIFY_INCOMING_TOKEN`** — the central Coolify webhook is disabled until it is configured.
- **Terminate TLS in front of the admin UI** — Basic credentials and the session cookie travel on every request.
- **Protect the `/data` volume** — and optionally set `TOKEN_ENCRYPTION_KEY` to
  encrypt secret columns (bot tokens, Slack/Discord webhook URLs, SMTP passwords)
  at rest. Without it they are stored as plaintext. Keep the key stable and out
  of the database backup.

### OAuth mode

- **Always set `OAUTH_ADMIN_EMAILS`** when using a public provider (e.g. GitHub).
  With it empty the *first* user to log in becomes admin — a stranger could claim
  it before you do.
- **Use a strong random `SESSION_SECRET`** and keep it stable across instances; it
  signs the session cookie.
- **Set `BASE_URL` to your `https://` URL** so the session cookie is marked
  `Secure` and OAuth redirect URIs are correct.
- **For Microsoft, pin `MICROSOFT_TENANT` to your tenant id** (not the default
  `common`). The admin-email bootstrap only trusts a provider-verified email, but
  restricting the tenant additionally prevents foreign-directory accounts from
  authenticating at all.
- A `global` bot can be *used* (its token sends messages) by any approved user —
  share credentials deliberately. The token itself stays viewable only to its
  owner and admins.
