"""Research-only long when ETH supply contracts despite steady issuance.

Require the latest 21-day change in ETH outstanding supply to turn negative
after a positive preceding 21-day change. Total reported issuance over the
latest 21 days must remain at least 90% of the preceding window. Delay the
observation two days and require all contributing source dates to have
recorded completion times at or before the modeled decision. Require nonbear
BTC and target R1 and a positive target return.
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

FAMILY_NOVELTY = ('Tests a change in the supply of ETH while its reported issuance stays near '
                  'its prior level. V140 measures liquidity through two dollar-token supplies; '
                  'V138 measures BTC fee demand. This uses neither a dollar peg nor fees.')
ETH_NET_SUPPLY_MANIFEST_SHA256 = '967b7b48c99984a3b3641d81b1b8e48b2352c4bf0a5121fe920c9c1611b62283'
MARKET_FEATURE = ('ETH_NET_SUPPLY_MANIFEST_SHA256', 'ETH outstanding supply, issuance and completion time')
MARKET_FEATURE_SOURCE = 'Coin Metrics Community Network Data API'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_eth_net_supply_20261125/manifest.json'
_BTC = None


def load_supply():
    raw = Path(os.environ.get('LONGSCOUT_ETH_NET_SUPPLY_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ETH_NET_SUPPLY_MANIFEST_SHA256:
        raise ValueError('expected pinned ETH supply input')
    data = json.loads(raw)
    fields = ['SplyCur', 'IssTotNtv', 'AssetEODCompletionTime']
    if (data['status'], data['asset'], data['frequency'], data['fields']) != ('COMPLETE', 'eth', '1d', fields):
        raise ValueError('invalid ETH supply input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('ETH supply dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def supply_contraction(supply, issuance, completed):
    valid = np.isfinite(supply) & supply.gt(0) & np.isfinite(issuance) & issuance.ge(0) & np.isfinite(completed)
    supply, issuance = supply.where(valid), issuance.where(valid)
    ready = valid.rolling(43, min_periods=43).sum().eq(43)
    change = supply.diff(21)
    recent_issuance = issuance.rolling(21, min_periods=21).sum()
    stable_issuance = recent_issuance.gt(0) & recent_issuance.ge(.90 * recent_issuance.shift(21))
    contraction = ready & change.lt(0) & change.shift(21).gt(0) & stable_issuance
    onset = (contraction & ready.shift(fill_value=False) & ~contraction.shift(fill_value=False)).shift(2, fill_value=False)
    available = pd.to_datetime(completed.where(valid).rolling(44, min_periods=44).max().shift(2), unit='s')
    decision = pd.Series(supply.index + pd.Timedelta(days=1), index=supply.index)
    return onset & available.le(decision)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_supply()
    event = supply_contraction(source.SplyCur, source.IssTotNtv, source.AssetEODCompletionTime)
    _BTC = event.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_supply_contraction(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l149_eth_net_supply_contraction_1d': dict(
        name='[RESEARCH] L149 ETH net supply contraction long', style='momentum',
        fn=sig_supply_contraction, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
