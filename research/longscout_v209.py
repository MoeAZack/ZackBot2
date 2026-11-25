"""Research-only long when BTC aggressive buying begins in spot before futures.

On paired 4h Binance candles, start with nonpositive imbalance on both
markets. On the next completed bar require spot imbalance at least +0.10
while USD-M perpetual imbalance stays at or below -0.05. On the following
bar require the perpetual to move to at least +0.05 while spot stays at
least +0.02. Buy a rising nonbear coin if BTC also rises in nonbear R1.
This tests observed order-flow sequence, which does not establish causation.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Tests a sequential handoff from cash BTC to the native perpetual. V136 '
                  'tests a reversal within the perpetual alone, V158 tests persistent '
                  'disagreement between two futures, and V188 uses price returns without flow.')
BTC_SPOT_PERP_FLOW_SHA256 = 'a2e4014b075c4ebc5a135dc7e26468b651fd7d3d2014a715ff9d2b9dddfc5bc2'
MARKET_FEATURE = ('BTC_SPOT_PERP_FLOW_SHA256', 'BTC Binance spot and USD-M perpetual taker-buy turnover')
MARKET_FEATURE_SOURCE = 'Binance spot BTCUSDT 1h and pinned native USD-M BTCUSDT 4h klines'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_spot_flow_20261125/manifest.json'
FIELDS = ['spot_quote', 'spot_taker_buy_quote', 'perp_quote', 'perp_taker_buy_quote']
_BTC = None


def load_paired_flow():
    raw = Path(os.environ.get('LONGSCOUT_BTC_SPOT_PERP_FLOW', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BTC_SPOT_PERP_FLOW_SHA256:
        raise ValueError('expected pinned Binance spot and perpetual flow records')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '4h', FIELDS):
        raise ValueError('invalid paired BTC flow archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']], unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('paired market dates must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype=float)


def imbalance(total, buy):
    available = np.isfinite(total) & np.isfinite(buy) & total.gt(0) & buy.ge(0) & buy.le(total)
    return 2 * buy.where(available) / total.where(available) - 1


def spot_lead_confirmed(spot, perpetual):
    both_soft = spot.le(0) & perpetual.le(0)
    cash_first = spot.ge(.10) & perpetual.le(-.05)
    futures_confirm = spot.ge(.02) & perpetual.ge(.05)
    return both_soft.shift(2, fill_value=False) & cash_first.shift(1, fill_value=False) & futures_confirm


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    data = load_paired_flow().reindex(grid)
    spot = imbalance(data.spot_quote, data.spot_taker_buy_quote)
    perp = imbalance(data.perp_quote, data.perp_taker_buy_quote)
    price = pd.Series(frame.c.to_numpy(), index=dates).reindex(grid)
    eligible = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    _BTC = spot_lead_confirmed(spot, perp) & price.diff().gt(0) & eligible.reindex(grid, fill_value=False)


def sig_spot_lead(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    contiguous = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    enter = (_BTC.reindex(dates, fill_value=False).to_numpy() & contiguous & d.c.diff().gt(0).to_numpy()
             & np.isin(L1.coin_regime(d), (0., 1.)))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter, index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l209_btc_spot_flow_leads_perp_4h': dict(
        name='[RESEARCH] L209 BTC spot buy flow leads perpetual confirmation', style='momentum',
        fn=sig_spot_lead, sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
