"""Research-only long after spot DEX volume shifts to major Ethereum rollups.

For the last three completed days, compare the combined Arbitrum and
Optimism share with the preceding 28 daily shares. Require a gain of
at least three percentage points and 35% relative to that median. Require
both rollups to increase at least 20% compared with their own medians,
while the three-chain total stays within 15% of its baseline. Apply two
additional days before the first qualifying event can inform a long.
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

FAMILY_NOVELTY = ('Tracks separately reported spot DEX turnover allocation across Ethereum '
                  'and two rollups. V162 uses ETH active addresses, V122 is Binance spot vs '
                  'perpetual turnover, and V164 uses Lightning channel capacity.')
ROLLUP_DEX_VOLUME_SHA256 = '46de8bb0db959bb2af09d8631bb818820c88e292afa8d7c5a330615a3bcbd22a'
MARKET_FEATURE = ('ROLLUP_DEX_VOLUME_SHA256', 'Ethereum, Arbitrum and Optimism daily spot DEX volume')
MARKET_FEATURE_SOURCE = 'DefiLlama public spot DEX historical volume per chain'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_l2_dex_turnover_20261125/manifest.json'
CHAINS = ['Ethereum', 'Arbitrum', 'Optimism']
_BTC = None


def load_dex_volume():
    raw = Path(os.environ.get('LONGSCOUT_ROLLUP_DEX_VOLUME', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ROLLUP_DEX_VOLUME_SHA256:
        raise ValueError('expected pinned DEX spot volume archive')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['units']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', CHAINS, 'reported USD spot DEX volume',
    ):
        raise ValueError('invalid rollup DEX volume source contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('DEX volume days must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=CHAINS, dtype='float64')


def rotation(volumes):
    chain = volumes.where(np.isfinite(volumes) & volumes.ge(0))
    total = chain.sum(axis=1, min_count=3).where(lambda value: value.gt(0))
    l2 = (chain.Arbitrum + chain.Optimism).where(total.notna())
    typical = chain.where(total.notna(), axis=0).shift(3).rolling(28, min_periods=28).median()
    total_normal = total.shift(3).rolling(28, min_periods=28).median()
    share_normal = (l2 / total).shift(3).rolling(28, min_periods=28).median()
    latest = chain.rolling(3, min_periods=3).mean()
    latest_total = total.rolling(3, min_periods=3).mean()
    share = l2.rolling(3, min_periods=3).mean() / latest_total
    ready = latest_total.notna() & typical.gt(0).all(axis=1) & share_normal.gt(0)
    state = (ready & share.ge(1.35 * share_normal) & share.ge(share_normal + .03)
             & latest.Arbitrum.ge(1.20 * typical.Arbitrum) & latest.Optimism.ge(1.20 * typical.Optimism)
             & (latest_total / total_normal).between(.85, 1.15))
    event = state & ready.shift(fill_value=False) & ~state.shift(fill_value=False)
    return event.shift(2, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    event = rotation(load_dex_volume()).reindex(btc.index, fill_value=False)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_rollup_turnover(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l178_rollup_dex_share_rotation_long_1d': dict(
        name='[RESEARCH] L178 Two-rollup spot DEX volume rotation long', style='momentum',
        fn=sig_rollup_turnover, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
