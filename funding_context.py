"""B4 `funding_context_shadow`: point-in-time funding / crowding context for a candidate entry (SHADOW ONLY).

Pure functions, stdlib only, no clock, no network. Nothing here gates, sizes or changes an order; the output is a tag
for research and shadow logs. Contract: issue #13 "DRAFT CONTRACT (Build -> Codex), B4".

funding_ctx(hist, info, t_decision, side) -> dict
  hist        settled funding rows: (fundingTime, fundingRate) pairs or dicts with those keys, or a `History` from
              `prepare_history`. fundingTime is epoch ms (int / numeric str) or a tz-aware datetime.
  info        interval information (see `IntervalLog`): None = no funding_info -> INCOMPLETE.
  t_decision  tz-aware datetime: the decision candle's CLOSE (for the backtest runner this is the trade's t_in, the
              open of the fill bar). A naive datetime raises ValueError.
  side        'LONG' / 'SHORT' (any case) or +1 / -1.

Visibility: a settlement counts iff fundingTime <= t_decision (compared at microsecond resolution; a settlement exactly
at the close counts, one 1 ms later does not). Rows after t_decision are ignored, whatever the caller passes.

OK fields (all computed from visible settlements only):
  last      the last settled rate
  mean3     mean of the last 3 settlements
  z30       (last - mean30) / std30, over settlements in (t - 30d, t]; sample std (ddof=1)
  crowd     sign(mean3) x side: +1 = the trade sides with the paying crowd (long into positive funding, short into
            negative funding), -1 = against it, 0 = mean3 exactly 0
  age_h     hours since the last settlement
  interval_h  the funding interval in force at the last settlement

INCOMPLETE (never imputed; every numeric field is None) when any holds - all applicable reasons are listed:
  no_funding_info       info is None / empty (or every entry malformed)
  bad_funding_info      an interval entry is missing / non-numeric / non-positive / fractional (dropped, not repaired)
  no_history            no settlement visible at t
  lt_30d_history        the first visible settlement is later than t - 30d
  no_interval_in_force  a settlement in the lookback has no interval known (PIT) for it
  gap                   a spacing > 1.5 x the interval in force at the later settlement, inside the 30-day lookback
                        (the pair straddling the window start included)
  stale                 age_h > 1.5 x interval_h
  bad_rate              a non-finite rate in the lookback
  zero_std              std30 is 0 (or < 1e-12): z30 undefined
  lt_3_settlements      fewer than 3 visible settlements (cannot occur with 30d of history; kept as a guard)

Intervals (`IntervalLog`): a list of entries {interval_h, effective_from_ms, known_at_ms}. The interval in force at a
settlement s is the latest entry with effective_from <= s among entries KNOWN at t_decision (known_at <= t). Two sources:
  - `derive_interval_log(history)`: point-in-time, from the settlement spacing itself. A new interval is recognised
    only after two consecutive spacings of the same allowed length (1/2/4/8h); it is effective from the first settlement
    of the new spacing and known only at the confirming settlement, so between the two the spacing reads as a gap
    (INCOMPLETE) - conservative, no lookahead.
  - `interval_log_from_funding_info(rows)`: the collector's fundingInfo change log (funding_info/ALL.csv). Its rows carry
    only `first_seen_utc` (when the collector saw the state, not when it took effect): known_at = first_seen, and the
    interval is assumed in force for all earlier history (effective_from = None). A row first seen after t_decision is
    invisible, so a funding_info snapshot taken after a research cutoff cannot leak into it.
"""
import bisect
import csv
import math
from collections import namedtuple
from datetime import datetime, timezone

H_MS = 3_600_000
DAY_MS = 24 * H_MS
LOOKBACK_MS = 30 * DAY_MS
GAP_MULT = 1.5
STALE_MULT = 1.5
ALLOWED_INTERVALS_H = (1, 2, 4, 8)
JITTER_MS = 60_000                # Binance fundingTime carries a few ms of jitter (e.g. ...400006); snap within 60 s
MIN_STD = 1e-12
NUMERIC = ('last', 'mean3', 'z30', 'crowd', 'age_h', 'interval_h')

History = namedtuple('History', 'times rates')            # parallel tuples, times ascending epoch ms (int)
IntervalEntry = namedtuple('IntervalEntry', 'interval_h effective_from_ms known_at_ms')


# ------------------------------------------------------------------ time helpers
def _dt_to_us(t, what):
    if not isinstance(t, datetime):
        raise TypeError(f'{what} must be a tz-aware datetime, got {type(t).__name__}')
    if t.tzinfo is None or t.tzinfo.utcoffset(t) is None:
        raise ValueError(f'{what} is a naive datetime; pass a tz-aware UTC time')
    d = t.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (d.days * 86_400 + d.seconds) * 1_000_000 + d.microseconds


