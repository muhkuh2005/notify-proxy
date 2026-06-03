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

- **Set a strong `ADMIN_PASSWORD`** — the app refuses to start otherwise.
- **Set `COOLIFY_INCOMING_TOKEN`** — the central Coolify webhook is disabled until it is configured.
- **Terminate TLS in front of the admin UI** — HTTP Basic Auth sends credentials on every request.
- **Protect the `/data` volume** — bot tokens are stored unencrypted in SQLite.
