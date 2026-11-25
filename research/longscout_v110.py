"""Research-only daily fade after a coin's directional streaks.

At the first three consecutive positive or negative daily returns after a
return of the opposite sign, consider a fade in the sideways R1 regime.
Use 120 prior predictor days ending two days before the signal. For each
matching three-day streak, measure its next day's return. A bullish fade
requires at least five prior negative streaks and a mean next-day return
of at least +0.4%. The short side mirrors this rule. The latest outcome
used for fitting is known the day before the signal. Fill next daily open.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v27 as L27
import strategies as S

FAMILY_NOVELTY = ('Learns next-day reversal after a three-day directional run. V8 and V13 '
                  'follow persistence after positive streaks. V21 uses unconditional '
                  'autocorrelation, and V105 learns responses to volume events, not sign runs.')


def streak_model(close, direction):
    returns = np.log(close / close.shift())
    signed = direction * returns
    three_days = signed.gt(0).rolling(3, min_periods=3).sum().eq(3)
    predictor = three_days & signed.shift(3).lt(0)
    # At row j, predictor j-2 supplies next-return j-1.
    counted = predictor.shift(2, fill_value=False)
    signed_response = (-direction * returns.shift()).where(counted, 0)
    count = counted.rolling(120, min_periods=120).sum()
    mean_reversal = signed_response.rolling(120, min_periods=120).sum().div(count.where(count > 0))
    ready = close.notna().rolling(125, min_periods=125).sum().eq(125)
    ready &= L27._consec(close.to_frame('c'), 124)
    return predictor & ready & count.ge(5) & mean_reversal.ge(.004)


def sig_streak_fade(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    if dates.hasnans or dates.tz is not None or not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError('daily timestamps must be unique sorted UTC labels')
    close = pd.Series(d.c.to_numpy(), index=dates).where(lambda c: np.isfinite(c) & c.gt(0))
    sideways = L1.regime_daily(close).eq(0)
    long = (sideways & streak_model(close, -1)).to_numpy()
    short = (sideways & streak_model(close, 1)).to_numpy()
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(long, index=d.index)), se=S.X(pd.Series(short, index=d.index)),
                lx=flat, sx=flat.copy())


CANDIDATES = {
    'r110_learned_streak_fade_1d': dict(
        name='[RESEARCH] R110 learned directional-streak fade', style='range', fn=sig_streak_fade,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=3),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
