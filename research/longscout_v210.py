"""Research-only long after SPX option skew falls while implied variance rises.

Require previous SKEW at least ten points above its prior 30-session median.
On the next session require SKEW to fall by at least five points while VIX
rises by at least 5%. Wait two extra UTC days. Buy a rising nonbear target
in nonbear BTC. Lower SKEW refers to priced asymmetry and cannot establish
a decrease in physical crash probability.
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

FAMILY_NOVELTY = ('Tests opposing repricing of asymmetry and dispersion in SPX options. '
                  'V174 compares implied dispersion at two maturities. V185 uses '
                  'dispersion of VIX options while SPX implied dispersion stays stable.')
CBOE_TAIL_DISPERSION_SHA256 = '9ffbc0f817b60ab287db0109b6e634c9d050ae077a0ee11db279f2ceb9ade45f'
MARKET_FEATURE = ('CBOE_TAIL_DISPERSION_SHA256', 'Cboe SKEW and VIX paired US session closes')
MARKET_FEATURE_SOURCE = 'Cboe Global Indices SKEW and VIX daily index archive'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_cboe_skew_20261125/manifest.json'
FIELDS = ['SKEW', 'VIX']
_BTC = None


def load_option_indices():
    raw = Path(os.environ.get('LONGSCOUT_CBOE_TAIL_DISPERSION', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CBOE_TAIL_DISPERSION_SHA256:
        raise ValueError('expected pinned Cboe SKEW and VIX index history')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', 'US equity session', FIELDS):
        raise ValueError('invalid Cboe tail and dispersion history contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if (not dates.is_unique or not dates.is_monotonic_increasing
            or not dates.equals(dates.normalize()) or (dates.dayofweek >= 5).any()):
        raise ValueError('Cboe session dates must be unique sorted weekday labels')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype=float)


def asymmetric_risk_eases(indices):
    complete = (np.isfinite(indices) & indices.gt(0)).all(axis=1)
    prices = indices.where(complete, axis=0)
    prior_median = prices.SKEW.shift(2).rolling(30, min_periods=30).median()
    elevated = prices.SKEW.shift().sub(prior_median).ge(10)
    skew_lower = prices.SKEW.diff().le(-5)
    vix_higher = prices.VIX.pct_change(fill_method=None).ge(.05)
    near_session = prices.index.to_series().diff().dt.days.between(1, 4)
    pulse = elevated & skew_lower & vix_higher & near_session
    return pulse.set_axis(pulse.index + pd.Timedelta(days=2))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    pulse = asymmetric_risk_eases(load_option_indices()).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_tail_risk_eases(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    eligible = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (eligible & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l210_spx_skew_relief_against_vix_rise_1d': dict(
        name='[RESEARCH] L210 SPX skew reprices against VIX rise', style='momentum',
        fn=sig_tail_risk_eases, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
