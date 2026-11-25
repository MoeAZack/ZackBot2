"""Research-only long after simultaneous spot-triangle prices converge.

For each completed 4h bar use the median log triangular residual from its
last three traded, time-matched minutes: BTCUSDT minus BTCUSDC minus
USDCUSDT in log prices. When the previous absolute residual is at least
10 bp, require it to halve or better, with rising BTC and coin closes and
rising/sideways R1 on both. The three quotes are market data features and
the simulator opens only the existing futures position.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Uses the no-arbitrage relation among three simultaneous traded spot markets. '
                  'V121 observes the stablecoin rate itself, and V120/V125 observe derivative '
                  'pricing. The triangular residual removes the stablecoin cross-rate.')
SPOT_TRIANGLE_MANIFEST_SHA256 = '76336cedfbe11b006f081d6b8b06d6d812fb2ac9959a5dd39ab8771e0b0516c8'
MARKET_FEATURE = ('SPOT_TRIANGLE_MANIFEST_SHA256', 'BTCUSDT/BTCUSDC/USDCUSDT synchronized triangular close residual')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_spot_triangle_20261125/manifest.json'
_BTC = None


def load_residuals():
    path = Path(os.environ.get('LONGSCOUT_SPOT_TRIANGLE_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if SPOT_TRIANGLE_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != SPOT_TRIANGLE_MANIFEST_SHA256:
        raise ValueError('expected pinned spot triangle manifest')
    data = json.loads(raw)
    if (data['status'], data['symbols'], data['timeframe'], data['field']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', ['BTCUSDT', 'BTCUSDC', 'USDCUSDT'], '4h',
        'median_log_triangle_residual_last_three_traded_minutes',
    ):
        raise ValueError('invalid spot triangle source contract')
    ts, values = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(ts, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('triangle grid must be unique and consecutive')
    residual = pd.to_numeric(pd.Series(values, index=dates))
    return residual.where(np.isfinite(residual))


def convergence(residual):
    distance = residual.abs()
    previous = distance.shift()
    return previous.ge(.001) & distance.le(.5 * previous)


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    btc = pd.Series(frame.c.to_numpy(), index=dates)
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    price = btc.reindex(grid)
    nonbear = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    _BTC = (convergence(load_residuals().reindex(grid)) & price.diff().gt(0)
            & nonbear.reindex(grid, fill_value=False))


def sig_triangular_close(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    consecutive = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    supported = (_BTC.reindex(dates, fill_value=False).to_numpy() & consecutive
                 & np.isin(L1.coin_regime(d), (0., 1.)) & d.c.diff().gt(0).to_numpy())
    entry = pd.Series(supported, index=d.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(entry & ~entry.shift(fill_value=False)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l132_spot_triangle_convergence_4h': dict(
        name='[RESEARCH] L132 spot triangle convergence long', style='momentum', fn=sig_triangular_close,
        sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
