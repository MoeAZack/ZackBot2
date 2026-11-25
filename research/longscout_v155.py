"""Research-only range fade after a sharp BTC futures collateral-share shock.

Compare the daily changes of COIN-M perpetual open contract count and the
USD-M perpetual's quoted open-interest notional at the same 23:55 UTC time.
The COIN-M contract's fixed face-value unit cancels in the log change.
Require an absolute differential of at least 7% and four times the prior
60-day median. BTC must be nonbear and move by at most 1% on the day.
Fade target coins in sideways R1 that move by at least 2% on that day.
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

FAMILY_NOVELTY = ('Uses relative outstanding exposure for two margin forms of the same BTC '
                  'perpetual. V135 compares settled payments, V144 compares quantities '
                  'across different underlying assets, and V146 compares a single contract '
                  'with its trading turnover.')
COLLATERAL_OI_MANIFEST_SHA256 = 'c04a5e6b7419f1ebd2021b148c5d5c4c7ea83212c44350506252502451e268c3'
MARKET_FEATURE = ('COLLATERAL_OI_MANIFEST_SHA256', 'matched BTC COIN-M and USD-M open interest at 23:55 UTC')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_collateral_oi_20261125/manifest.json'
FIELDS = {'BTCUSD_PERP': 'sum_open_interest', 'BTCUSDT': 'sum_open_interest_value'}
_BTC = None


def load_collateral():
    raw = Path(os.environ.get('LONGSCOUT_BTC_COLLATERAL_OI_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != COLLATERAL_OI_MANIFEST_SHA256:
        raise ValueError('expected pinned collateral open-interest input')
    data = json.loads(raw)
    if (data['status'], data['sample'], data['instruments']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '23:55 UTC exact observation', FIELDS):
        raise ValueError('invalid collateral open-interest input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row['date'] for row in data['days']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('collateral open-interest dates must be continuous and unique')
    return pd.DataFrame({s: [row[s].get('value') for row in data['days']]
                         for s in FIELDS}, index=dates).apply(pd.to_numeric, errors='raise')


def collateral_shock(coin_contracts, usd_notional):
    safe = np.isfinite(coin_contracts) & coin_contracts.gt(0) & np.isfinite(usd_notional) & usd_notional.gt(0)
    difference = np.log(coin_contracts.where(safe)) - np.log(usd_notional.where(safe))
    move = difference.diff().abs()
    median = move.shift().rolling(60, min_periods=60).median()
    observed = median.notna() & move.notna()
    shock = observed & move.ge(.07) & move.ge(4. * median)
    return shock & observed.shift(fill_value=False) & ~shock.shift(fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    market = load_collateral()
    shock = collateral_shock(market.BTCUSD_PERP, market.BTCUSDT)
    _BTC = (shock.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))
            & btc.pct_change(fill_method=None).abs().le(.01))


def sig_collateral_shock(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    eligible = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).eq(0)
    change = grid.pct_change(fill_method=None)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series((eligible & change.le(-.02)).reindex(close.index).to_numpy(), index=d.index)),
                se=S.X(pd.Series((eligible & change.ge(.02)).reindex(close.index).to_numpy(), index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r155_btc_collateral_oi_shock_1d': dict(
        name='[RESEARCH] R155 BTC collateral open-interest shock fade', style='range',
        fn=sig_collateral_shock, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
