"""Research-only long after leveraged CME Bitcoin funds reduce gross shorts.

From consecutive weekly CFTC futures-only reports, require leveraged funds'
net share of open interest to climb by three percentage points from at
most -15%, with lower gross shorts. Asset managers' net share must stay
at least 10% and move at most three percentage points. Use the later of
the normal publication buffer and the documented delayed-release buffer.
Buy rising targets while BTC and target R1 are nonbear.
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

FAMILY_NOVELTY = ('Uses gross and net positions in CFTC asset-manager and leveraged-fund categories '
                  'for listed CME Bitcoin futures. V119 compares native Binance trader concentration '
                  'with account counts and V142 compares Binance account cohorts.')
CME_COT_SHA256 = 'ac9df9124ce3907a56b402af4002b402bdb83ee2fa677840d2bcd32052a57f75'
MARKET_FEATURE = ('CME_COT_SHA256', 'CFTC CME Bitcoin futures-only position categories')
MARKET_FEATURE_SOURCE = 'CFTC TFF Futures Only, CME Bitcoin 133741'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_cme_cot_20261125/manifest.json'
FIELDS = ['open_interest_all', 'asset_mgr_positions_long', 'asset_mgr_positions_short',
          'lev_money_positions_long', 'lev_money_positions_short']
_BTC = None


def load_cot():
    raw = Path(os.environ.get('LONGSCOUT_CME_COT', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CME_COT_SHA256:
        raise ValueError('expected pinned CFTC CME Bitcoin positions')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['market_code'], data['fields']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1w', '133741', FIELDS,
    ):
        raise ValueError('invalid CME Bitcoin COT source contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    available = pd.DatetimeIndex(pd.to_datetime([row[1] for row in data['rows']]))
    if (not dates.is_unique or not dates.is_monotonic_increasing or not available.is_unique
            or (available < dates + pd.Timedelta(days=9)).any()):
        raise ValueError('CFTC release dates must respect the minimum publication buffer')
    frame = pd.DataFrame([row[2:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')
    return frame, available


def cover_shorts(frame, available):
    valid = np.isfinite(frame).all(axis=1) & frame.open_interest_all.gt(0)
    valid &= frame[FIELDS[1:]].ge(0).all(axis=1)
    valid &= frame[FIELDS[1:]].le(frame.open_interest_all, axis=0).all(axis=1)
    amount = frame.open_interest_all.where(valid)
    assets = (frame.asset_mgr_positions_long - frame.asset_mgr_positions_short) / amount
    funds = (frame.lev_money_positions_long - frame.lev_money_positions_short) / amount
    short = frame.lev_money_positions_short.where(valid)
    reports_are_adjacent = frame.index.to_series().diff().dt.days.between(6, 8)
    previous_is_known = available.to_series(index=frame.index).diff().gt(pd.Timedelta(0))
    qualified = (reports_are_adjacent & previous_is_known & funds.shift().le(-.15)
                 & funds.diff().ge(.03) & short.diff().lt(0) & assets.ge(.10)
                 & assets.shift().ge(.10) & assets.diff().abs().le(.03))
    return qualified.set_axis(available)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    report, available = load_cot()
    covering = cover_shorts(report, available).reindex(btc.index, fill_value=False)
    _BTC = covering & L1.regime_daily(btc).isin((0., 1.))


def sig_cme_covering(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l176_cme_positioning_long_after_short_covering_1d': dict(
        name='[RESEARCH] L176 CME Bitcoin leveraged-fund short covering long', style='momentum',
        fn=sig_cme_covering, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
