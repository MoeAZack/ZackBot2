"""Research-only long on broad increases in alt futures account positioning.

At 23:55 UTC compare each of five fixed Binance USD-M all-account long/short
ratios with its own prior 60-day median, with at least 50 observed days.
Require all five markets on each decision day. Buy after the share above
normal rises to at least 80% from no more than 40% yesterday and on average
over the preceding five days. Require positive target return and nonbear
BTC and target regimes. Account counts do not measure position size.
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

FAMILY_NOVELTY = ('Tests synchronized changes in account positioning across five different alt '
                  'contracts. V142 compares two account cohorts inside BTC, V92 uses price '
                  'breadth, and V144 uses the amount of open interest without its account side.')
ALT_ACCOUNT_BREADTH_SHA256 = '4376a1e63354a731d37e76ca9270acd37a6d49efc2cb15811f13862da0544b29'
MARKET_FEATURE = ('ALT_ACCOUNT_BREADTH_SHA256', 'five matched alt perpetual account-count ratios at 23:55 UTC')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_alt_account_breadth_20261125/manifest.json'
MARKETS = ['ADAUSDT', 'DOGEUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']
_BTC = None


def load_ratios():
    raw = Path(os.environ.get('LONGSCOUT_ALT_ACCOUNT_BREADTH', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ALT_ACCOUNT_BREADTH_SHA256:
        raise ValueError('expected pinned alt account breadth')
    data = json.loads(raw)
    expected = ('COMPLETE_WITH_EXPLICIT_MASKS', 'count_long_short_ratio', '23:55 UTC exact observation', MARKETS)
    if (data['status'], data['field'], data['sample'], data['markets']) != expected:
        raise ValueError('invalid alt account breadth input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row['date'] for row in data['days']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('alt account breadth dates must be continuous and unique')
    return pd.DataFrame({symbol: [row['markets'][symbol].get('ratio') for row in data['days']]
                         for symbol in MARKETS}, index=dates).apply(pd.to_numeric, errors='raise')


def broadening(accounts):
    ratios = accounts.where(np.isfinite(accounts) & accounts.gt(0))
    reference = ratios.shift().rolling(60, min_periods=50).median()
    complete = ratios.notna().all(axis=1) & reference.notna().all(axis=1)
    breadth = ratios.gt(reference).mean(axis=1).where(complete)
    prior = breadth.shift()
    return breadth.ge(.8) & prior.le(.4) & prior.rolling(5, min_periods=5).mean().le(.4)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    surge = broadening(load_ratios())
    _BTC = surge.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_account_breadth(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l161_alt_account_positioning_breadth_1d': dict(
        name='[RESEARCH] L161 Alt account-positioning breadth long', style='momentum',
        fn=sig_account_breadth, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
