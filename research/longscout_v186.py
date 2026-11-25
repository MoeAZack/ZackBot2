"""Research-only long when the spread between two USDC lending rates narrows.

Compare base supply APYs for the same Ethereum USDC in Aave V3 and Compound
V3. Require the spread to contract from at least 1 percentage point to no
more than .50 points. The previously higher rate must fall at least .40
points while the other rate stays within .25 points. Use adjacent paired
dates and one additional UTC day. Buy an up-day target in nonbear R1.
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

FAMILY_NOVELTY = ('Tests cross-protocol relief in same-token Ethereum lending rates, excluding '
                  'reward incentives. V179 measures lending quantities, V175 measures the '
                  'distribution within Treasury repo and V143 compares futures funding.')
USDC_APY_DISPERSION_SHA256 = 'ecfa62df0d511f3e7e6fb7fec79175f5bf3d61bbb02b3aea8c63dcc36e1b28e3'
MARKET_FEATURE = ('USDC_APY_DISPERSION_SHA256', 'Aave V3 and Compound V3 Ethereum USDC base supply APY')
MARKET_FEATURE_SOURCE = 'DefiLlama historical daily Aave V3 and Compound V3 main-market USDC base supply APYs'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_usdc_apy_dispersion_20261125/manifest.json'
FIELDS = ['AaveV3', 'CompoundV3']
_BTC = None


def load_supply_rates():
    raw = Path(os.environ.get('LONGSCOUT_USDC_APY_DISPERSION', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != USDC_APY_DISPERSION_SHA256:
        raise ValueError('expected pinned Ethereum USDC base rates')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['basis']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS,
        'USDC Ethereum supply base APY (percent); rewards excluded; paired within 60 minutes on the same UTC day',
    ):
        raise ValueError('invalid USDC supply rate source contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('USDC rate dates must be unique and consecutive')
    rates = [[leg[1] if leg is not None else None for leg in row[1:]] for row in data['rows']]
    return pd.DataFrame(rates, index=dates, columns=FIELDS, dtype='float64')


def venue_gap_closes(quotes):
    rates = quotes.where(np.isfinite(quotes) & quotes.ge(0))
    spread = rates.AaveV3 - rates.CompoundV3
    aave_was_higher = spread.shift().gt(0)
    aave_change, compound_change = rates.AaveV3.diff(), rates.CompoundV3.diff()
    former_high_change = aave_change.where(aave_was_higher, compound_change)
    former_low_change = compound_change.where(aave_was_higher, aave_change)
    pulse = (spread.shift().abs().ge(1.0) & spread.abs().le(.50)
             & former_high_change.le(-.40) & former_low_change.abs().le(.25))
    return pulse.shift(1, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    event = venue_gap_closes(load_supply_rates()).reindex(btc.index, fill_value=False)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_usdc_gap_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l186_same_usdc_lending_rate_gap_relief_1d': dict(
        name='[RESEARCH] L186 Same-token DeFi lending rate gap relief long', style='momentum',
        fn=sig_usdc_gap_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
