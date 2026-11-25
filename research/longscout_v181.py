"""Research-only long after an isolated stETH discount starts to ease.

Require stETH to trade at or below .985 ETH on the prior date and improve
at least .005 ETH while remaining below .998 ETH. Compare the rETH/ETH
log ratio with its prior 60-observation median, excluding both event days
and requiring 50 values. rETH must stay within .005 log points throughout.
Use matched secondary-market marks and an extra UTC day of delay. Buy
up-day targets when BTC and the target are nonbear.
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

FAMILY_NOVELTY = ('Isolates an observed stETH discount from the price of a second staking issuer '
                  'with a different reward accounting model. V157 tracks wrapped BTC token supply, '
                  'V121 compares dollar stablecoins and V170 studies shared futures basis recovery.')
ETH_STAKING_MARKS_SHA256 = '2698d2f63cf4287a4aeff59636fec0a2ed0561249d4806f6f86afdeca22f8fc6'
MARKET_FEATURE = ('ETH_STAKING_MARKS_SHA256', 'paired ETH, Lido stETH and Rocket Pool rETH market marks')
MARKET_FEATURE_SOURCE = 'DefiLlama Coins historical CoinGecko marks with actual timestamps'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_ethereum_staking_marks_20261125/manifest.json'
FIELDS = ['ETH', 'stETH', 'rETH']
_BTC = None


def load_staking_marks():
    raw = Path(os.environ.get('LONGSCOUT_ETH_STAKING_MARKS', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ETH_STAKING_MARKS_SHA256:
        raise ValueError('expected pinned ETH staking token market marks')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['matching']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS,
        'within four hours of UTC midnight and 30 minutes across all three legs',
    ):
        raise ValueError('invalid Ether staking-mark history contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('staking mark labels must be unique consecutive dates')
    marks = [[leg[1] if leg is not None else None for leg in row[1:]] for row in data['rows']]
    return pd.DataFrame(marks, index=dates, columns=FIELDS, dtype='float64')


def isolated_discount_eases(marks):
    prices = marks.where(np.isfinite(marks) & marks.gt(0))
    steth = prices.stETH / prices.ETH
    rocket = np.log(prices.rETH / prices.ETH)
    reference = rocket.shift(2).rolling(60, min_periods=50).median()
    rocket_stable = (rocket - reference).abs().le(.005) & (rocket.shift() - reference).abs().le(.005)
    relief = steth.shift().le(.985) & steth.diff().ge(.005) & steth.lt(.998) & rocket_stable
    return relief.shift(1, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    pulse = isolated_discount_eases(load_staking_marks()).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_isolated_staking_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l181_isolated_steth_discount_relief_1d': dict(
        name='[RESEARCH] L181 Isolated stETH market discount relief long', style='momentum',
        fn=sig_isolated_staking_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
