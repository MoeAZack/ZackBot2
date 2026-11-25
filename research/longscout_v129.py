"""Research-only daily range fade using a persistent/transitory price model.

Filter log closes with a two-state model. The persistent level is a random
walk and the transitory state decays by 0.6 per day. Noise scales are fixed
fractions of prior-30-return volatility: 0.25 for persistent innovations,
0.45 for transitory innovations and 0.15 for observation error. After
30 updates, trade the first uptick of a negative transitory z-score after
z <= -1.5 and a positive price move. Mirror for shorts. Keep sideways coin
R1, rising/sideways BTC R1, and next-day fills.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Separates permanent and transitory movements in one close stream with an '
                  'online state-space filter. V101 tests mean reversion in a peer-relative spread, '
                  'V83 fits calendar curvature, and V116 fits fixed-cycle phase. None separately '
                  'estimates a persistent state and its transitory observation component.')
_BTC = None
DECAY = .60


def distortion_z(price):
    log_price = np.log(price)
    scale = log_price.diff().shift().rolling(30, min_periods=30).std().to_numpy()
    values = log_price.to_numpy()
    result = np.full(len(price), np.nan)
    warmed = 0
    for i, value in enumerate(values):
        sigma = scale[i]
        if not np.isfinite(value) or not np.isfinite(sigma) or sigma <= 0:
            warmed = 0
            continue
        if warmed == 0:
            level, temporary = value, 0.
            var_level = var_temporary = sigma * sigma
            cross = 0.
            warmed = 1
            continue
        temporary *= DECAY
        var_level += (.25 * sigma)**2
        var_temporary = DECAY**2 * var_temporary + (.45 * sigma)**2
        cross *= DECAY
        level_obs = var_level + cross
        temporary_obs = var_temporary + cross
        error_var = level_obs + temporary_obs + (.15 * sigma)**2
        innovation = value - level - temporary
        level += level_obs * innovation / error_var
        temporary += temporary_obs * innovation / error_var
        var_level -= level_obs * level_obs / error_var
        var_temporary -= temporary_obs * temporary_obs / error_var
        cross -= level_obs * temporary_obs / error_var
        warmed += 1
        if warmed >= 31 and var_temporary > 0:
            result[i] = temporary / np.sqrt(var_temporary)
    return pd.Series(result, index=price.index)


def set_btc(frame):
    global _BTC
    close, _ = L101._native_daily(frame)
    close = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    _BTC = L1.regime_daily(close).isin((0., 1.))


def sig_state_fade(d, params, ctx):
    close, _ = L101._native_daily(d)
    dates = close.index
    price = close.reindex(pd.date_range(dates[0], dates[-1], freq='D'))
    z = distortion_z(price)
    prior, change = z.shift(), z.diff()
    sideways = L1.regime_daily(price).eq(0) & _BTC.reindex(price.index, fill_value=False)
    long = sideways & prior.le(-1.5) & z.lt(0) & change.gt(0) & price.diff().gt(0)
    short = sideways & prior.ge(1.5) & z.gt(0) & change.lt(0) & price.diff().lt(0)
    edge = lambda b: (b & ~b.shift(fill_value=False)).reindex(dates).to_numpy()
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(edge(long), index=d.index)),
                se=S.X(pd.Series(edge(short), index=d.index)), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r129_state_space_transitory_fade_1d': dict(
        name='[RESEARCH] R129 state-space transitory price fade', style='range', fn=sig_state_fade,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
