"""Research-only long when public attention to Ethereum broadens independently.

Compare daily English Wikipedia user-category pageviews with the prior
eight observations for the same weekday. Require Ethereum at least 1.5x
its median and Bitcoin and Stock_market within 15% of their medians.
Use the first qualifying event after two additional UTC days, a nonbear
BTC regime and a rising nonbear target. Pageviews need not be unique.
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

FAMILY_NOVELTY = ('Uses external information-seeking activity from Wikimedia. V36 and V94 '
                  'proxy attention with traded crypto volume and V106 learns price weekday effects.')
WIKIMEDIA_ATTENTION_SHA256 = 'eba1cc228e91b737d6981b328e80adb5dd7f28b402251a6b75c29ab9b611734e'
MARKET_FEATURE = ('WIKIMEDIA_ATTENTION_SHA256', 'English Wikipedia user pageviews for ETH, BTC and Stock_market')
MARKET_FEATURE_SOURCE = 'Wikimedia Analytics API daily user-category pageviews'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_wikimedia_attention_20261125/manifest.json'
FIELDS = ['Ethereum', 'Bitcoin', 'Stock_market']
_BTC = None


def load_pageviews():
    raw = Path(os.environ.get('LONGSCOUT_WIKIMEDIA_ATTENTION', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != WIKIMEDIA_ATTENTION_SHA256:
        raise ValueError('expected pinned Wikimedia pageview history')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['agent'], data['project']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS, 'user', 'en.wikipedia',
    ):
        raise ValueError('invalid Wikimedia attention archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Wikimedia pageview days must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def eth_attention_spikes(views):
    valid = (np.isfinite(views) & views.ge(0)).all(axis=1)
    usable = views.where(valid, axis=0)
    baseline = usable.groupby(usable.index.dayofweek).transform(lambda same: same.shift().rolling(8, min_periods=8).median())
    normalized = usable / baseline.where(baseline.gt(0))
    observed = normalized.notna().all(axis=1)
    state = (observed & normalized.Ethereum.ge(1.50) & normalized.Bitcoin.between(.85, 1.15)
             & normalized.Stock_market.between(.85, 1.15))
    pulse = state & observed.shift(fill_value=False) & ~state.shift(fill_value=False)
    return pulse.shift(2, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    pulse = eth_attention_spikes(load_pageviews()).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_eth_public_attention(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l194_ethereum_wikimedia_attention_spike_1d': dict(
        name='[RESEARCH] L194 Ethereum public pageview spike long', style='momentum',
        fn=sig_eth_public_attention, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
