"""Research-only daily long on joint Ethereum USDC/USDT supply expansion.

Use the fixed Ethereum denomination of each issuer. Require both supplies to
rise over seven days and their combined nominal supply to rise by at least
0.5%. Buy the first qualifying observation after the two-day buffer only
when all four contributing daily snapshots have completed by decision time.
Require positive target return and nonbear BTC and target regimes.
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

FAMILY_NOVELTY = ('Tests the outstanding nominal quantity of two dollar tokens on one fixed '
                  'chain. V121 uses a spot peg ratio and V132 uses arbitrage residuals. '
                  'Neither observes issuance and redemption reflected in token supply.')
ETH_STABLE_SUPPLY_MANIFEST_SHA256 = 'ce18b22424e0b2352bc4a46ad6219c52cacecee09a53e166b22e032ef8dfa098'
MARKET_FEATURE = ('ETH_STABLE_SUPPLY_MANIFEST_SHA256', 'USDC and USDT Ethereum supply plus completion time')
MARKET_FEATURE_SOURCE = 'Coin Metrics Community Network Data API'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_eth_stable_supply_20261125/manifest.json'
_BTC = None


def load_supply():
    raw = Path(os.environ.get('LONGSCOUT_ETH_STABLE_SUPPLY_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ETH_STABLE_SUPPLY_MANIFEST_SHA256:
        raise ValueError('expected pinned Ethereum USDC/USDT supply input')
    data = json.loads(raw)
    fields = ['usdc_eth_supply', 'usdt_eth_supply', 'usdc_eth_complete', 'usdt_eth_complete']
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE', '1d', fields):
        raise ValueError('invalid Ethereum stablecoin supply input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Ethereum supply dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def joint_expansion(usdc, usdt, usdc_complete, usdt_complete):
    observed = np.isfinite(usdc) & np.isfinite(usdt) & usdc.gt(0) & usdt.gt(0)
    observed &= np.isfinite(usdc_complete) & np.isfinite(usdt_complete)
    combined = (usdc + usdt).where(observed)
    usable = observed & observed.shift(7, fill_value=False)
    expanding = usable & usdc.gt(usdc.shift(7)) & usdt.gt(usdt.shift(7)) & combined.ge(1.005 * combined.shift(7))
    crossing = (expanding & usable.shift(fill_value=False) & ~expanding.shift(fill_value=False)).shift(2, fill_value=False)
    snapshot = pd.concat([usdc_complete.where(observed), usdt_complete.where(observed)], axis=1).max(axis=1, skipna=False)
    required = pd.concat([snapshot, snapshot.shift(), snapshot.shift(7), snapshot.shift(8)], axis=1)
    available = pd.to_datetime(required.max(axis=1, skipna=False).shift(2), unit='s')
    decision = pd.Series(usdc.index + pd.Timedelta(days=1), index=usdc.index)
    return crossing & available.le(decision)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_supply()
    expansion = joint_expansion(source.usdc_eth_supply, source.usdt_eth_supply,
                                source.usdc_eth_complete, source.usdt_eth_complete)
    _BTC = expansion.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_joint_expansion(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l140_eth_stablecoin_joint_expansion_1d': dict(
        name='[RESEARCH] L140 Joint Ethereum stablecoin expansion long', style='momentum',
        fn=sig_joint_expansion, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
