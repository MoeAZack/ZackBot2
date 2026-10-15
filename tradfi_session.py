"""TRADFI-01 T2: US cash-session gate for tradfi ENTRIES. Pure functions (no clock, no I/O).

ENTRY gating only - position management (stops, targets, time exits, flatten, reconcile) never consults this module;
an open position is always managed regardless of the cash session.

Times are evaluated in zoneinfo 'America/New_York' (DST-correct). Regular session 09:30 <= local < 16:00, early-close
days 09:30 <= local < 13:00. Outside the static table's years, or if zoneinfo/tzdata is unavailable -> 'UNKNOWN'.
"""
from datetime import date, datetime, time, timezone

from tradfi_universe import GOLD_FAMILY, STOCK_SET, OIL_SET

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo('America/New_York')
except Exception:  # zoneinfo or tzdata missing -> every query is UNKNOWN (fail closed for gated entries)
    _NY = None

# NYSE holiday / early-close table. Source: NYSE "Holidays & Trading Hours" (nyse.com/markets/hours-calendars),
# 2026 and 2027 schedules. Re-verify against nyse.com when extending the table.
NYSE_FULL_HOLIDAYS = frozenset({
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Washington's Birthday
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed; Jul 4 is a Saturday)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving Day
    date(2026, 12, 25),  # Christmas Day
    # 2027
    date(2027, 1, 1),    # New Year's Day
    date(2027, 1, 18),   # Martin Luther King Jr. Day
    date(2027, 2, 15),   # Washington's Birthday
    date(2027, 3, 26),   # Good Friday
    date(2027, 5, 31),   # Memorial Day
    date(2027, 6, 18),   # Juneteenth (observed; Jun 19 is a Saturday)
    date(2027, 7, 5),    # Independence Day (observed; Jul 4 is a Sunday)
    date(2027, 9, 6),    # Labor Day
    date(2027, 11, 25),  # Thanksgiving Day
    date(2027, 12, 24),  # Christmas Day (observed; Dec 25 is a Saturday)
})
NYSE_EARLY_CLOSES = frozenset({  # 13:00 ET close
    date(2026, 11, 27),  # day after Thanksgiving
    date(2026, 12, 24),  # Christmas Eve
    date(2027, 11, 26),  # day after Thanksgiving
})
TABLE_YEARS = frozenset({2026, 2027})

OPEN_T, CLOSE_T, EARLY_CLOSE_T = time(9, 30), time(16, 0), time(13, 0)
SESSION_GATED_FAMILIES = frozenset({'range', 'scalp'})  # VWAPREV / SCALP


def _to_utc(ts_utc):
    if isinstance(ts_utc, datetime):
        return ts_utc.replace(tzinfo=timezone.utc) if ts_utc.tzinfo is None else ts_utc
    return datetime.fromtimestamp(float(ts_utc), tz=timezone.utc)  # epoch seconds


def us_cash_session(ts_utc):
    """ts_utc (epoch seconds or datetime; naive = UTC) -> 'OPEN' | 'CLOSED' | 'UNKNOWN'."""
    tz = _NY
    if tz is None: return 'UNKNOWN'
    try:
        local = _to_utc(ts_utc).astimezone(tz)
    except Exception:
        return 'UNKNOWN'
    d = local.date()
    if d.year not in TABLE_YEARS: return 'UNKNOWN'
    if d.weekday() >= 5 or d in NYSE_FULL_HOLIDAYS: return 'CLOSED'
    close = EARLY_CLOSE_T if d in NYSE_EARLY_CLOSES else CLOSE_T
    return 'OPEN' if OPEN_T <= local.time() < close else 'CLOSED'


def entry_allowed(symbol, family, ts_utc):
    """May a NEW tradfi entry be opened? Gold -> True (no cash-session gate). Stocks: family in {'range','scalp'} only when
    OPEN; other families True unless UNKNOWN (fail closed). Oil -> always False. Non-tradfi symbols -> True (not gated here).
    Position management never consults this."""
    if symbol in OIL_SET or family == 'oil': return False
    if symbol in GOLD_FAMILY: return True
    if symbol in STOCK_SET:
        st = us_cash_session(ts_utc)
        if family in SESSION_GATED_FAMILIES: return st == 'OPEN'
        return st != 'UNKNOWN'
    return True
