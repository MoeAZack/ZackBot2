"""Research-only range fade gated by a three-state causal regime filter.

Normalize daily returns by the prior 30-day return standard deviation. Track
range, uptrend and downtrend probabilities using fixed Gaussian emissions
and persistent state transitions. Enter a two-sided fade when the filtered
range probability first crosses 0.70 and the close is more than one prior
20-day log-price standard deviation from the mean. Require nonbear BTC.
Exit at the same rolling mean, loss of coverage or range posterior below 0.5.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Uses a recursively filtered probability of a discrete low-volatility regime. '
                  'V45 uses observable sideways R1; V126 estimates barrier-hit odds; '
                  'V129 decomposes price into continuous trend and transitory levels, without '
                  'a discrete regime posterior.')
TRANSITION = np.array([[.90, .05, .05], [.08, .90, .02], [.08, .02, .90]])
LOCATION = np.array([0., .55, -.55])
SCALE = np.array([.55, 1.20, 1.20])
PRIOR = np.array([1/3, 1/3, 1/3])
_BTC = None


def filter_states(observed):
    posterior = PRIOR.copy()
    output = np.full((len(observed), len(PRIOR)), np.nan)
    for i, value in enumerate(observed):
        if not np.isfinite(value):
            posterior = PRIOR.copy()
            continue
        value = np.clip(value, -20., 20.)
        log_emission = -.5 * ((value - LOCATION) / SCALE) ** 2 - np.log(SCALE)
        emission = np.exp(log_emission - log_emission.max())
        posterior = (posterior @ TRANSITION) * emission
        posterior /= posterior.sum()
        output[i] = posterior
    return pd.DataFrame(output, index=observed.index, columns=['range', 'up', 'down'])


def range_flags(close, btc_nonbear):
    log_price = np.log(close.where(np.isfinite(close) & close.gt(0)))
    ret = log_price.diff()
    scaling = ret.shift().rolling(30, min_periods=30).std()
    probability = filter_states(ret / scaling.where(scaling.gt(0)))['range']
    reference = log_price.shift().rolling(20, min_periods=20)
    dev = reference.std()
    z = (log_price - reference.mean()) / dev.where(dev.gt(0))
    onset = probability.ge(.70) & probability.shift().lt(.70)
    valid_exit = btc_nonbear & probability.ge(.50) & z.notna()
    return (btc_nonbear & onset & z.lt(-1.), btc_nonbear & onset & z.gt(1.),
            ~valid_exit | z.ge(0), ~valid_exit | z.le(0))


def set_btc(frame):
    global _BTC
    close, _ = L101._native_daily(frame)
    close = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    _BTC = L1.regime_daily(close).isin((0., 1.))


def sig_range_posterior(d, params, ctx):
    close, _ = L101._native_daily(d)
    dates = close.index
    full = close.reindex(pd.date_range(dates[0], dates[-1], freq='D'))
    flags = range_flags(full, _BTC.reindex(full.index, fill_value=False))
    return {name: S.X(pd.Series(flag.reindex(dates).to_numpy(), index=d.index))
            for name, flag in zip(('le', 'se', 'lx', 'sx'), flags)}


CANDIDATES = {
    'r148_discrete_range_regime_posterior_1d': dict(
        name='[RESEARCH] R148 Discrete range regime posterior', style='range', fn=sig_range_posterior,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
