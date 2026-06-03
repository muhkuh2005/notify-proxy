# Contributing

Thanks for your interest in improving notify-proxy!

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# the app refuses to start without a password
ADMIN_PASSWORD=dev uvicorn app.main:app --reload
```

## Running tests

```bash
pytest -q
```

CI runs the same suite on every pull request (Python 3.12).

## Pull requests

- Keep changes focused; one logical change per PR.
- Add or update tests for behavior changes.
- Make sure `pytest` passes locally before pushing.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for commit
  messages (e.g. `fix(webhook): ...`, `feat(admin): ...`).

## Reporting bugs / requesting features

Use the issue templates. For security issues, see [SECURITY.md](SECURITY.md) —
please do not file them as public issues.

## Never commit secrets

No tokens, passwords, real hostnames, or `.env` files. All configuration is via
environment variables (see [`.env.example`](.env.example)).
