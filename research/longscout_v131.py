"""Research-only long after an inferred spread shock subsides.

Use the zero-truncated two-day Corwin–Schultz high/low spread proxy
(doi:10.1111/j.1540-6261.2012.01729.x), without overnight adjustment for
continuous UTC bars. Require the prior proxy to exceed twice its baseline
plus 20 bp and the current proxy to fall within 5 bp of baseline. The
baseline is the 20 observations before the shock. Require current range
at least its prior-20-day median, a rising close, and non-bear BTC/coin R1.
The proxy is a research input and does not replace simulation costs.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Tests a transaction-spread proxy from two-day versus single-day ranges, holding '
                  'the current daily range above its recent norm. V82 estimates price impact per '
                  'dollar of volume and V91 uses realized volatility. Neither separates spread '
                  'from underlying range using the two-day relationship.')
_BTC = None


def spread_proxy(high, low):
    valid = np.isfinite(high) & np.isfinite(low) & low.gt(0) & high.ge(low)
    h, l = high.where(valid), low.where(valid)
    log_range = np.log(h / l)
    beta = log_range**2 + log_range.shift()**2
    gamma = np.log(np.maximum(h, h.shift()) / np.minimum(l, l.shift()))**2
    denom = 3 - 2*np.sqrt(2)
    alpha = (np.sqrt(2*beta) - np.sqrt(beta)) / denom - np.sqrt(gamma / denom)
    return 2 * np.tanh(alpha.clip(lower=0) / 2), log_range


def spread_recovery(price, spread, price_range):
    normal = spread.shift(2).rolling(20, min_periods=20).median()
    prior_range = price_range.shift().rolling(20, min_periods=20).median()
    return (spread.shift().gt(2*normal + .002) & spread.le(normal + .0005)
            & price_range.ge(prior_range) & price.diff().gt(0))


def set_btc(frame):
    global _BTC
    close, _ = L101._native_daily(frame)
    close = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    _BTC = L1.regime_daily(close).isin((0., 1.))


def sig_spread_recovery(d, params, ctx):
    close, _ = L101._native_daily(d)
    dates = close.index
    grid = pd.date_range(dates[0], dates[-1], freq='D')
    prices = close.reindex(grid)
    high = pd.Series(d.h.to_numpy(), index=dates).reindex(grid)
    low = pd.Series(d.l.to_numpy(), index=dates).reindex(grid)
    spr, rng = spread_proxy(high, low)
    entry = (spread_recovery(prices, spr, rng) & L1.regime_daily(prices).isin((0., 1.))
             & _BTC.reindex(grid, fill_value=False))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(entry.reindex(dates).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l131_estimated_spread_shock_recovery_1d': dict(
        name='[RESEARCH] L131 estimated spread shock recovery', style='reversal', fn=sig_spread_recovery,
        sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