def _to_ms(v, what='fundingTime'):
    if isinstance(v, bool):
        raise TypeError(f'{what}: bool is not a time')
    if isinstance(v, datetime):
        us = _dt_to_us(v, what)
        if us % 1000:
            raise ValueError(f'{what}: sub-millisecond settlement time')
        return us // 1000
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if not math.isfinite(v) or v != int(v):
            raise ValueError(f'{what}: not an integer epoch ms')
        return int(v)
    if isinstance(v, str):
        return int(v.strip())
    raise TypeError(f'{what}: unsupported type {type(v).__name__}')


def utc_ms(t):
    """tz-aware datetime -> epoch ms (floor). Naive raises."""
    return _dt_to_us(t, 'time') // 1000


# ------------------------------------------------------------------ history
def prepare_history(rows):
    """Normalise settlement rows into a sorted, de-duplicated `History`. Duplicate fundingTimes must agree on the rate."""
    if isinstance(rows, History):
        return rows
    seen = {}
    for r in rows or ():
        if isinstance(r, dict):
            t, x = r.get('fundingTime'), r.get('fundingRate')
        else:
            t, x = r[0], r[1]
        t = _to_ms(t)
        try:
            x = float(x)
        except (TypeError, ValueError):
            x = float('nan')
        if t in seen and not (seen[t] == x or (math.isnan(seen[t]) and math.isnan(x))):
            raise ValueError(f'conflicting duplicate funding settlement at {t}')
        seen[t] = x
    ts = tuple(sorted(seen))
    return History(ts, tuple(seen[t] for t in ts))


def load_funding_csv(path, cutoff):
    """Read a collector funding_rate/<SYM>.csv, keeping ONLY rows with fundingTime < cutoff (tz-aware datetime).
    The file is read forward and reading stops at the first row at/after the cutoff (the collector writes ascending
    order; a descending step before the cutoff raises), so sealed rows are never parsed. Missing file -> None."""
    cut = utc_ms(cutoff)
    out, prev = [], None
    try:
        f = open(path, encoding='utf-8', newline='')
    except FileNotFoundError:
        return None
    with f:
        for r in csv.DictReader(f):
            t = int(r['fundingTime'])
            if t >= cut:
                break
            if prev is not None and t < prev:
                raise ValueError(f'{path}: fundingTime not ascending ({t} after {prev})')
            prev = t
            out.append((t, r['fundingRate']))
    return prepare_history(out)


# ------------------------------------------------------------------ intervals
def _snap_h(d_ms):
    h = round(d_ms / H_MS)
    return h if h in ALLOWED_INTERVALS_H and abs(d_ms - h * H_MS) <= JITTER_MS else None


def derive_interval_log(hist):
    """Point-in-time interval log from the settlement spacing (see module doc). Returns a list of IntervalEntry."""
    h = prepare_history(hist)
    ts, log, cur = h.times, [], None
    for j in range(1, len(ts) - 1):
        a, b = _snap_h(ts[j] - ts[j - 1]), _snap_h(ts[j + 1] - ts[j])
        if a is None or a != b or a == cur:
            continue
        # first settlement ending a new-spacing period is ts[j]; confirmed (known) at ts[j + 1]
        eff = ts[j] if cur is not None else ts[0]          # the very first regime covers the start of history
        log.append(IntervalEntry(a, eff, ts[j + 1]))
        cur = a
    return log


def interval_log_from_funding_info(rows, symbol):
    """fundingInfo change-log rows (funding_info/ALL.csv dicts) for one symbol -> IntervalLog (see module doc)."""
    out = []
    for r in rows or ():
        if str(r.get('symbol', '')).upper() != symbol.upper():
            continue
        fs = str(r.get('first_seen_utc') or '').strip()
        if not fs:
            continue
        known = utc_ms(datetime.fromisoformat(fs.replace('Z', '+00:00')))
        out.append(IntervalEntry(int(float(r['fundingIntervalHours'])), None, known))
    return out


def _hours(v):
    """A usable interval in hours, or None (missing / non-numeric / non-positive / non-integer): never guessed."""
    try:
        h = float(v)
    except (TypeError, ValueError):
        return None
    return int(h) if math.isfinite(h) and h > 0 and h == int(h) else None


