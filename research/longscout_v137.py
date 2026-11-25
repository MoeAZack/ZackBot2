"""Research-only long after inversion eases between two BTC delivery futures.

Use consecutive quarterly expiries. The front contract must be 15–110 days
from delivery; the back contract must expire 70–110 days later. The previous
three daily observations for the same pair must all have a negative annualized
calendar spread, and the current spread must turn nonnegative. Trade next
open only with a rising daily target close and nonbear BTC and target regimes.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v125 as L125
import strategies as S

FAMILY_NOVELTY = ('Tests repricing between two distinct fixed-delivery futures on the same '
                  'underlying. The calendar spread isolates deferred versus nearer delivery. '
                  'V125 compares one expiry with spot, and V135 compares perpetual payments.')
BTC_DELIVERY_MANIFEST_SHA256 = L125.BTC_DELIVERY_MANIFEST_SHA256
MARKET_FEATURE = ('BTC_DELIVERY_MANIFEST_SHA256', 'matching closes of two BTCUSDT quarterly deliveries')
_BTC = None


def spread_signal(index, contracts, expiries):
    settlement = index + pd.Timedelta(days=1)
    output = pd.Series(False, index=index)
    allocated = output.copy()
    ordered = sorted(expiries, key=expiries.get)
    for front, back in zip(ordered, ordered[1:]):
        front_days = (expiries[front] - settlement) / pd.Timedelta(days=1)
        tenor = (expiries[back] - expiries[front]) / pd.Timedelta(days=1)
        active = ~allocated & (front_days >= 15) & (front_days <= 110)
        allocated |= active
        if not 70 <= tenor <= 110 or front not in contracts or back not in contracts:
            continue
        front_prices = contracts[front].reindex(index)
        back_prices = contracts[back].reindex(index)
        spread = 365 * np.log(back_prices / front_prices) / tenor
        prior = spread.shift().rolling(3, min_periods=3).max()
        output |= active & prior.lt(0) & spread.ge(0)
    return output


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _, contracts, expiries = L125.load_delivery()
    _BTC = spread_signal(btc.index, contracts, expiries) & L1.regime_daily(btc).isin((0., 1.))


def sig_calendar_spread(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l137_btc_delivery_curve_inversion_exit_1d': dict(
        name='[RESEARCH] L137 BTC delivery curve inversion exit long', style='momentum',
        fn=sig_calendar_spread, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
