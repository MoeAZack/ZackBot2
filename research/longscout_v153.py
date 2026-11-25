"""Research-only range fade during consolidation of Bitcoin's output set.

Divide each day's spent output count by the previous day's unspent output
count. Require the latest seven-day mean to be at least 25% above the
preceding 56-day daily median while the unspent output count contracts over
the week. Use the first qualifying day after a seven-day confirmation lag.
Fade a sideways target's 2% move when BTC is nonbear and its absolute
daily return is at or below its preceding 20-day median.
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

FAMILY_NOVELTY = ('Measures the changing number of outstanding Bitcoin outputs together with '
                  'counts spent. V141 assigns a share by age and amount of outputs, and '
                  'V138 measures fees and transaction counts. No user or wallet identity is inferred.')
UTXO_TURNOVER_MANIFEST_SHA256 = 'ffa4ca47060d683c66573d045a475a7b0b36e6110064398168a2fdd53929104d'
MARKET_FEATURE = ('UTXO_TURNOVER_MANIFEST_SHA256', 'BTC end-of-day UTXO count and outputs spent')
MARKET_FEATURE_SOURCE = 'Open Bitcoin Metrics release 0.1.0, Bitcoin Core full-node data'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_utxo_turnover_20261125/manifest.json'
_BTC = None


def load_output_counts():
    raw = Path(os.environ.get('LONGSCOUT_BTC_UTXO_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != UTXO_TURNOVER_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC output counts')
    data = json.loads(raw)
    fields = ['unspent_count', 'spent_count']
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE', '1d', fields):
        raise ValueError('invalid BTC output count input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BTC output count dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def output_consolidation(unspent, spent):
    valid = np.isfinite(unspent) & unspent.gt(0) & np.isfinite(spent) & spent.ge(0)
    stock = unspent.where(valid)
    intensity = spent.where(valid) / stock.shift()
    flow = intensity.rolling(7, min_periods=7).mean()
    baseline = intensity.shift(7).rolling(56, min_periods=56).median()
    observed = baseline.gt(0) & flow.notna()
    shrinking = stock.lt(stock.shift(7))
    consolidation = observed & flow.ge(1.25 * baseline) & shrinking
    onset = consolidation & observed.shift(fill_value=False) & ~consolidation.shift(fill_value=False)
    return onset.shift(7, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_output_counts()
    event = output_consolidation(source.unspent_count, source.spent_count)
    change = btc.pct_change(fill_method=None).abs()
    quiet = change.le(change.shift().rolling(20, min_periods=20).median())
    _BTC = event.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.)) & quiet


def sig_output_consolidation(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).eq(0)
    move = grid.pct_change(fill_method=None)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series((active & move.le(-.02)).reindex(close.index).to_numpy(), index=d.index)),
                se=S.X(pd.Series((active & move.ge(.02)).reindex(close.index).to_numpy(), index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r153_btc_output_set_consolidation_1d': dict(
        name='[RESEARCH] R153 BTC output-set consolidation range fade', style='range',
        fn=sig_output_consolidation, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
