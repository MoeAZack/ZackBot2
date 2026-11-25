"""Research-only long when BTC spot leads price discovery in its perpetual.

Compare two partial correlations over the 120 completed 4h observations
ending before the decision bar. Each measures one market's lagged return
against the other market's current return, controlling for the latter's own
lagged return. Require spot-to-perpetual correlation of at least +0.20 and
at least 0.10 above the reverse direction. Enter when spot rises at least
0.5% and outpaces the perpetual, with rising target and nonbear regimes.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Measures lagged price information between spot and perpetual BTC after '
                  'controlling for the receiving market’s own prior return. V21 tests '
                  'BTC-to-alt price catch-up; V122 uses turnover shares; V128 and V132 '
                  'compare contemporaneous reference prices.')
SPOT_LEADERSHIP_MANIFEST_SHA256 = '2f90a9194a7779eaa084a0511385875062bd85c7c2feb60a462731aeb24671bc'
MARKET_FEATURE = ('SPOT_LEADERSHIP_MANIFEST_SHA256', 'completed BTCUSDT spot 4h close')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_spot_leadership_20261125/manifest.json'
_BTC = None


def load_spot():
    raw = Path(os.environ.get('LONGSCOUT_BTC_SPOT_LEADERSHIP_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SPOT_LEADERSHIP_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC spot closes')
    data = json.loads(raw)
    expected = ('COMPLETE_WITH_EXPLICIT_MASKS', 'BTCUSDT', '4h', 'spot_close')
    if (data['status'], data['symbol'], data['timeframe'], data['field']) != expected:
        raise ValueError('invalid spot leadership input contract')
    stamps, values = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(stamps, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('BTC spot dates must be continuous and unique')
    prices = pd.to_numeric(pd.Series(values, index=dates), errors='raise')
    return prices.where(np.isfinite(prices) & prices.gt(0))


def directional_lead(target, predictor):
    suggested, own_lag = predictor.shift(), target.shift()
    valid = np.isfinite(target) & np.isfinite(suggested) & np.isfinite(own_lag)
    target, suggested, own_lag = target.where(valid), suggested.where(valid), own_lag.where(valid)
    raw = target.rolling(120, min_periods=120).corr(suggested)
    target_own = target.rolling(120, min_periods=120).corr(own_lag)
    suggested_own = suggested.rolling(120, min_periods=120).corr(own_lag)
    denom = (1 - target_own.pow(2)) * (1 - suggested_own.pow(2))
    partial = (raw - target_own * suggested_own) / np.sqrt(denom.where(denom.gt(1e-6)))
    return partial.clip(-1., 1.).shift()


def spot_confirmation(spot, perpetual):
    spot_lead = directional_lead(perpetual, spot)
    perpetual_lead = directional_lead(spot, perpetual)
    return spot_lead.ge(.20) & (spot_lead - perpetual_lead).ge(.10) & spot.ge(.005) & spot.gt(perpetual)


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    market = pd.Series(frame.c.to_numpy(), index=dates).reindex(grid)
    perp = market.pct_change(fill_method=None)
    spot = load_spot().reindex(grid).pct_change(fill_method=None)
    nonbear = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    _BTC = spot_confirmation(spot, perp) & nonbear.reindex(grid, fill_value=False)


def sig_spot_leadership(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    consecutive = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    event = (_BTC.reindex(dates, fill_value=False).to_numpy() & consecutive & d.c.diff().gt(0).to_numpy()
             & np.isin(L1.coin_regime(d), (0., 1.)))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event, index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l147_btc_spot_price_discovery_4h': dict(
        name='[RESEARCH] L147 BTC spot price discovery long', style='momentum',
        fn=sig_spot_leadership, sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
