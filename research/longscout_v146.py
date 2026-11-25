"""Research-only long when BTC trading capacity catches up to open positions.

Divide the BTC open-interest quantity by its mean base-asset trading volume
over seven complete days. Require a cross below the preceding 30-day median
after the ratio was at least 25% above its reference within the last five
days. BTC open interest and price must both rise on the signal day. Trade
on the next daily open with positive target return and nonbear regimes.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v118 as L118
import strategies as S

FAMILY_NOVELTY = ('Uses the stock of BTC open positions against observed turnover in the same '
                  'base units. V118 requires open interest to contract; this tests increased '
                  'trading capacity with rising open interest. V144 compares only the stock '
                  'of open contracts against two other markets.')
BTC_OI_MANIFEST_SHA256 = L118.BTC_OI_MANIFEST_SHA256
MARKET_FEATURE = ('BTC_OI_MANIFEST_SHA256', 'BTC open-interest quantity relative to frozen daily base-unit turnover')
_BTC = None


def turnover_catches_up(open_interest, base_volume):
    oi = open_interest.where(np.isfinite(open_interest) & open_interest.gt(0))
    volume = base_volume.where(np.isfinite(base_volume) & base_volume.gt(0))
    capacity = volume.rolling(7, min_periods=7).mean()
    ratio = oi / capacity
    reference = ratio.shift().rolling(30, min_periods=30).median()
    relative = ratio / reference
    previous = relative.shift()
    high = previous.rolling(5, min_periods=5).max().ge(1.25)
    return high & previous.gt(1.) & relative.le(1.) & oi.diff().gt(0)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    base = pd.to_numeric(pd.Series(frame.v.to_numpy(), index=btc.index), errors='coerce')
    dates = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    btc = btc.reindex(dates)
    oi = L118.load_open_interest().reindex(dates)
    _BTC = (turnover_catches_up(oi, base.reindex(dates)) & btc.diff().gt(0)
            & L1.regime_daily(btc).isin((0., 1.)))


def sig_open_interest_turnover(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l146_btc_oi_turnover_capacity_recovery_1d': dict(
        name='[RESEARCH] L146 BTC open-interest turnover capacity recovery long',
        style='momentum', fn=sig_open_interest_turnover, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
