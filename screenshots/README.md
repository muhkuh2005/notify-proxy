# notify-proxy Screenshots

This directory contains HTML snapshots of the notify-proxy admin UI captured for documentation purposes.

## Files

- **01-admin-dashboard.html** - Main admin dashboard showing projects and routing overview
- **02-new-project.html** - New project creation form
- **03-bots-list.html** - List of configured notification bots (Telegram, ntfy, Slack, Discord, Mattermost, Email)
- **04-settings.html** - Application settings page

## Notes

These are HTML snapshots for documentation reference. The actual application features:

- **Central Coolify router** — one incoming webhook, routed by application_uuid, app name, or server_uuid
- **Per-project webhooks** — tokenized URLs for non-Coolify sources
- **Multiple notifiers** — Telegram, ntfy, Mattermost, Slack, Discord, Email
- **Filtering** — all, errors_only, or off (configurable per project and destination)
- **OAuth authentication** — GitHub and Microsoft365 login support
- **Admin UI** — HTTP Basic Auth or OAuth with approval workflow

To view these HTML files, simply open them in any web browser.
