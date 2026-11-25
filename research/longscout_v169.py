"""Research-only long on broadening Bitcoin mining-pool participation.

For each completed publisher week, require the top three attributed pools'
share to dip at least four percentage points below its preceding eight-week
median while aggregate estimated hashrate changes by at most 7.5%. Use a
two-day buffer after the Monday UTC cutoff. Enter after the first qualifying
week with nonbear BTC and a positive-return target in nonbear R1.
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

FAMILY_NOVELTY = ('Tests dispersion of work across publicly attributed mining pools while '
                  'aggregate hashrate stays near stable. V139 uses the aggregate amount '
                  'of work, and V152 uses scheduled subsidy economics.')
POOL_CONCENTRATION_SHA256 = 'e34fff13d61d985b5fcc9532750e9d06c61c4ba6d5c37192cdba77e106314629'
MARKET_FEATURE = ('POOL_CONCENTRATION_SHA256', 'top-three attributed BTC pool share and aggregate work')
MARKET_FEATURE_SOURCE = 'Mempool.Space Bitcoin weekly pool history'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_pool_concentration_20261125/manifest.json'
_BTC = None


def load_pool_share():
    raw = Path(os.environ.get('LONGSCOUT_BTC_POOL_CONCENTRATION', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != POOL_CONCENTRATION_SHA256:
        raise ValueError('expected pinned weekly BTC pool share')
    data = json.loads(raw)
    fields = ['top_three_share', 'aggregate_hashrate']
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1w', fields):
        raise ValueError('invalid BTC pool concentration contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='W-MON')):
        raise ValueError('pool concentration weeks must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def pool_share_broadens(share, hashrate):
    valid = np.isfinite(share) & share.between(0., 1.) & np.isfinite(hashrate) & hashrate.gt(0)
    concentration, work = share.where(valid), hashrate.where(valid)
    median = concentration.shift().rolling(8, min_periods=8).median()
    change = work.pct_change(fill_method=None).abs()
    observed = valid & median.notna() & change.notna()
    event = observed & concentration.le(median - .04) & change.le(.075)
    return event & observed.shift(fill_value=False) & ~event.shift(fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_pool_share()
    event = pool_share_broadens(source.top_three_share, source.aggregate_hashrate)
    decision_label = event.index + pd.Timedelta(days=1)
    available = event.set_axis(decision_label).reindex(btc.index, fill_value=False)
    _BTC = available & L1.regime_daily(btc).isin((0., 1.))


def sig_pool_breadth(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l169_btc_mining_pool_share_diffusion_1d': dict(
        name='[RESEARCH] L169 BTC mining-pool share dilution long', style='momentum',
        fn=sig_pool_breadth, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
