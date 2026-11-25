"""Buy a rising nonbear coin after unexpectedly fast relief in Bitcoin fear.

Use the Alternative.me Bitcoin Fear & Greed Index. Fit today's expected daily
index change from BTC's seven-day log return using 60 preceding daily pairs.
A pulse requires yesterday's index <=35, a nonpositive expected index change,
and actual change at least eight points above expectation. Use only complete
daily dates and wait two extra UTC days for the aggregate index. Its price and
volume components mean the residual cannot establish non-price causation.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Tests deviation from a rolling price-based expectation of a published BTC fear '
                  'composite. V194 uses Wikipedia article reads, not index changes or price residuals.')
ALTME_FEAR_GREED_SHA256 = '40bf10237963f8bd4e9faded538c1da3538fc6fee91586fa5fe55974742dcc10'
MARKET_FEATURE = ('ALTME_FEAR_GREED_SHA256', 'Alternative.me Bitcoin Fear & Greed Index')
MARKET_FEATURE_SOURCE = 'Alternative.me Bitcoin Fear & Greed Index public API'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_alternative_me_20261125/manifest.json'
FIELDS = ['fear_greed_index']
_BTC = None


def load_fear_greed():
    raw = Path(os.environ.get('LONGSCOUT_ALTME_FEAR_GREED', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ALTME_FEAR_GREED_SHA256:
        raise ValueError('expected pinned Alternative.me Bitcoin Fear & Greed Index')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS):
        raise ValueError('invalid Alternative.me archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('index dates must be unique and consecutive')
    values = pd.Series([row[1] for row in data['rows']], index=dates, dtype='float64')
    if (values.notna() & ~values.between(0, 100)).any():
        raise ValueError('index observations must lie within 0 to 100')
    return values


def fear_relief(index, btc_seven_day_log_return):
    change = index.diff()
    paired_price = btc_seven_day_log_return.where(change.notna())
    paired_change = change.where(paired_price.notna())
    hist_price = paired_price.shift(1).rolling(60, min_periods=60)
    hist_change = paired_change.shift(1).rolling(60, min_periods=60)
    variance = hist_price.var()
    covariance = hist_price.cov(paired_change.shift(1))
    slope = covariance / variance.where(variance.gt(1e-12))
    expected = hist_change.mean() + slope * (paired_price - hist_price.mean())
    pulse = index.shift().le(35) & expected.le(0) & (change - expected).ge(8)
    return pulse.shift(2, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    complete = btc.rolling(8, min_periods=8).count().eq(8)
    seven_day_return = np.log(btc / btc.shift(7)).where(complete)
    pulse = fear_relief(load_fear_greed().reindex(btc.index), seven_day_return)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_composite_fear_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l202_btc_fear_relief_surprise_1d': dict(
        name='[RESEARCH] L202 BTC composite fear relief beyond prior price expectation', style='momentum',
        fn=sig_composite_fear_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
