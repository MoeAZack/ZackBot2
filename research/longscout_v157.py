"""Research-only long when tracked WBTC supply resumes expansion.

After a week of declining WBTC supply, require current seven-day growth
to equal at least half the growth in native BTC supply over the current
week. This measures relative magnitude without assuming that the two flows
involve the same coins. Use two full additional days and recorded source
completion times. Require nonbear BTC and target regimes and rising target.
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

FAMILY_NOVELTY = ('Compares growth in a backed BTC representation with growth in underlying BTC '
                  'supply. V140 tests expansion in dollar tokens on one chain, V141 studies '
                  'the age of native outputs and V152 tests scheduled compensation per work.')
WRAPPED_SUPPLY_SHA256 = 'b4ab6cdfc1ff2b605f9d8f36307797716e0f4c39ff8c4b407ddf2b2b455510dc'
MARKET_FEATURE = ('WRAPPED_SUPPLY_SHA256', 'Coin Metrics WBTC and BTC supply with completion times')
MARKET_FEATURE_SOURCE = 'Coin Metrics Community Network Data API'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_wrapped_supply_20261125/manifest.json'
_BTC = None


def load_wrapped_supply():
    raw = Path(os.environ.get('LONGSCOUT_WRAPPED_SUPPLY_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != WRAPPED_SUPPLY_SHA256:
        raise ValueError('expected pinned BTC and WBTC supply input')
    data = json.loads(raw)
    fields = ['btc_supply', 'wbtc_supply', 'btc_complete', 'wbtc_complete']
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE', '1d', fields):
        raise ValueError('invalid BTC and WBTC supply input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BTC and WBTC supply dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def wrapping_resumes(native, wrapped, native_done, wrapped_done):
    valid = np.isfinite(native) & native.gt(0) & np.isfinite(wrapped) & wrapped.gt(0) & native.gt(wrapped)
    valid &= np.isfinite(native_done) & np.isfinite(wrapped_done)
    native, wrapped = native.where(valid), wrapped.where(valid)
    mint, growth = wrapped.diff(7), native.diff(7)
    observable = valid.rolling(15, min_periods=15).sum().eq(15)
    renewed = observable & mint.shift(7).lt(0) & growth.gt(0) & mint.ge(.50 * growth)
    signal = (renewed & observable.shift(fill_value=False) & ~renewed.shift(fill_value=False)).shift(2, fill_value=False)
    completion = pd.concat([native_done.where(valid), wrapped_done.where(valid)], axis=1).max(axis=1, skipna=False)
    matched = pd.to_datetime(completion.rolling(16, min_periods=16).max().shift(2), unit='s')
    decision = pd.Series(native.index + pd.Timedelta(days=1), index=native.index)
    return signal & matched.le(decision)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    supply = load_wrapped_supply()
    event = wrapping_resumes(supply.btc_supply, supply.wbtc_supply, supply.btc_complete, supply.wbtc_complete)
    _BTC = event.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_wrapped_supply_growth(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l157_relative_wrapped_btc_supply_recovery_1d': dict(
        name='[RESEARCH] L157 Relative wrapped BTC supply recovery long', style='momentum',
        fn=sig_wrapped_supply_growth, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
