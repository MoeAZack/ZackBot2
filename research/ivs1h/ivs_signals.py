"""IVS-1H BTC implied-volatility shock (P5_IVS_FROZEN_RULING @82f70688). Research only, default OFF.
V_t = hourly BTCBVOL level from the audited hourly table (valid hours only; invalid/missing = NaN, never filled).
x_t = ln(V_t / V_{t-1}) only when BOTH consecutive calendar hours are valid.
z_t = (x_t - mean(prev 168 x)) / std(prev 168 x, ddof=1), current x excluded; requires 168 consecutive finite x
(i.e. 169 prior valid levels plus the current level). Nonfinite or zero std -> skip.
Global event: z_t >= 3 (LEVEL; no first-cross; no negative arm). Per coin on that closed hour:
futures (c-o) <= -ATR14 -> LONG; (c-o) >= +ATR14 -> SHORT (price units, ATR > 0). No other filter."""
import numpy as np, pandas as pd

WIN, ZMIN = 168, 3.0


def levels(hourly):
    """hourly: DataFrame with hour_open_utc (str), value (str/''), valid (0/1). Returns V on the table's own complete hourly grid."""
    ix = pd.to_datetime(hourly['hour_open_utc'])
    assert ix.is_unique and ix.is_monotonic_increasing and (ix.diff().dropna() == pd.Timedelta(hours=1)).all(), 'hourly grid'
    v = pd.to_numeric(hourly['value'].where(hourly['valid'].astype(int) == 1), errors='coerce').to_numpy(dtype=float, copy=True)
    v[~(np.isfinite(v) & (v > 0))] = np.nan
    return pd.Series(v, index=pd.DatetimeIndex(ix))


def zscores(V):
    """V on a complete consecutive hourly grid. Returns (x, z) series; NaN where undefined."""
    assert (V.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all(), 'V must be on a complete hourly grid'
    v = V.to_numpy(dtype=float); n = len(v)
    x = np.full(n, np.nan); x[1:] = np.log(v[1:] / v[:-1])          # NaN propagates if either level missing
    z = np.full(n, np.nan)
    good = np.isfinite(x).astype(int); run = np.convolve(good, np.ones(WIN, int), 'full')[:n]
    for t in range(WIN, n):
        if np.isfinite(x[t]) and run[t - 1] == WIN:                  # x[t-WIN .. t-1] all finite
            w = x[t - WIN:t]; s = np.std(w, ddof=1)
            if np.isfinite(s) and s > 0: z[t] = (x[t] - np.mean(w)) / s
    return pd.Series(x, index=V.index), pd.Series(z, index=V.index)


def events(z):
    return z.notna() & (z >= ZMIN)


def coin_sides(ev, fut, atr):
    """ev: global event bool series on the futures grid; fut (o,h,l,c) and atr aligned on the same grid. +1 long, -1 short, 0."""
    assert ev.index.equals(fut.index)
    fok = (np.isfinite(fut[['o', 'h', 'l', 'c']]).all(axis=1) & (fut[['o', 'h', 'l', 'c']] > 0).all(axis=1)
           & (fut.h >= fut[['o', 'c']].max(axis=1)) & (fut.l <= fut[['o', 'c']].min(axis=1)))
    a = pd.Series(np.asarray(atr, dtype=float), index=fut.index)
    disp = fut.c - fut.o
    base = ev.fillna(False).astype(bool) & fok & np.isfinite(a) & (a > 0)
    lo = base & (disp <= -a); sh = base & (disp >= a)
    assert not (lo & sh).any()
    side = pd.Series(0, index=fut.index, dtype=int); side[lo] = 1; side[sh] = -1
    return side


def frequency_bound(fill_times, T0, T1):
    """Stage B: upper bound on completed closes under max-1-global = number of DISTINCT scheduled fill timestamps.
    Calendar weeks = (T1 - T0) / 7 days (missing days stay in the denominator). FAIL if < 3/week or < 100."""
    d = len(set(fill_times)); weeks = (T1 - T0) / pd.Timedelta(days=7)
    return dict(distinct_fill_times=d, calendar_weeks=weeks, upper_bound_per_week=d / weeks,
                FREQUENCY_FAIL=bool(d < 100 or d / weeks < 3))


def decide(k, t_arr, atr_arr, elig_ok):
    """Signal on closed bar k (open t_arr[k]). Entry = NEXT bar's open (t_arr[k+1], must be exactly +1h);
    stop distance = 3 * ORIGINAL signal-bar ATR14. Returns (refusal, None) or (None, (fill_time, dist))."""
    if k is None or not elig_ok: return 'ineligible', None
    if k + 1 >= len(t_arr) or t_arr[k + 1] - t_arr[k] != np.timedelta64(1, 'h'): return 'missing_next_hour', None
    return None, (t_arr[k + 1], 3 * float(atr_arr[k]))