def _normalise_info(info):
    """-> (entries, malformed). Malformed entries are dropped and reported, never repaired."""
    if info is None:
        return [], False
    if isinstance(info, dict):                              # a static {'fundingIntervalHours': h}: known for all time
        if not info:
            return [], False
        h = _hours(info.get('fundingIntervalHours', info.get('interval_h')))
        return ([IntervalEntry(h, None, None)], False) if h else ([], True)
    out, bad = [], False
    for e in info:
        if not isinstance(e, IntervalEntry):
            e = IntervalEntry(e.get('interval_h'), e.get('effective_from_ms'), e.get('known_at_ms'))
        h = _hours(e.interval_h)
        if h is None:
            bad = True; continue
        out.append(e._replace(interval_h=h))
    return out, bad


def _interval_at(entries, s_ms):
    """entries already filtered to known ones; latest effective_from <= s (None = -inf; ties -> later known_at)."""
    best, key = None, None
    for e in entries:
        ef = -math.inf if e.effective_from_ms is None else e.effective_from_ms
        if ef > s_ms:
            continue
        k = (ef, -math.inf if e.known_at_ms is None else e.known_at_ms)
        if key is None or k >= key:
            best, key = e.interval_h, k
    return best


# ------------------------------------------------------------------ the feature
def _side(side):
    if isinstance(side, str):
        s = side.strip().upper()
        if s in ('LONG', 'BUY'): return 1
        if s in ('SHORT', 'SELL'): return -1
    elif not isinstance(side, bool) and side in (1, -1):
        return int(side)
    raise ValueError(f'side must be LONG/SHORT or +1/-1, got {side!r}')


def _incomplete(reasons):
    d = {k: None for k in NUMERIC}
    d.update(status='INCOMPLETE', reasons=sorted(set(reasons)))
    return d


def funding_ctx(hist, info, t_decision, side):
    t_us = _dt_to_us(t_decision, 't_decision')
    sd = _side(side)
    h = prepare_history(hist)
    entries, malformed = _normalise_info(info)
    reasons = ['bad_funding_info'] if malformed else []
    if not entries:
        reasons.append('no_funding_info')
    # visible: fundingTime(ms) * 1000 <= t_us  <=>  fundingTime <= floor(t_us / 1000)
    t_ms_floor = t_us // 1000
    n = bisect.bisect_right(h.times, t_ms_floor)
    if n == 0:
        return _incomplete(reasons + ['no_history'])
    ts, xs = h.times[:n], h.rates[:n]
    t_ms = t_us / 1000.0                                    # exact (fractional) decision time for age / window edges
    if ts[0] > t_ms - LOOKBACK_MS:
        reasons.append('lt_30d_history')
    if n < 3:
        reasons.append('lt_3_settlements')
    known = [e for e in (entries or []) if e.known_at_ms is None or e.known_at_ms * 1000 <= t_us]
    lo = bisect.bisect_right(ts, t_ms - LOOKBACK_MS)        # window = (t - 30d, t]
    win_x = xs[lo:]
    for i in range(max(lo, 1), n):                          # pairs whose later settlement is in the window
        iv = _interval_at(known, ts[i])
        if iv is None:
            reasons.append('no_interval_in_force'); continue
        if ts[i] - ts[i - 1] > GAP_MULT * iv * H_MS:
            reasons.append('gap')
    iv_last = _interval_at(known, ts[-1])
    if iv_last is None:
        reasons.append('no_interval_in_force')
    age_h = (t_ms - ts[-1]) / H_MS
    if iv_last is not None and age_h > STALE_MULT * iv_last:
        reasons.append('stale')
    if any(not math.isfinite(x) for x in win_x) or any(not math.isfinite(x) for x in xs[-3:]):
        reasons.append('bad_rate')
    if reasons:
        return _incomplete(reasons)
    m = len(win_x)
    mean30 = sum(win_x) / m
    std30 = math.sqrt(sum((x - mean30) ** 2 for x in win_x) / (m - 1)) if m > 1 else 0.0
    if not std30 >= MIN_STD:
        return _incomplete(['zero_std'])
    last = xs[-1]
    mean3 = sum(xs[-3:]) / 3
    crowd = (mean3 > 0) - (mean3 < 0)
    return dict(status='OK', reasons=[], last=last, mean3=mean3, z30=(last - mean30) / std30, crowd=crowd * sd,
                age_h=age_h, interval_h=iv_last)


# the one prespecified counterfactual (contract B4; fixed in advance, no threshold search)
CF_Z = 2.0


def counterfactual_drop(ctx):
    """True iff the prespecified filter would drop this entry: crowd == +1 and z30 >= 2. INCOMPLETE is never dropped."""
    return bool(ctx.get('status') == 'OK' and ctx['crowd'] == 1 and ctx['z30'] >= CF_Z)
