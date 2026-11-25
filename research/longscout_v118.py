"""Research-only daily long after BTC open-interest contraction and recovery.

A shock day has a BTC close return of at most -2% and a drop of at least
2% in its 23:55 UTC open interest measured in contracts. On the next
completed day require a positive BTC close return and no more than 1%
OI rebuilding. Both BTC and the coin must be in rising/sideways R1.
The target coin must rise on decision day and beat BTC over the two-day
shock/recovery interval. Model fills at the next daily open.
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

FAMILY_NOVELTY = ('Uses the contraction in outstanding BTC futures contracts to identify a '
                  'deleveraging event. V2 uses a BTC price flush, V93 uses linear downside '
                  'beta, and V117 uses a coin’s settled funding. None measures changes in open interest.')
BTC_OI_MANIFEST_SHA256 = '92f11f644674f029d32695dcc78335b42c01767e75476d48829e14aeb67dafcf'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_oi_20261125/manifest.json'
_AFTER_SHOCK = _BTC_2D = None


def load_open_interest():
    path = Path(os.environ.get('LONGSCOUT_BTC_OI_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if BTC_OI_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != BTC_OI_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC open-interest manifest')
    data = json.loads(raw)
    if (data['status'], data['field'], data['sample']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', 'sum_open_interest', '23:55 UTC exact observation',
    ):
        raise ValueError('invalid BTC open-interest source contract')
    dates = pd.DatetimeIndex([row['date'] for row in data['days']])
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BTC open-interest dates must be unique and consecutive')
    samples = pd.to_numeric(pd.Series([row.get('sum_open_interest') for row in data['days']], index=dates))
    return samples.where(np.isfinite(samples) & samples.gt(0))


def recovery_flags(btc, open_interest):
    price_change = btc.pct_change(fill_method=None)
    oi_change = open_interest.reindex(btc.index).pct_change(fill_method=None)
    shock = price_change.le(-.02) & oi_change.le(-.02)
    return (shock.shift(fill_value=False) & price_change.gt(0) & oi_change.le(.01)
            & L1.regime_daily(btc).isin((0., 1.)))


def set_btc(frame):
    global _AFTER_SHOCK, _BTC_2D
    prices, _ = L101._native_daily(frame)
    prices = prices.reindex(pd.date_range(prices.index[0], prices.index[-1], freq='D'))
    _AFTER_SHOCK = recovery_flags(prices, load_open_interest())
    _BTC_2D = prices.pct_change(periods=2, fill_method=None)


def sig_oi_reset(d, params, ctx):
    if _AFTER_SHOCK is None:
        raise RuntimeError('set_btc required')
    close, _ = L101._native_daily(d)
    prices = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    rising = prices.gt(prices.shift())
    outperform = prices.pct_change(periods=2, fill_method=None).gt(_BTC_2D.reindex(prices.index))
    qualifies = (_AFTER_SHOCK.reindex(prices.index, fill_value=False) & rising & outperform
                 & L1.regime_daily(prices).isin((0., 1.)))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(qualifies.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l118_btc_open_interest_reset_1d': dict(
        name='[RESEARCH] L118 BTC open-interest reset long', style='reversal', fn=sig_oi_reset,
        sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
