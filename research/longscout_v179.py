"""Research-only long when USDC and DAI debt expand on Ethereum Aave.

Measure nominal tokens across Aave V2 and V3 to avoid dollar repricing
and migration between versions. Require both debts to grow at least 3%
over three days while available balances stay between 98% and 112% of
their prior values. Require all intervening observations. Use the first
event after two additional days, nonbear BTC and target, and a rising coin.
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

FAMILY_NOVELTY = ('Tests outstanding stablecoin loan demand at comparable lending-pool liquidity. '
                  'V140 measures token supply regardless of use, V146 studies futures contracts '
                  'versus trading turnover and V175 uses traditional repo rates.')
AAVE_STABLE_DEBT_SHA256 = 'e0f10c764869157ab9c3d92d57ecdba327d904c309ee863e99b36f12528abf51'
MARKET_FEATURE = ('AAVE_STABLE_DEBT_SHA256', 'Ethereum Aave V2+V3 raw USDC and DAI cash balances and debt')
MARKET_FEATURE_SOURCE = 'DefiLlama Ethereum Aave V2/V3 token balances and borrowed token balances'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_aave_eth_stable_debt_20261125/manifest.json'
FIELDS = ['USDC_available', 'USDC_debt', 'DAI_available', 'DAI_debt']
_BTC = None


def load_stable_debt():
    raw = Path(os.environ.get('LONGSCOUT_AAVE_STABLE_DEBT', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != AAVE_STABLE_DEBT_SHA256:
        raise ValueError('expected pinned Aave token balances')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['units']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS, 'native token units',
    ):
        raise ValueError('invalid Aave token balance contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Aave balance dates must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def debt_expands(balances):
    amounts = balances.where(np.isfinite(balances) & balances.gt(0))
    ready = amounts.notna().all(axis=1).rolling(4, min_periods=4).sum().eq(4)
    relative = amounts / amounts.shift(3)
    state = (ready & relative.USDC_debt.ge(1.03) & relative.DAI_debt.ge(1.03)
             & relative.USDC_available.between(.98, 1.12) & relative.DAI_available.between(.98, 1.12))
    event = state & ready.shift(fill_value=False) & ~state.shift(fill_value=False)
    return event.shift(2, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    event = debt_expands(load_stable_debt()).reindex(btc.index, fill_value=False)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_stable_debt(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l179_aave_stablecoin_debt_expansion_1d': dict(
        name='[RESEARCH] L179 Aave stablecoin loan expansion long', style='momentum',
        fn=sig_stable_debt, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
