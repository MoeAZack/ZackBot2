"""Research-only long when relative stablecoin quote prices normalize.

When the previous completed USDCUSDT spot close is at least 15 basis points
from 1, require at least 5 basis points of contraction toward 1 on the next
completed 4h bar. Enter a rising coin after a rising BTC close. Require
both R1 regimes to be rising or sideways. The spot pair measures relative
quote pricing, and the simulator trades only the frozen futures universe.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Uses normalization of relative stablecoin pricing as a market-wide quote '
                  'liquidity input. V117 uses contract funding and V120 compares futures premium '
                  'with BTC price. Earlier BTC recovery rules do not observe a stablecoin pair.')
STABLECOIN_MANIFEST_SHA256 = 'e4a4e13834bf1d19dd5f39a6c61fc05201a063f57c78dc4e4764d3102c7ae637'
MARKET_FEATURE = ('STABLECOIN_MANIFEST_SHA256', 'USDCUSDT spot 4h close')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_stablecoin_20261125/manifest.json'
_AFTER_STABLE = None


def load_spot():
    path = Path(os.environ.get('LONGSCOUT_STABLECOIN_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if STABLECOIN_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != STABLECOIN_MANIFEST_SHA256:
        raise ValueError('expected pinned stablecoin manifest')
    data = json.loads(raw)
    if (data['status'], data['symbol'], data['timeframe'], data['field']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', 'USDCUSDT', '4h', 'spot_close',
    ):
        raise ValueError('invalid stablecoin source contract')
    ts, values = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(ts, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('stablecoin grid must be unique and consecutive')
    close = pd.to_numeric(pd.Series(values, index=dates))
    return close.where(np.isfinite(close) & close.gt(0))


def normalization(spot):
    distance_bp = ((spot - 1).abs() * 10000).round(8)
    previous = distance_bp.shift()
    return previous.ge(15) & (previous - distance_bp).ge(5)


def set_btc(frame):
    global _AFTER_STABLE
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    prices = pd.Series(frame.c.to_numpy(), index=dates)
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    ret = prices.reindex(grid).diff()
    nonbear = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    _AFTER_STABLE = normalization(load_spot().reindex(grid)) & ret.gt(0) & nonbear.reindex(grid, fill_value=False)


def sig_stable_recovery(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    eligible = _AFTER_STABLE.reindex(dates, fill_value=False).to_numpy() & np.isin(L1.coin_regime(d), (0., 1.))
    contiguous = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    entry = pd.Series(eligible & contiguous & d.c.diff().gt(0).to_numpy(), index=d.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(entry & ~entry.shift(fill_value=False)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l121_relative_stablecoin_normalization_4h': dict(
        name='[RESEARCH] L121 relative stablecoin normalization long', style='momentum',
        fn=sig_stable_recovery, sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
