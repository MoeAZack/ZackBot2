"""Research-only range fade during disagreement in BTC taker direction.

Measure the signed taker-buy imbalance in BTC's linear and inverse perpetual
markets using each market's native volume units. Require opposing imbalances
of at least 0.12 in each of three consecutive completed 4h bars, with the
same market leading on all three. Use the first complete disagreement run.
Fade a sideways coin's 1.5% move when BTC is nonbear and moves at most 1%.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Tests persistent opposing taker directions across BTC’s two collateral models. '
                  'V136 tests a reversal in a single market. V135 compares past funding '
                  'payments and V155 compares open interest, without observing trade direction.')
CROSS_MARGIN_FLOW_SHA256 = '98fe9465254e0cb43df719a8fb301a58d55ab53a79427cffaefc35a1ff9a650b'
MARKET_FEATURE = ('CROSS_MARGIN_FLOW_SHA256', '4h buy share in linear and inverse BTC perpetuals')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_cross_margin_flow_20261125/manifest.json'
_BTC = None


def load_flows():
    raw = Path(os.environ.get('LONGSCOUT_BTC_CROSS_MARGIN_FLOW', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CROSS_MARGIN_FLOW_SHA256:
        raise ValueError('expected pinned cross-margin BTC input')
    data = json.loads(raw)
    fields = {'BTCUSDT': ['quote_volume', 'taker_buy_quote_volume'],
              'BTCUSD_PERP': ['number_of_contracts', 'taker_buy_contracts']}
    expected = ('COMPLETE_WITH_EXPLICIT_MASKS', '4h', ['BTCUSDT', 'BTCUSD_PERP'], fields)
    if (data['status'], data['timeframe'], data['instruments'], data['fields']) != expected:
        raise ValueError('invalid cross-margin flow input contract')
    stamps, linear, inverse = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(stamps, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('cross-margin flow dates must be continuous and unique')
    inputs = [pd.DataFrame(list(row or [None, None] for row in values), index=dates, columns=['total', 'buy']).apply(pd.to_numeric)
              for values in (linear, inverse)]
    return inputs


def signed_imbalance(total, buy):
    valid = np.isfinite(total) & total.gt(0) & np.isfinite(buy) & buy.ge(0) & buy.le(total)
    return 2 * buy.where(valid) / total.where(valid) - 1


def disagreement(linear, inverse):
    observed = linear.notna() & inverse.notna()
    linear_leads = observed & linear.ge(.12) & inverse.le(-.12)
    inverse_leads = observed & inverse.ge(.12) & linear.le(-.12)
    run = linear_leads.rolling(3, min_periods=3).sum().eq(3) | inverse_leads.rolling(3, min_periods=3).sum().eq(3)
    return run & observed.shift(3, fill_value=False) & ~run.shift(fill_value=False)


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    linear, inverse = (value.reindex(grid) for value in load_flows())
    event = disagreement(signed_imbalance(linear.total, linear.buy), signed_imbalance(inverse.total, inverse.buy))
    price = pd.Series(frame.c.to_numpy(), index=dates).reindex(grid)
    regime = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    _BTC = event & regime.reindex(grid, fill_value=False) & price.pct_change(fill_method=None).abs().le(.01)


def sig_cross_margin_disagreement(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    price = pd.Series(d.c.to_numpy(), index=dates).reindex(grid)
    change = price.pct_change(fill_method=None).reindex(dates).to_numpy()
    active = _BTC.reindex(dates, fill_value=False).to_numpy() & (L1.coin_regime(d) == 0)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(active & (change <= -.015), index=d.index)),
                se=S.X(pd.Series(active & (change >= .015), index=d.index)), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r158_btc_cross_margin_flow_disagreement_4h': dict(
        name='[RESEARCH] R158 BTC cross-margin flow disagreement range fade', style='range',
        fn=sig_cross_margin_disagreement, sides='both', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
