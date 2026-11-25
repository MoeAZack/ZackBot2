"""Research-only range fade after a BTC trade-arrival concentration shock.

Normalize each of six 4h trade counts by 42 preceding counts from the same
UTC slot. On complete days compute the coefficient of variation across
those six relative counts. Require the first day with variation at least
0.50 and twice its prior 30-day median while quote turnover remains within
80–125% of its prior 30-day median. Fade a sideways target coin's move of
at least 1.5% when BTC is nonbear and within 1% on the day.
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

FAMILY_NOVELTY = ('Tests concentration in the timing of trade arrivals after controlling for UTC '
                  'slot and aggregate dollar turnover. V89 studies dollar-volume allocation '
                  'across days, V112 uses turnover as a clock, and V98 addresses taker direction '
                  'or average print size without modeling the timing of arrival clusters.')
TRADE_ARRIVAL_MANIFEST_SHA256 = '7a67fc9a48039bb9815a97d0c0da1042f153fab44ca46bbc5d7c6473c7828a5e'
MARKET_FEATURE = ('TRADE_ARRIVAL_MANIFEST_SHA256', 'BTC USD-M counts and quote turnover in six 4h slots')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_trade_arrival_20261125/manifest.json'
_BTC = None


def load_arrivals():
    raw = Path(os.environ.get('LONGSCOUT_BTC_TRADE_ARRIVAL_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != TRADE_ARRIVAL_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC trade arrival data')
    data = json.loads(raw)
    fields = ['quote_turnover', 'trade_count']
    expected = ('COMPLETE_WITH_EXPLICIT_MASKS', 'BTCUSDT', '4h', fields)
    if (data['status'], data['symbol'], data['timeframe'], data['fields']) != expected:
        raise ValueError('invalid BTC trade arrival input contract')
    stamps, values = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(stamps, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('BTC trade arrival dates must be continuous and unique')
    return pd.DataFrame([row or [None, None] for row in values], index=dates, columns=fields).apply(pd.to_numeric)


def arrival_shock(count, turnover):
    valid = np.isfinite(count) & count.gt(0) & count.eq(np.floor(count)) & np.isfinite(turnover) & turnover.gt(0)
    counts = count.where(valid)
    expected = counts.groupby(count.index.hour).transform(lambda slot: slot.shift().rolling(42, min_periods=42).median())
    normalized = counts / expected
    per_day = normalized.resample('D')
    variation = (per_day.std(ddof=0) / per_day.mean()).where(per_day.count().eq(6))
    dollars = turnover.where(valid).resample('D').sum(min_count=6)
    base_cv = variation.shift().rolling(30, min_periods=30).median()
    base_dollars = dollars.shift().rolling(30, min_periods=30).median()
    observed = variation.notna() & base_cv.notna() & dollars.notna() & base_dollars.gt(0)
    settled = (dollars / base_dollars).between(.80, 1.25)
    unusual = observed & variation.ge(.50) & variation.ge(2 * base_cv) & settled
    return unusual & observed.shift(fill_value=False) & ~unusual.shift(fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    data = load_arrivals()
    shock = arrival_shock(data.trade_count, data.quote_turnover)
    _BTC = (shock.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))
            & btc.pct_change(fill_method=None).abs().le(.01))


def sig_arrival_concentration(d, params, ctx):
    close, _ = L101._native_daily(d)
    full = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(full.index, fill_value=False) & L1.regime_daily(full).eq(0)
    move = full.pct_change(fill_method=None)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series((active & move.le(-.015)).reindex(close.index).to_numpy(), index=d.index)),
                se=S.X(pd.Series((active & move.ge(.015)).reindex(close.index).to_numpy(), index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r151_btc_trade_arrival_concentration_1d': dict(
        name='[RESEARCH] R151 BTC arrival concentration range fade', style='range',
        fn=sig_arrival_concentration, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
