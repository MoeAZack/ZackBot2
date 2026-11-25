"""Research-only long after BTC options' implied-volatility premium contracts.

Compare Binance's completed-day BTC BVOL with trailing 30-day realized
volatility, annualized from BTC daily log returns. Require the preceding
spread to be at least 10 volatility points, the spread to contract by
at least two points, and BVOL itself to fall by at least two points.
Enter on the first such completed day with BTC and coin closes rising
and their R1 regimes rising or sideways. Model the next daily open.
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

FAMILY_NOVELTY = ('Compares forward-looking volatility from traded options with trailing realized '
                  'underlying volatility. V84/V91 use realized bar volatility, V116 historical '
                  'cycle energy, and V125 dated futures basis. None uses implied volatility.')
BTC_BVOL_MANIFEST_SHA256 = 'e80a7ef7ff5dede66d48a5ae6ef865ea87fc3a5e6756363f6979be08a3581c50'
MARKET_FEATURE = ('BTC_BVOL_MANIFEST_SHA256', 'BTC options-derived BVOL at each completed day')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_bvol_20261125/manifest.json'
_BTC = None


def load_bvol():
    path = Path(os.environ.get('LONGSCOUT_BTC_BVOL_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if BTC_BVOL_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != BTC_BVOL_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC BVOL manifest')
    data = json.loads(raw)
    if (data['status'], data['symbol'], data['field']) != ('COMPLETE_WITH_EXPLICIT_MASKS', 'BTCBVOLUSDT', 'index_percent'):
        raise ValueError('invalid BVOL source contract')
    dates = pd.DatetimeIndex([row['date'] for row in data['days']])
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BVOL dates must be unique and consecutive')
    values = pd.to_numeric(pd.Series([row.get('index_percent') for row in data['days']], index=dates))
    return values.where(np.isfinite(values) & values.gt(0))


def vol_recovery(price, percent):
    implied = percent.reindex(price.index) / 100
    ret = np.log(price / price.shift())
    realized = ret.rolling(30, min_periods=30).std() * np.sqrt(365)
    spread = implied - realized
    ready = spread.shift().ge(.10) & spread.diff().le(-.02) & implied.diff().le(-.02) & ret.gt(0)
    return ready & ~ready.shift(fill_value=False)


def set_btc(frame):
    global _BTC
    close, _ = L101._native_daily(frame)
    close = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    _BTC = vol_recovery(close, load_bvol()) & L1.regime_daily(close).isin((0., 1.))


def sig_bvol_recovery(d, params, ctx):
    price, _ = L101._native_daily(d)
    dates = price.index
    close = price.reindex(pd.date_range(dates[0], dates[-1], freq='D'))
    entry = _BTC.reindex(close.index, fill_value=False) & close.diff().gt(0) & L1.regime_daily(close).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(entry.reindex(dates).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l127_btc_implied_volatility_relief_1d': dict(
        name='[RESEARCH] L127 BTC implied-volatility relief', style='momentum',
        fn=sig_bvol_recovery, sides='long', tf='1d', research_only=True,
        params={}, mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
