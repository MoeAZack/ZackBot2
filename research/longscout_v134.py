"""Research-only long after ETH-specific implied risk declines versus BTC.

Require the log ETH/BTC implied-volatility ratio to fall by at least 0.075
on a completed UTC day while BTC implied volatility is flat or rising.
Also require a positive ETH perpetual close return above BTC's return.
Buy a rising coin with rising/sideways coin and BTC R1. This tests relative
risk rotation using two option indices while trading the existing futures
panel and keeping the native simulation model.
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

FAMILY_NOVELTY = ('Compares implied risk between ETH and BTC and requires BTC implied volatility '
                  'to remain flat or rise. V127 tests overall BTC volatility relief relative '
                  'to its own realized volatility. This tests cross-asset risk rotation instead.')
IMPLIED_ROTATION_MANIFEST_SHA256 = '597ef34e1239f39e47047f1645135a981351f04a414a36f3f66e3a876cffaba9'
MARKET_FEATURE = ('IMPLIED_ROTATION_MANIFEST_SHA256', 'BTC/ETH option-implied volatility and ETH perpetual daily close')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_implied_rotation_20261125/manifest.json'
_BTC = None
FIELDS = ['btc_bvol_percent', 'eth_bvol_percent', 'eth_perp_close']


def load_inputs():
    path = Path(os.environ.get('LONGSCOUT_IMPLIED_ROTATION_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if IMPLIED_ROTATION_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != IMPLIED_ROTATION_MANIFEST_SHA256:
        raise ValueError('expected pinned implied rotation manifest')
    data = json.loads(raw)
    if (data['status'], data['timeframe'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS):
        raise ValueError('invalid implied rotation source contract')
    stamp, *values = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(stamp, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('implied rotation dates must be unique and consecutive')
    decoded = pd.DataFrame(dict(zip(FIELDS, values, strict=True)), index=dates).apply(pd.to_numeric)
    return decoded.where(np.isfinite(decoded) & decoded.gt(0))


def rotation(btc, inputs):
    pair = inputs.reindex(btc.index)
    ratio = np.log(pair.eth_bvol_percent / pair.btc_bvol_percent)
    er = pair.eth_perp_close.pct_change(fill_method=None)
    br = btc.pct_change(fill_method=None)
    return ratio.diff().le(-.075) & pair.btc_bvol_percent.diff().ge(0) & er.gt(0) & er.gt(br)


def set_btc(frame):
    global _BTC
    close, _ = L101._native_daily(frame)
    price = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    _BTC = rotation(price, load_inputs()) & L1.regime_daily(price).isin((0., 1.))


def sig_implied_rotation(d, params, ctx):
    close, _ = L101._native_daily(d)
    dates = close.index
    price = close.reindex(pd.date_range(dates[0], dates[-1], freq='D'))
    entry = _BTC.reindex(price.index, fill_value=False) & price.diff().gt(0) & L1.regime_daily(price).isin((0., 1.))
    edge = (entry & ~entry.shift(fill_value=False)).reindex(dates)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(edge.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l134_eth_btc_implied_risk_rotation_1d': dict(
        name='[RESEARCH] L134 ETH versus BTC implied risk rotation', style='momentum', fn=sig_implied_rotation,
        sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
