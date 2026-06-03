from datetime import datetime, timezone


def now_utc() -> datetime:
    """Timezone-aware current UTC time (replaces the deprecated datetime.utcnow)."""
    return datetime.now(timezone.utc)
