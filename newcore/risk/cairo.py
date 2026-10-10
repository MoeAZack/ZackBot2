"""The trading day: the Africa/Cairo calendar date at the decision time (AUD-07 C13d corrected rule).

For a candle-close decision the decision time is the candle CLOSE, never its open. Egypt observes DST (UTC+2 winter,
UTC+3 summer since 2023), so the offset is resolved per timestamp through the IANA zone (zoneinfo + tzdata), never
hard-coded. Cairo midnight is 22:00 UTC in winter and 21:00 UTC in summer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CAIRO = ZoneInfo('Africa/Cairo')


def cairo_day(ms: int) -> str:
    """'YYYY-MM-DD' Cairo date of an integer UTC-ms instant."""
    if type(ms) is not int:
        raise TypeError(f'integer UTC milliseconds, not {type(ms).__name__}')
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).astimezone(CAIRO).date().isoformat()


def cairo_offset_hours(ms: int) -> int:
    """UTC offset of Cairo at that instant (2 or 3): for display and tests only."""
    off = datetime.fromtimestamp(ms // 1000, tz=timezone.utc).astimezone(CAIRO).utcoffset()
    return int(off.total_seconds() // 3600)
