"""Research-only range fade when BTC moves against large-account positioning.

Compare the public top-trader position ratio with the account-count ratio
at 23:55 UTC. Their log difference captures concentration by side. Use the
prior 45 completed observations as the median baseline. Fade the direction
of the concentration when it is beyond +/−log(1.25) from its baseline and
BTC's completed price move goes against that direction. Take longs on
underperformers and shorts on outperformers over three days. Require
sideways coin R1 and rising/sideways BTC R1. Model the next daily open.
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

FAMILY_NOVELTY = ('Compares position concentration by direction with account counts and fades when '
                  'price moves against the concentrated side. V117 uses settled funding and V118 '
                  'uses total open interest. Neither observes how exposure is distributed by side.')
BTC_POSITION_MANIFEST_SHA256 = 'f1bb1094b5ea58a1843f7f02037e98a20434d816266275f1848e5d8042d1918f'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_position_20261125/manifest.json'
ACCOUNT = 'count_toptrader_long_short_ratio'
POSITION = 'sum_toptrader_long_short_ratio'
_MARKET = None


def load_ratios():
    path = Path(os.environ.get('LONGSCOUT_BTC_POSITION_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if BTC_POSITION_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != BTC_POSITION_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC position manifest')
    data = json.loads(raw)
    if (data['status'], data['fields'], data['sample']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', [ACCOUNT, POSITION], '23:55 UTC exact observation',
    ):
        raise ValueError('invalid BTC positioning source contract')
    dates = pd.DatetimeIndex([row['date'] for row in data['days']])
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BTC positioning dates must be unique and consecutive')
    ratios = pd.DataFrame({field: [row.get(field) for row in data['days']] for field in (ACCOUNT, POSITION)}, index=dates)
    ratios = ratios.apply(pd.to_numeric, errors='raise')
    return ratios.where(np.isfinite(ratios) & ratios.gt(0))


def market_conditions(btc, ratios):
    rates = ratios.reindex(btc.index)
    concentration = np.log(rates[POSITION]) - np.log(rates[ACCOUNT])
    base = concentration.shift().rolling(45, min_periods=45).median()
    displacement = concentration - base
    move = btc.pct_change(fill_method=None)
    btc_nonbear = L1.regime_daily(btc).isin((0., 1.))
    long = btc_nonbear & concentration.lt(0) & displacement.le(-np.log(1.25)) & move.gt(0)
    short = btc_nonbear & concentration.gt(0) & displacement.ge(np.log(1.25)) & move.lt(0)
    return pd.DataFrame(dict(long=long, short=short, btc_3d=btc.pct_change(3, fill_method=None)))


def set_btc(frame):
    global _MARKET
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _MARKET = market_conditions(btc, load_ratios())


def sig_position_fade(d, params, ctx):
    price, _ = L101._native_daily(d)
    prices = price.reindex(pd.date_range(price.index[0], price.index[-1], freq='D'))
    market = _MARKET.reindex(prices.index)
    coin_ready = prices.notna().rolling(4, min_periods=4).sum().eq(4) & L1.regime_daily(prices).eq(0)
    relative = prices.pct_change(3, fill_method=None) - market.btc_3d
    long = market.long.eq(True) & coin_ready & relative.lt(0)
    short = market.short.eq(True) & coin_ready & relative.gt(0)
    edge = lambda b: (b & ~b.shift(fill_value=False)).reindex(price.index).to_numpy()
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(edge(long), index=d.index)),
                se=S.X(pd.Series(edge(short), index=d.index)), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r119_btc_position_concentration_fade_1d': dict(
        name='[RESEARCH] R119 BTC position concentration fade', style='range', fn=sig_position_fade,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
