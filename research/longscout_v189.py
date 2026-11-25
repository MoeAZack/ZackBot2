"""Research-only long after BTC realized capitalization gains without a price trend.

Require BTC realized cap to gain at least .75% in 30 days after gaining
no more than .25% in the preceding 30. Market cap must change by at most
5% in the latest window. Use the first qualifying day with consecutive
observations, wait two further UTC days, and buy a rising target in
nonbear target and BTC R1. Realized cap is a ledger valuation convention.
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

FAMILY_NOVELTY = ('Tests repricing of UTXO-weighted realized capitalization with a constrained '
                  'spot-valued cap move. V141 uses spent-output age and V153 output counts, '
                  'without assigning historical prices to the UTXO stock.')
BTC_REALIZED_CAP_SHA256 = 'e43260419875058f3c284496a391ffc91862073e0225b74f4d8f0fc873016cf3'
MARKET_FEATURE = ('BTC_REALIZED_CAP_SHA256', 'Bitcoin realized and market capitalizations')
MARKET_FEATURE_SOURCE = 'Explore21 full-node reconstruction, CC BY 4.0'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_realized_cap_20261125/manifest.json'
FIELDS = ['realized_cap_usd', 'market_cap_usd']
_BTC = None


def load_caps():
    raw = Path(os.environ.get('LONGSCOUT_BTC_REALIZED_CAP', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BTC_REALIZED_CAP_SHA256:
        raise ValueError('expected pinned reconstructed Bitcoin cap measures')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['units']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS, 'USD',
    ):
        raise ValueError('invalid Bitcoin capitalization source contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('cap dates must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def books_reprice(caps):
    data = caps.where(np.isfinite(caps) & caps.gt(0))
    observed = data.notna().all(axis=1).rolling(61, min_periods=61).sum().eq(61)
    realized = data.realized_cap_usd.pct_change(30, fill_method=None)
    market = data.market_cap_usd.pct_change(30, fill_method=None)
    state = observed & realized.ge(.0075) & realized.shift(30).le(.0025) & market.abs().le(.05)
    event = state & observed.shift(fill_value=False) & ~state.shift(fill_value=False)
    return event.shift(2, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    pulse = books_reprice(load_caps()).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_onchain_repricing(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l189_btc_realized_cap_acceleration_1d': dict(
        name='[RESEARCH] L189 Bitcoin realized cap acceleration long', style='momentum',
        fn=sig_onchain_repricing, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
