"""Research-only long on simultaneous recovery of alt perpetual bases.

At the daily close, require at least three of five fixed contracts to move
from below -3 basis points to at least -1 basis point versus their index.
Require complete paired data for both adjacent closes, nonbear BTC and
target R1, and positive target return. Trade at the next daily open.
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

FAMILY_NOVELTY = ('Tests simultaneous normalization of traded bases across alt contracts. '
                  'V120 measures the BTC premium index, V128 the BTC extreme relative '
                  'to mark and index, and V143 realized funding ranks.')
ALT_BASIS_BREADTH_SHA256 = 'fa6fe984dba1b263c28cd72107958d916246ce54244c62e7029aadd1e5471b10'
MARKET_FEATURE = ('ALT_BASIS_BREADTH_SHA256', 'five alt perpetual closes matched to spot index closes')
MARKET_FEATURE_SOURCE = 'Binance Data Vision USD-M futures 4-hour traded and index candles'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_alt_basis_breadth_20261125/manifest.json'
MARKETS = ['ADAUSDT', 'DOGEUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']
_BTC = None


def load_bases():
    raw = Path(os.environ.get('LONGSCOUT_ALT_BASIS_BREADTH', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ALT_BASIS_BREADTH_SHA256:
        raise ValueError('expected pinned alt perpetual and index prices')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['markets'], data['fields'], data['sample']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', MARKETS, ['perpetual_close', 'index_close'],
        '20:00 UTC 4h bar close',
    ):
        raise ValueError('invalid alt basis archive contract')
    days, quotes = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(days))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('alt basis days must be unique and consecutive')
    bases = pd.DataFrame(index=dates)
    for market in MARKETS:
        prices = pd.DataFrame([row.get(market) or [None, None] for row in quotes],
                              index=dates, columns=['perpetual', 'index']).apply(pd.to_numeric)
        prices = prices.where(np.isfinite(prices) & prices.gt(0))
        bases[market] = np.log(prices.perpetual / prices['index'])
    return bases


def simultaneous_recovery(bases):
    bases = bases.reindex(columns=MARKETS)
    bases = bases.where(np.isfinite(bases))
    prior = bases.shift()
    complete = (bases.notna() & prior.notna()).all(axis=1)
    recoveries = prior.lt(-.0003) & bases.ge(-.0001)
    return complete & recoveries.sum(axis=1).ge(3)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    events = simultaneous_recovery(load_bases()).reindex(btc.index, fill_value=False)
    _BTC = events & L1.regime_daily(btc).isin((0., 1.))


def sig_basis_recovery(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l170_alt_perpetual_basis_recovery_breadth_1d': dict(
        name='[RESEARCH] L170 Synchronized alt perpetual basis recovery', style='reversal',
        fn=sig_basis_recovery, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
