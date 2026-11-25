"""Research-only long when Bitcoin nominal confirmation gaps normalize.

Count canonical block header timestamp gaps of at least 30 minutes by
UTC median-time-past day. Require the preceding share at least 7% and
1.5 times the prior 30-day median. Buy after the share returns to or below
that median while block counts on both days stay within 15% of normal.
Use two additional days, nonbear BTC and target R1, and an up-day target.
Header clocks are a proxy for timing, not recorded arrival times.
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

FAMILY_NOVELTY = ('Uses the distribution of nominal block gaps at comparable daily block count. '
                  'V139 tracks estimated total computational work, V169/V172 mining pool share, '
                  'and V165 the unconfirmed transaction queue.')
BTC_HEADER_GAPS_SHA256 = '68ebe16ad29c0df148d581cdd2b02a5a1446f776f3710d26f449d45fa1ab9b47'
MARKET_FEATURE = ('BTC_HEADER_GAPS_SHA256', 'daily canonical Bitcoin block count and 30-minute header gaps')
MARKET_FEATURE_SOURCE = 'Blockstream Electrum Bitcoin block headers with independently checked endpoints'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_header_gaps_20261125/manifest.json'
FIELDS = ['block_count', 'absolute_header_gap_at_least_30m']
_BTC = None


def load_block_gaps():
    raw = Path(os.environ.get('LONGSCOUT_BTC_HEADER_GAPS', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BTC_HEADER_GAPS_SHA256:
        raise ValueError('expected pinned Bitcoin header gap archive')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS):
        raise ValueError('invalid Bitcoin header gap input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('header aggregate days must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def long_gaps_normalize(observations):
    count, gaps = observations.block_count, observations.absolute_header_gap_at_least_30m
    count = count.where(np.isfinite(count) & count.gt(0))
    valid = np.isfinite(gaps) & gaps.ge(0) & gaps.le(count)
    share = gaps.where(valid) / count
    benchmark = share.shift(2).rolling(30, min_periods=30).median()
    normal = count.shift(2).rolling(30, min_periods=30).median()
    stable_count = (count / normal).between(.85, 1.15) & (count.shift() / normal).between(.85, 1.15)
    stress = share.shift().ge(.07) & share.shift().ge(1.50 * benchmark)
    relief = stress & stable_count & share.le(benchmark)
    return relief.shift(2, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    event = long_gaps_normalize(load_block_gaps()).reindex(btc.index, fill_value=False)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_block_gap_recovery(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l183_btc_header_gap_tail_normalizes_1d': dict(
        name='[RESEARCH] L183 BTC long-header-gap tail normalizes', style='momentum',
        fn=sig_block_gap_recovery, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
