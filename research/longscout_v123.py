"""Research-only range fade immediately after shortened funding cadence.

Detect a newly published funding event whose declared interval is half
or less than the prior interval. Confirm the observed time gap matches
the new interval. Use only the first completed 4h bar to see the event.
Fade 1% or larger price displacement over three 4h bars when the coin
is sideways and BTC is rising or sideways. Usable event timestamps must
precede the signal close. Simulator cost and funding rates stay fixed.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v117 as FUND
import strategies as S

FAMILY_NOVELTY = ('Tests the first verified change to a faster funding schedule as a market mechanics '
                  'event. V117 tests the sign of settled funding payments and V120 tests premium '
                  'convergence. This signal does not use funding amounts or premia.')
DATA_MANIFEST_SHA256 = FUND.DATA_MANIFEST_SHA256
RUNNER = 'longscout_flow_run.py'
set_panel = FUND.set_panel
set_btc = L1.set_btc
HOUR_MS = 3_600_000


def cadence_change(candle_close_ms, funding):
    stamps = funding.calc_time.to_numpy(dtype=np.int64)
    hours = funding.funding_interval_hours.to_numpy(dtype=np.int64)
    if not (np.all(np.diff(stamps) > 0) and np.all((hours >= 1) & (hours <= 24))):
        raise ValueError('invalid funding event chronology')
    seen = np.searchsorted(stamps, candle_close_ms, side='left') - 1
    changed = np.r_[False, seen[1:] != seen[:-1]] if len(seen) else np.zeros(0, dtype=bool)
    at = (seen >= 1) & changed
    last = seen[at]
    previous = last - 1
    interval_ms = hours[last] * HOUR_MS
    confirmed = np.abs(stamps[last] - stamps[previous] - interval_ms) <= 60_000
    fresh = candle_close_ms[at] - stamps[last] <= interval_ms + 60_000
    compatible = np.zeros(len(seen), dtype=bool)
    compatible[at] = (hours[last] * 2 <= hours[previous]) & confirmed & fresh
    return compatible


def sig_funding_cadence(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    close_ms = (dates + pd.Timedelta(hours=4)).as_unit('ms').asi8
    funding = FUND.load_funding(L101._context_symbol(ctx))
    candidate = cadence_change(close_ms, funding)
    complete = dates.to_series().diff().eq(pd.Timedelta(hours=4)).rolling(3).sum().eq(3).to_numpy()
    displacement = d.c.pct_change(3, fill_method=None).to_numpy()
    common = candidate & complete & (L1.coin_regime(d) == 0) & np.isin(L1.btc_regime(d), (0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(common & (displacement <= -.01), index=d.index)),
                se=S.X(pd.Series(common & (displacement >= .01), index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r123_funding_cadence_change_fade_4h': dict(
        name='[RESEARCH] R123 funding cadence change range fade', style='range', fn=sig_funding_cadence,
        sides='both', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
