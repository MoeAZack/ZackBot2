"""Research-only long after stablecoin transfers broaden on Ethereum.

For both Ethereum USDC and USDT, require the median daily transfer count
over the latest seven days at least 25% above the prior 28-day median.
Each native supply must stay within 2% of its week-ago level. Use the
first qualifying day, wait two extra UTC days, and require contributing
provider completion times by the decision. Buy an up-day nonbear target
with BTC nonbear. Transfer counts can include automated activity.
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

FAMILY_NOVELTY = ('Tests usage counts of two Ethereum stablecoin ledgers at comparable supply. '
                  'V140 uses issuance and V162 uses ETH addresses, while V179 uses loan balances.')
ETH_STABLE_ACTIVITY_SHA256 = 'd500f40de3259a025d7a40de780062e344679bc38cd37416699ef9eaec69919b'
MARKET_FEATURE = ('ETH_STABLE_ACTIVITY_SHA256', 'Ethereum USDC and USDT transfers, supply and completion timestamps')
MARKET_FEATURE_SOURCE = 'Coin Metrics Community Ethereum-specific ERC-20 daily asset metrics'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_eth_stable_activity_20261125/manifest.json'
ASSETS = ['usdc_eth', 'usdt_eth']
METRICS = ['TxTfrCnt', 'SplyCur', 'AssetEODCompletionTime']
FIELDS = [asset + '_' + metric for asset in ASSETS for metric in METRICS]
_BTC = None


def load_stable_activity():
    raw = Path(os.environ.get('LONGSCOUT_ETH_STABLE_ACTIVITY', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ETH_STABLE_ACTIVITY_SHA256:
        raise ValueError('expected pinned USDC and USDT Ethereum observations')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['assets'], data['fields']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', ASSETS, FIELDS,
    ):
        raise ValueError('invalid Ethereum stablecoin activity archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Ethereum stablecoin dates must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def transfers_broaden(data):
    counts = data[[asset + '_TxTfrCnt' for asset in ASSETS]]
    supply = data[[asset + '_SplyCur' for asset in ASSETS]]
    finished = data[[asset + '_AssetEODCompletionTime' for asset in ASSETS]]
    valid = np.isfinite(data).all(axis=1) & counts.ge(0).all(axis=1) & supply.gt(0).all(axis=1) & finished.gt(0).all(axis=1)
    usable = counts.where(valid, axis=0)
    typical = usable.shift(7).rolling(28, min_periods=28).median()
    observed = valid.rolling(35, min_periods=35).sum().eq(35) & typical.gt(0).all(axis=1)
    latest = usable.rolling(7, min_periods=7).median()
    broad = latest.ge(1.25 * typical).all(axis=1)
    stable_supply = supply.pct_change(7, fill_method=None).abs().le(.02).all(axis=1)
    state = observed & broad & stable_supply
    pulse = (state & observed.shift(fill_value=False) & ~state.shift(fill_value=False)).shift(2, fill_value=False)
    last_done = finished.max(axis=1).where(valid).rolling(36, min_periods=36).max().shift(2)
    decision = pd.Series(data.index + pd.Timedelta(days=1), index=data.index)
    return pulse & pd.to_datetime(last_done, unit='s').le(decision)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    event = transfers_broaden(load_stable_activity()).reindex(btc.index, fill_value=False)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_stable_transfers(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l193_eth_stablecoin_transfer_intensity_1d': dict(
        name='[RESEARCH] L193 Long on rising Ethereum stablecoin transfers', style='momentum',
        fn=sig_stable_transfers, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
