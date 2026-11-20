"""V102 prospective bar-based auction value hypotheses, research-only/default OFF.

For each observed UTC day, the PRIOR 20 complete consecutive days supply 120
4h bars. A 20-bin equal-log-price histogram allocates each bar's quote volume to
its typical price (H+L+C)/3. POC is the midpoint of the largest-weight bin,
with lowest-bin ties; expand the value area by the heavier adjacent bin until
70% of total weight is included, choosing the lower bin on a tie. This is a
bar-based approximation, not true tick-level volume at price.

L102: two consecutive completed daily closes each above its own prior profile's
VAH; first full condition, R1 rising/sideways. R102: completed day low below VAL
and close strictly between VAL and POC -> long; mirror high above VAH and close
strictly between POC and VAH -> short. First condition per direction, R1
sideways. Signal next day 00-open bar / modeled 04-open fill; 2.5 ATR, 2R,
30 bars. R102 exits on a 4h close reaching the current day's prior-data POC.
Each day's profile is available from 00:00 even if that day is still partial.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v92 as H


def profile_window(bars):
    """Return prior-window POC, VAL and VAH, or None for invalid input."""
    if len(bars) != 120 or not {'t', 'h', 'l', 'c', 'qv'}.issubset(bars.columns):
        return None
    t = pd.DatetimeIndex(pd.to_datetime(bars.t))
    if t.hasnans or t.tz is not None or t[0] != t[0].normalize():
        return None
    if not np.all(t[1:] - t[:-1] == pd.Timedelta(hours=4)):
        return None
    try:
        a = bars[['h', 'l', 'c', 'qv']].to_numpy(dtype=float)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(a).all():
        return None
    high, low, close, weight = a.T
    if (low <= 0).any() or (high < low).any() or (close < low).any() or (close > high).any() or (weight < 0).any():
        return None
    total = float(weight.sum())
    lo, hi = float(low.min()), float(high.max())
    if not np.isfinite(total) or total <= 0 or hi <= lo:
        return None
    left, right = np.log(lo), np.log(hi)
    edges = np.linspace(left, right, 21)
    typical = (high + low + close) / 3.
    if not np.isfinite(typical).all():
        return None
    bins = np.clip(np.searchsorted(edges, np.log(typical), side='right') - 1, 0, 19)
    volumes = np.bincount(bins, weights=weight, minlength=20)
    poc = int(np.argmax(volumes))  # numpy chooses the first, i.e. lower-price, tie
    lower = upper = poc
    accumulated = float(volumes[poc])
    while accumulated < .70 * total and (lower > 0 or upper < 19):
        below = float(volumes[lower-1]) if lower > 0 else -1.
        above = float(volumes[upper+1]) if upper < 19 else -1.
        if below >= above:
            lower -= 1
            accumulated += below
        else:
            upper += 1
            accumulated += above
    return dict(poc=float(np.exp((edges[poc]+edges[poc+1])/2.)),
                val=float(np.exp(edges[lower])), vah=float(np.exp(edges[upper+1])))


def daily_profiles(d):
    """Compute each observed day's profile exclusively from the prior 20 days."""
    t = pd.to_datetime(d.t)
    if t.isna().any() or t.dt.tz is not None:
        raise ValueError('timestamps must be UTC-naive and valid')
    if not t.is_monotonic_increasing or t.duplicated().any():
        raise ValueError('timestamps must be sorted and unique')
    byday = {day: group for day, group in d.groupby(t.dt.normalize(), sort=True)}
    rows = {}
    for day in byday:
        prior = [day-pd.Timedelta(days=k) for k in range(20, 0, -1)]
        if not all(x in byday for x in prior):
            continue
        result = profile_window(pd.concat([byday[x] for x in prior], ignore_index=True))
        if result is not None:
            rows[day] = result
    if not rows:
        return pd.DataFrame(columns=['poc', 'val', 'vah'], index=pd.DatetimeIndex([], name='day'), dtype=float)
    out = pd.DataFrame.from_dict(rows, orient='index')
    out.index.name = 'day'
    return out.sort_index()


def daily_events(d, profiles=None):
    """Statistical entry edges on complete days; regimes are applied later."""
    D = H.daily(d)
    times = pd.to_datetime(d.t)
    exact = times.groupby(times.dt.normalize()).apply(
        lambda x: list(x) == [x.iloc[0].normalize()+pd.Timedelta(hours=h) for h in range(0, 24, 4)])
    D = D[exact.reindex(D.index, fill_value=False)]
    P = daily_profiles(d) if profiles is None else profiles
    p = P.reindex(D.index)
    previous_day = D.index.to_series().diff().eq(pd.Timedelta(days=1))
    above = (D.c > p.vah).fillna(False)
    accepted = above & above.shift(fill_value=False) & previous_day
    acceptance_edge = accepted & ~accepted.shift(fill_value=False)
    lower = ((D.l < p.val) & (D.c > p.val) & (D.c < p.poc)).fillna(False)
    upper = ((D.h > p.vah) & (D.c < p.vah) & (D.c > p.poc)).fillna(False)
    lower_edge = lower & ~(lower.shift(fill_value=False) & previous_day)
    upper_edge = upper & ~(upper.shift(fill_value=False) & previous_day)
    return D, dict(acceptance=acceptance_edge.fillna(False), long=lower_edge.fillna(False), short=upper_edge.fillna(False))


def _empty(d):
    return {key: pd.Series(False, index=d.index) for key in ('le', 'se', 'lx', 'sx')}


def sig_acceptance(d, p, ctx):
    profiles = daily_profiles(d)
    D, flags = daily_events(d, profiles)
    out = _empty(d)
    gate = np.isin(L1.coin_regime(d), (0., 1.))
    out['le'] = S.X(pd.Series(L27._next_day(flags['acceptance'], D, d) & gate, index=d.index))
    return out


def sig_rejection(d, p, ctx):
    profiles = daily_profiles(d)
    D, flags = daily_events(d, profiles)
    out = _empty(d)
    gate = L1.coin_regime(d) == 0
    for side, key in (('le', 'long'), ('se', 'short')):
        out[side] = S.X(pd.Series(L27._next_day(flags[key], D, d) & gate, index=d.index))
    days = pd.DatetimeIndex(pd.to_datetime(d.t).dt.normalize())
    poc = profiles.poc.reindex(days).to_numpy(dtype=float)
    close = pd.to_numeric(d.c, errors='coerce').to_numpy(dtype=float)
    out['lx'] = S.X(pd.Series(np.isfinite(poc) & np.isfinite(close) & (close >= poc), index=d.index))
    out['sx'] = S.X(pd.Series(np.isfinite(poc) & np.isfinite(close) & (close <= poc), index=d.index))
    return out


CANDIDATES = {
    'l102_auction_acceptance_4h': dict(name='[RESEARCH] L102 bar-profile value acceptance', style='momentum',
        fn=sig_acceptance, sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2., max_bars=30)),
    'r102_auction_rejection_4h': dict(name='[RESEARCH] R102 bar-profile value rejection', style='range',
        fn=sig_rejection, sides='both', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2., max_bars=30)),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
