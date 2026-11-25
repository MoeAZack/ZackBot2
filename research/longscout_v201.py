"""Research-only long after Uniswap V3 effective swap fees return to normal.

Use Ethereum-only fees paid and notional traded in USD. Require yesterday's
fee per traded dollar at least 25% above its preceding 28-day median, with
today at most 5% above that benchmark. Turnover on both days must stay
within 20% of prior median. Require complete dates and wait two extra UTC
days before buying a rising nonbear target in nonbear BTC. The ratio can
change with the mix of trades across pools.
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

FAMILY_NOVELTY = ('Tests actual fees per traded dollar in a fixed on-chain exchange and chain. '
                  'V178 measures the allocation of dollar volume and V180 estimates a futures '
                  'spread from return covariance, without observing paid swap fees.')
UNISWAP_EFFECTIVE_FEES_SHA256 = 'a668b58c0935482641593241984d29366fecbabedab14f73455c17352c0f8a13'
MARKET_FEATURE = ('UNISWAP_EFFECTIVE_FEES_SHA256', 'Uniswap V3 Ethereum user swap fees and traded volume')
MARKET_FEATURE_SOURCE = 'DefiLlama chain breakdowns for Uniswap V3 swap fees and volume'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_uniswap_effective_fees_20261125/manifest.json'
FIELDS = ['ethereum_swap_fees_usd', 'ethereum_swap_volume_usd']
_BTC = None


def load_swap_fees():
    raw = Path(os.environ.get('LONGSCOUT_UNISWAP_EFFECTIVE_FEES', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != UNISWAP_EFFECTIVE_FEES_SHA256:
        raise ValueError('expected pinned Uniswap V3 Ethereum fee and volume history')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS):
        raise ValueError('invalid Uniswap fee archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Uniswap fee dates must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def effective_fees_ease(data):
    fee, volume = data.ethereum_swap_fees_usd, data.ethereum_swap_volume_usd
    valid = np.isfinite(fee) & np.isfinite(volume) & fee.gt(0) & volume.gt(0) & fee.le(volume)
    volume = volume.where(valid)
    effective = fee.where(valid) / volume
    median_fee = effective.shift(2).rolling(28, min_periods=28).median()
    median_volume = volume.shift(2).rolling(28, min_periods=28).median()
    relative = volume / median_volume
    previous_volume = volume.shift() / median_volume
    stable_volume = relative.between(.80, 1.20) & previous_volume.between(.80, 1.20)
    pulse = effective.shift().ge(1.25 * median_fee) & effective.le(1.05 * median_fee) & stable_volume
    return pulse.shift(2, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    pulse = effective_fees_ease(load_swap_fees()).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_uniswap_fee_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l201_uniswap_eth_swap_fee_relief_1d': dict(
        name='[RESEARCH] L201 Uniswap V3 effective swap fee returns to normal', style='momentum',
        fn=sig_uniswap_fee_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
