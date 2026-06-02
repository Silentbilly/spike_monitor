"""Time utilities."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


def ts_ms() -> int:
    """Current UTC time as milliseconds since epoch."""
    return int(utcnow().timestamp() * 1000)


def hours_since(dt: datetime) -> float:
    """Hours elapsed since dt (tz-aware)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = utcnow() - dt
    return delta.total_seconds() / 3600.0


def format_utc(dt: datetime) -> str:
    """ISO-8601 UTC string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")
