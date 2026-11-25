"""Research-only daily long after a fixed-maturity BTC basis crosses zero.

For each completed UTC day choose the nearest scheduled BTCUSDT quarterly
expiry 30 to 120 days from that day's close. Compute annualized log
delivery/spot basis using the same contract for the signal and baseline.
Buy after basis crosses upward through zero if the previous observation
was at least five percentage points below the prior 30-day median.
Require positive coin return and rising/sideways BTC and coin R1.
An unavailable selected contract leaves no signal. No roll adjustment.
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

FAMILY_NOVELTY = ('Uses cash-and-carry basis for a future with an actual delivery date, measured '
                  'against contemporaneous spot. V117/V123 use perpetual funding payments or '
                  'timing, and V120 uses a perpetual premium index. Those contracts do not expire.')
BTC_DELIVERY_MANIFEST_SHA256 = '79095a9f1f12bbd7ddf6a363bde314f3056258e4bc8f30f58c5252f2be00f7ae'
MARKET_FEATURE = ('BTC_DELIVERY_MANIFEST_SHA256', 'BTCUSDT daily spot and individual quarterly-delivery closes')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_delivery_20261125/manifest.json'
_BTC = None


def load_delivery():
    path = Path(os.environ.get('LONGSCOUT_BTC_DELIVERY_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if BTC_DELIVERY_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != BTC_DELIVERY_MANIFEST_SHA256:
        raise ValueError('expected pinned spot/delivery manifest')
    data = json.loads(raw)
    if (data['status'], data['timeframe']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d'):
        raise ValueError('invalid delivery source contract')
    market = {}
    for symbol, rows in data['market_closes'].items():
        date = pd.DatetimeIndex(pd.to_datetime([row[0] for row in rows], unit='ms'))
        if not date.is_unique or not date.is_monotonic_increasing:
            raise ValueError('delivery source dates must be unique and sorted')
        prices = pd.to_numeric(pd.Series([row[1] for row in rows], index=date, dtype='str'))
        market[symbol] = prices.where(np.isfinite(prices) & prices.gt(0))
    expiry = {s: pd.to_datetime(t, unit='ms') for s, t in data['expiry_ms'].items()}
    return market.pop('BTCUSDT'), market, expiry


def carry_signal(spot, contracts, expiries):
    allocated = pd.Series(False, index=spot.index)
    candidates = allocated.copy()
    settlement = spot.index + pd.Timedelta(days=1)
    for symbol, expiry in sorted(expiries.items(), key=lambda pair: pair[1]):
        time_left = (expiry - settlement) / pd.Timedelta(days=1)
        active = ~allocated & (time_left >= 30) & (time_left <= 120)
        allocated |= active
        prices = contracts.get(symbol, pd.Series(dtype=float)).reindex(spot.index)
        days_left = pd.Series(time_left, index=spot.index).where(time_left > 0)
        basis = 365 * np.log(prices / spot) / days_left
        previous = basis.shift()
        reference = basis.shift(2).rolling(30, min_periods=30).median()
        candidates |= active & previous.lt(0) & (reference - previous).ge(.05) & basis.ge(0)
    return candidates


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    spot, contracts, expiries = load_delivery()
    _BTC = carry_signal(spot.reindex(btc.index), contracts, expiries) & L1.regime_daily(btc).isin((0., 1.))


def sig_delivery(d, params, ctx):
    close, _ = L101._native_daily(d)
    dates = close.index
    grid = close.reindex(pd.date_range(dates[0], dates[-1], freq='D'))
    qualifies = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(qualifies.reindex(dates).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l125_btc_quarterly_delivery_basis_1d': dict(
        name='[RESEARCH] L125 BTC quarterly-delivery basis long', style='momentum', fn=sig_delivery,
        sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
