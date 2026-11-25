"""Research-only long with a failed intraday downside auction.

On six complete UTC 4h bars, the first occurrence of the session's low must
be in the first half and the first occurrence of the session's high must
be in the second half. The low must test below the prior complete day's
low and the final close must reclaim that level in the top quarter of
the session's range. R1 coin regime must be rising or sideways.

Generate the signal on the 20:00 UTC bar's close. Fill at the next 4h open
under the frozen simulator. The ordering is between bars only.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Requires the complete daily extrema to occur in opposing half-sessions, in the '
                  'low-then-high order. V93 uses half-day return displacement, V99 uses early '
                  'volume share, and V69 is a daily candle-body pattern. Those inputs do not '
                  'establish the within-day order of extremes.')


def auction_recovery(d):
    t = pd.to_datetime(d.t)
    if t.isna().any() or t.dt.tz is not None or not t.is_unique or not t.is_monotonic_increasing:
        raise ValueError('4h timestamps must be sorted unique UTC-naive labels')
    expected = t.dt.floor('4h')
    if not t.eq(expected).all():
        raise ValueError('candles must start at exact UTC 4h boundaries')
    day = t.dt.normalize()
    valid = d[['h', 'l', 'c']].apply(lambda p: np.isfinite(p) & (p > 0)).all(axis=1)
    valid &= d.h.ge(d.c) & d.l.le(d.c)
    h = d.h.where(valid)
    l = d.l.where(valid)
    highs = h.groupby(day).transform('max')
    lows = l.groupby(day).transform('min')
    first_low = t.where(l.eq(lows)).groupby(day).min().dt.hour
    first_high = t.where(h.eq(highs)).groupby(day).min().dt.hour
    days = pd.DataFrame({
        'h': h.groupby(day).max(), 'l': l.groupby(day).min(),
        'c': d.c.where(valid & t.dt.hour.eq(20)).groupby(day).max(),
        'complete': valid.groupby(day).sum().eq(6),
        'first_low': first_low, 'first_high': first_high,
    })
    prior_day_ok = days['complete'].shift(fill_value=False) & days.index.to_series().diff().eq(pd.Timedelta(days=1))
    prior_low = days.l.shift().where(prior_day_ok)
    close_position = (days.c - days.l) / (days.h - days.l).where(days.h.gt(days.l))
    hit = (days['complete'] & days.first_low.lt(12) & days.first_high.ge(12)
           & days.l.lt(prior_low) & days.c.gt(prior_low) & close_position.ge(.75))
    return t.dt.hour.eq(20) & day.map(hit).fillna(False)


def sig_auction_recovery(d, params, ctx):
    base = auction_recovery(d)
    ready = base & np.isin(L1.coin_regime(d), (0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(ready), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l109_ordered_intraday_extrema_reclaim_4h': dict(
        name='[RESEARCH] L109 ordered intraday auction reclaim', style='reversal', fn=sig_auction_recovery,
        sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
