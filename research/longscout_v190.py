"""Research-only range fade after CME Bitcoin option-equivalent exposure falls.

Subtract futures-only open interest from combined CFTC open interest. The
combined report counts options in futures-delta-equivalent units. Require
the prior excess at least 10% of futures OI, falling by three percentage
points and at least 20% in units while futures OI stays within 7.5%.
After verified release availability, fade a 2% move in a sideways coin
with nonbear BTC. Delta changes can affect the inferred exposure.
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

FAMILY_NOVELTY = ('Infers gross delta-equivalent options exposure from paired combined and '
                  'futures-only reports. V176 looks at futures trader direction, and V127 '
                  'and V185 use options prices rather than outstanding exposure.')
CME_OPTION_OI_SHA256 = 'a5934304d6266ea78e383866184af66233861d41aed8fddba7d888bafd12f830'
MARKET_FEATURE = ('CME_OPTION_OI_SHA256', 'CFTC CME Bitcoin options-combined versus futures-only open interest')
MARKET_FEATURE_SOURCE = 'CFTC TFF Combined and TFF Futures Only, CME Bitcoin 133741'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_cme_option_oi_20261125/manifest.json'
FIELDS = ['futures_oi', 'combined_oi']
_EVENT = _BTC_RANGE = None


def load_cot_oi():
    raw = Path(os.environ.get('LONGSCOUT_CME_OPTION_OI', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CME_OPTION_OI_SHA256:
        raise ValueError('expected pinned options-combined and futures-only COT')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['market_code'], data['fields']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1w', '133741', FIELDS,
    ):
        raise ValueError('invalid paired CFTC open-interest contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    available = pd.DatetimeIndex(pd.to_datetime([row[1] for row in data['rows']]))
    if (not dates.is_unique or not dates.is_monotonic_increasing or not available.is_unique
            or (available < dates + pd.Timedelta(days=9)).any()):
        raise ValueError('CFTC observation and availability dates are invalid')
    return pd.DataFrame([row[2:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64'), available


def option_exposure_falls(rows, available):
    valid = np.isfinite(rows).all(axis=1) & rows.futures_oi.gt(0) & rows.combined_oi.ge(rows.futures_oi)
    futures = rows.futures_oi.where(valid)
    options = (rows.combined_oi - futures).where(valid)
    share = options / futures
    adjacent = rows.index.to_series().diff().dt.days.between(6, 8)
    causal = available.to_series(index=rows.index).diff().gt(pd.Timedelta(0))
    stable = (futures / futures.shift()).between(.925, 1.075)
    pulse = (adjacent & causal & stable & share.shift().ge(.10) & share.diff().le(-.03)
             & options.le(.80 * options.shift()))
    return pulse.set_axis(available)


def set_btc(frame):
    global _EVENT, _BTC_RANGE
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _BTC_RANGE = L1.regime_daily(btc).isin((0., 1.))
    rows, published = load_cot_oi()
    _EVENT = option_exposure_falls(rows, published).reindex(btc.index, fill_value=False)


def sig_cme_options_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    supported = _BTC_RANGE.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).eq(0)
    entry = supported & _EVENT.reindex(grid.index, fill_value=False)
    move = grid.pct_change(fill_method=None)
    average = grid.shift().rolling(20, min_periods=20).mean()
    long = entry & move.le(-.02) & grid.lt(average)
    short = entry & move.ge(.02) & grid.gt(average)
    exit_long = ~supported | average.isna() | grid.ge(average)
    exit_short = ~supported | average.isna() | grid.le(average)
    return {name: S.X(pd.Series(flag.reindex(close.index).to_numpy(), index=d.index))
            for name, flag in zip(('le', 'se', 'lx', 'sx'), (long, short, exit_long, exit_short))}


CANDIDATES = {
    'r190_cme_option_equivalent_oi_relief_1d': dict(
        name='[RESEARCH] R190 Range fade after CME Bitcoin options exposure declines',
        style='range', fn=sig_cme_options_relief, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
