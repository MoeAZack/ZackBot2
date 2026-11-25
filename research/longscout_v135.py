"""Research-only long after a BTC settlement divergence by margin asset.

Compare BTC coin-margined and USDT-margined funding from the same observed
settlement minute. Adjust both quoted rates to an eight-hour equivalent.
Require positive USDT-margined and negative coin-margined funding with
at least 3 bp difference. Buy the first state with BTC and coin rising
on the 4h bar and their R1 regimes rising/sideways. Mask stale or missing
settlement observations. Keep the frozen funding cost model.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Compares compensation in coin collateral with USDT collateral for the same '
                  'underlying at a matched settlement time. V117 uses single-contract signed '
                  'funding and V123 changes in cadence. This tests divergence between margin assets.')
BTC_COLLATERAL_MANIFEST_SHA256 = '5d99bf116b00cb9f342d9d80aa1c9c765ed8f9dd26eeb28ca80931ba0c967c0b'
MARKET_FEATURE = ('BTC_COLLATERAL_MANIFEST_SHA256', 'BTC COIN-M and USD-M matched funding rates')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_collateral_20261125/manifest.json'
HOUR_MS = 3_600_000
_BTC = None


def load_events():
    path = Path(os.environ.get('LONGSCOUT_BTC_COLLATERAL_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if BTC_COLLATERAL_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != BTC_COLLATERAL_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC collateral funding manifest')
    data = json.loads(raw)
    if (data['status'], data['field']) != ('COMPLETE_WITH_EXPLICIT_MASKS', 'settled_funding_by_collateral'):
        raise ValueError('invalid collateral funding source')
    return data['linear'], data['inverse']


def synchronized_rate(candle_end, rows):
    stamp, hours, signed = zip(*rows)
    stamp, hours, signed = (np.asarray(a, dtype=t) for a, t in zip((stamp, hours, signed), (np.int64, np.int64, float)))
    if not (np.all(np.diff(stamp) > 0) and np.all((hours >= 1) & (hours <= 24)) and np.isfinite(signed).all()):
        raise ValueError('invalid collateral funding chronology')
    known = np.searchsorted(stamp, candle_end, side='left') - 1
    has_two = known >= 1
    latest = known[has_two]
    previous = latest - 1
    age = candle_end[has_two] - stamp[latest]
    gap = stamp[latest] - stamp[previous]
    fresh = ((age <= hours[latest] * HOUR_MS + 60_000)
             & (gap <= np.maximum(hours[latest], hours[previous]) * HOUR_MS + 60_000))
    rate = np.full(len(candle_end), np.nan)
    settled = np.full(len(candle_end), -1, dtype=np.int64)
    positions = np.flatnonzero(has_two)[fresh]
    rate[positions] = signed[latest[fresh]] * 8 / hours[latest[fresh]]
    settled[positions] = stamp[latest[fresh]]
    return settled, rate


def divergence(candle_end, linear, inverse):
    lt, lr = synchronized_rate(candle_end, linear)
    it, ir = synchronized_rate(candle_end, inverse)
    paired = (lt >= 0) & (it >= 0) & (np.abs(lt-it) <= 60_000)
    return paired & (lr > 0) & (ir < 0) & (lr - ir >= .0003)


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    end = (dates + pd.Timedelta(hours=4)).as_unit('ms').asi8
    nonbear = np.isin(L1.coin_regime(frame), (0., 1.))
    current = divergence(end, *load_events()) & nonbear & frame.c.diff().gt(0).to_numpy()
    flags = pd.Series(current, index=dates)
    _BTC = flags & ~flags.shift(fill_value=False)


def sig_collateral(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    context = _BTC.reindex(dates, fill_value=False).to_numpy()
    ready = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    le = context & ready & d.c.diff().gt(0).to_numpy() & np.isin(L1.coin_regime(d), (0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(le, index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l135_btc_collateral_funding_divergence_4h': dict(
        name='[RESEARCH] L135 BTC collateral funding divergence long', style='momentum', fn=sig_collateral,
        sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
