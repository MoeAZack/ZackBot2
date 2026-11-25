"""Research-only long after Ethereum address activity broadens.

Require recorded active-address count at least 20% above its prior 60-day
median while transaction count stays within 10% of its own median. Buy the
first qualifying observation after two full days, with every contributing
source date completed by decision time. Require nonbear BTC and target
regimes and positive target return. Addresses do not identify people.
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

FAMILY_NOVELTY = ('Tests whether more unique ETH addresses appear at comparable transaction '
                  'throughput. V138 tests fee pressure, V140 tests token supply, and V130 '
                  'tests the breadth of liquid futures contracts.')
ETH_ADDRESS_BREADTH_SHA256 = '5e9f14fc3aa5ea4128234cac029ed68021a850f483a5e3d569497f6f5832475e'
MARKET_FEATURE = ('ETH_ADDRESS_BREADTH_SHA256', 'ETH active address count and transaction count')
MARKET_FEATURE_SOURCE = 'Coin Metrics Community Network Data API'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_eth_address_breadth_20261125/manifest.json'
_BTC = None


def load_address_counts():
    raw = Path(os.environ.get('LONGSCOUT_ETH_ADDRESS_BREADTH', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ETH_ADDRESS_BREADTH_SHA256:
        raise ValueError('expected pinned ETH address activity')
    data = json.loads(raw)
    fields = ['AdrActCnt', 'TxCnt', 'AssetEODCompletionTime']
    if (data['status'], data['asset'], data['frequency'], data['fields']) != ('COMPLETE', 'eth', '1d', fields):
        raise ValueError('invalid ETH address activity input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('ETH address activity dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def addresses_without_volume(addresses, transactions, complete):
    valid = np.isfinite(addresses) & addresses.gt(0) & np.isfinite(transactions) & transactions.gt(0) & np.isfinite(complete)
    addr, tx = addresses.where(valid), transactions.where(valid)
    base_addr = addr.shift().rolling(60, min_periods=60).median()
    base_tx = tx.shift().rolling(60, min_periods=60).median()
    observed = valid & base_addr.gt(0) & base_tx.gt(0)
    broad = observed & addr.ge(1.20 * base_addr) & (tx / base_tx).between(.90, 1.10)
    onset = (broad & observed.shift(fill_value=False) & ~broad.shift(fill_value=False)).shift(2, fill_value=False)
    available = pd.to_datetime(complete.where(valid).rolling(62, min_periods=62).max().shift(2), unit='s')
    decision = pd.Series(addresses.index + pd.Timedelta(days=1), index=addresses.index)
    return onset & available.le(decision)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_address_counts()
    breadth = addresses_without_volume(source.AdrActCnt, source.TxCnt, source.AssetEODCompletionTime)
    _BTC = breadth.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_address_breadth(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l162_eth_active_address_breadth_1d': dict(
        name='[RESEARCH] L162 ETH active-address breadth long', style='momentum',
        fn=sig_address_breadth, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
