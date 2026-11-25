"""Research-only long when BTC trading turnover moves toward spot.

On a completed 4h BTC bar, calculate spot quote turnover as a share of
combined spot and perpetual turnover. Require spot share at least five
percentage points above the prior 42-bar median, and spot turnover at
least 25% above its own prior 42-bar median. This requires an increase
in spot trading activity. Buy on the first full condition with BTC and
the coin rising on the bar and both coin R1 and BTC R1 non-bear.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Tests the allocation of same-quote BTC turnover between spot and perpetual '
                  'trading. V112 changes the clock using a single market’s turnover, V98 uses '
                  'taker flows, and V120 uses the premium index. No prior family attributes a '
                  'rise in activity to spot relative to perpetual trading.')
SPOT_TURNOVER_MANIFEST_SHA256 = '88b2035e35ca42dcd224237331356fe090190bfbf3689aabcf362d78b576a58a'
MARKET_FEATURE = ('SPOT_TURNOVER_MANIFEST_SHA256', 'BTCUSDT spot 4h quote turnover')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_spot_turnover_20261125/manifest.json'
_BTC = None


def load_spot_turnover():
    path = Path(os.environ.get('LONGSCOUT_BTC_SPOT_TURNOVER_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if SPOT_TURNOVER_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != SPOT_TURNOVER_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC spot turnover manifest')
    data = json.loads(raw)
    if (data['status'], data['symbol'], data['timeframe'], data['field']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', 'BTCUSDT', '4h', 'spot_quote_volume',
    ):
        raise ValueError('invalid BTC turnover source contract')
    ts, values = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(ts, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('BTC spot turnover grid must be unique and consecutive')
    volumes = pd.to_numeric(pd.Series(values, index=dates))
    return volumes.where(np.isfinite(volumes) & volumes.gt(0))


def spot_share_accelerates(spot, perp):
    valid = np.isfinite(spot) & np.isfinite(perp) & spot.gt(0) & perp.gt(0)
    spot = spot.where(valid)
    share = spot / (spot + perp.where(valid))
    prior_share = share.shift().rolling(42, min_periods=42).median()
    prior_spot = spot.shift().rolling(42, min_periods=42).median()
    return share.ge(prior_share + .05) & spot.ge(1.25 * prior_spot)


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    rows = pd.DataFrame(dict(close=frame.c.to_numpy(), qv=frame.qv.to_numpy()), index=dates)
    grid = rows.reindex(pd.date_range(dates[0], dates[-1], freq='4h'))
    spot = load_spot_turnover().reindex(grid.index)
    nonbear = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    _BTC = spot_share_accelerates(spot, grid.qv) & grid.close.diff().gt(0) & nonbear.reindex(grid.index, fill_value=False)


def sig_spot_lead(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    contiguous = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    raw = _BTC.reindex(dates, fill_value=False).to_numpy()
    entry = raw & contiguous & d.c.diff().gt(0).to_numpy() & np.isin(L1.coin_regime(d), (0., 1.))
    le = pd.Series(entry, index=d.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(le & ~le.shift(fill_value=False)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l122_btc_spot_turnover_lead_4h': dict(
        name='[RESEARCH] L122 BTC spot turnover lead', style='momentum', fn=sig_spot_lead,
        sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
