"""Buy a rising nonbear coin after a spot BTC ETF premium opposes fixing-hour drift.

Use regular US equity sessions in the issuer's available daily series.
Require an IBIT market premium of at least 15 basis points over NAV when
Binance BTCUSDT spot ends the corresponding 15:00-16:00 New York hour no
higher than quote-volume/base-volume VWAP. Wait two extra UTC days.
Binance spot is a directional timing proxy for the published multi-venue fix.
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

FAMILY_NOVELTY = ('Tests price versus reported net asset value for an equity-listed BTC vehicle '
                  'with an explicit fixing-hour direction check. V187 compares contemporaneous '
                  'spot venue prices and V181 compares staking token discounts.')
IBIT_SPOT_FIX_SHA256 = '2ffdeed209712e0ed169dab4ccf3017798801be4c6a2d39f80aad331dd6670c9'
MARKET_FEATURE = ('IBIT_SPOT_FIX_SHA256', 'IBIT NAV premium and BTCUSDT spot fixing-hour closing drift')
MARKET_FEATURE_SOURCE = 'iShares IBIT premium/discount chart and Binance BTCUSDT spot 1h archive'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_ibit_20261125/manifest.json'
FIELDS = ['ibit_nav_premium_pct', 'binance_close', 'binance_hour_vwap']
_BTC = None


def load_spot_etf():
    raw = Path(os.environ.get('LONGSCOUT_IBIT_SPOT_FIX', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != IBIT_SPOT_FIX_SHA256:
        raise ValueError('expected pinned IBIT premium and comparison spot-fix history')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', 'NYSE session', FIELDS):
        raise ValueError('invalid IBIT premium archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if (not dates.is_unique or not dates.is_monotonic_increasing
            or not dates.equals(dates.normalize()) or (dates.dayofweek >= 5).any()):
        raise ValueError('expected ordered unique NYSE date labels')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype=float)


def premium_disagrees(frame):
    complete = np.isfinite(frame).all(axis=1) & frame.binance_hour_vwap.gt(0) & frame.binance_close.gt(0)
    opposes_fix_drift = frame.binance_close.le(frame.binance_hour_vwap)
    pulse = complete & frame.ibit_nav_premium_pct.ge(.15) & opposes_fix_drift
    return pulse.set_axis(pulse.index + pd.Timedelta(days=2))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    pulse = premium_disagrees(load_spot_etf()).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_ibit_premium(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    eligible = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (eligible & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l206_ibit_premium_against_spot_fix_drift_1d': dict(
        name='[RESEARCH] L206 IBIT premium against final-hour BTC spot drift', style='momentum',
        fn=sig_ibit_premium, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
