from __future__ import annotations

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo


def hhmm_to_unix(hhmm: str, on_date: date, tz: ZoneInfo) -> float:
    """Convert an 'HH:MM' string + date in `tz` to a Unix epoch (float seconds)."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid HH:MM: {hhmm!r}")
    local_dt = datetime(
        on_date.year,
        on_date.month,
        on_date.day,
        hour,
        minute,
        tzinfo=tz,
    )
    return local_dt.timestamp()


def signal_date_in_tz(unix_ts: float, tz: ZoneInfo) -> date:
    """Return the date (in `tz`) that `unix_ts` falls on."""
    return datetime.fromtimestamp(unix_ts, tz=tz).date()


def is_within_window(
    trigger_unix: float,
    now_unix: float,
    *,
    past_tolerance: float = 60.0,
    future_tolerance: float = 1800.0,
) -> bool:
    """True if `trigger_unix` is within `[now - past_tolerance, now + future_tolerance]`."""
    return (now_unix - past_tolerance) <= trigger_unix <= (now_unix + future_tolerance)


def format_local_hhmm(unix_ts: float, tz: ZoneInfo) -> str:
    """Format a Unix epoch as 'HH:MM' in the given timezone.

    Example: 1740000000 in America/Sao_Paulo → '10:20'.
    Used by the M7 notifier to render timestamps for self-DMs.
    """
    dt = datetime.fromtimestamp(unix_ts, tz=tz)
    return f"{dt.hour:02d}:{dt.minute:02d}"


def now_unix() -> float:
    """Current wall-clock Unix time as a float. Thin wrapper over time.time()."""
    return time.time()


def monotonic() -> float:
    """Monotonic clock reading (seconds, float). Reserved for M6's scheduler."""
    return time.monotonic()
