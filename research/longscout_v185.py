"""Research-only range fade once equity volatility-of-volatility settles.

Require the previous VVIX close at least 12% above its prior 30-session
median, with the current close no more than 4% above that benchmark. VIX
must move no more than 4%, and the paired sessions no more than four days
apart. After one extra UTC day, fade a 1.5% daily move in a sideways coin
within nonbear BTC. Exit when the coin reaches its trailing 20-day mean
in the trade direction or the range regime fails.
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

FAMILY_NOVELTY = ('Tests uncertainty about future equity volatility from VIX options, holding '
                  'the contemporaneous VIX level near its previous value. V174 compares two '
                  'SPX-options horizons and V148 filters a crypto coin return directly.')
CBOE_VVIX_SHA256 = 'f446845d6e884dcd5de85558828bdaf1875f1844e4f92d32c884c9b705849f4d'
MARKET_FEATURE = ('CBOE_VVIX_SHA256', 'paired Cboe VVIX and VIX index closes')
MARKET_FEATURE_SOURCE = 'Cboe historical daily index prices'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_cboe_vvix_20261125/manifest.json'
FIELDS = ['VVIX', 'VIX']
_EVENT = _BTC_RANGE = None


def load_vol_of_vol():
    raw = Path(os.environ.get('LONGSCOUT_CBOE_VVIX', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CBOE_VVIX_SHA256:
        raise ValueError('expected pinned Cboe VVIX and VIX index closes')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS):
        raise ValueError('invalid Cboe volatility-of-volatility archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not (dates.is_unique and dates.is_monotonic_increasing and dates.equals(dates.normalize())):
        raise ValueError('Cboe session dates must be sorted unique midnights')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def volatility_uncertainty_settles(quotes):
    valid = (np.isfinite(quotes) & quotes.gt(0)).all(axis=1)
    data = quotes.where(valid, axis=0)
    baseline = data.VVIX.shift(2).rolling(30, min_periods=24).median()
    recent = valid.index.to_series().diff().dt.days.between(1, 4)
    vix_change = data.VIX.pct_change(fill_method=None).abs()
    pulse = (recent & data.VVIX.shift().ge(1.12 * baseline) & data.VVIX.le(1.04 * baseline)
             & vix_change.le(.04))
    return pulse.set_axis(pulse.index + pd.Timedelta(days=1))


def set_btc(frame):
    global _EVENT, _BTC_RANGE
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _BTC_RANGE = L1.regime_daily(btc).isin((0., 1.))
    _EVENT = volatility_uncertainty_settles(load_vol_of_vol()).reindex(btc.index, fill_value=False)


def sig_vvix_fade(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    stable = _BTC_RANGE.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).eq(0)
    reference = grid.shift().rolling(20, min_periods=20).mean()
    day = grid.pct_change(fill_method=None)
    pulse = _EVENT.reindex(grid.index, fill_value=False) & stable
    long = pulse & day.le(-.015) & grid.lt(reference)
    short = pulse & day.ge(.015) & grid.gt(reference)
    close_long = ~stable | grid.ge(reference) | reference.isna()
    close_short = ~stable | grid.le(reference) | reference.isna()
    return {name: S.X(pd.Series(flag.reindex(close.index).to_numpy(), index=d.index))
            for name, flag in zip(('le', 'se', 'lx', 'sx'), (long, short, close_long, close_short))}


CANDIDATES = {
    'r185_cboe_vvix_dislocation_settles_1d': dict(
        name='[RESEARCH] R185 Range fade after implied volatility-of-volatility settles',
        style='range', fn=sig_vvix_fade, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
