"""Research-only long on broad expansion in liquid futures markets.

Compare each coin's prior-30-day median quote turnover with $10m. Count
entrants and exits only if both days have valid observations for the coin.
Over seven days require net entrants to exceed 5% of today's paired
observation count, with at least 60 paired observations daily. Enter the
first time breadth crosses that threshold. Buy a coin with positive 10d
return under rising/sideways coin and BTC R1. Simulate the next daily open.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Trades expansion of the set of liquid markets, based on paired entries and exits '
                  'from a quote-turnover tier. V92 counts positive returns, V82 measures one '
                  'market’s price impact, and V108 treats liquidity as a filter rather than an event.')
_MARKET = None


def liquid_net_change(median_turnover):
    previous = median_turnover.shift()
    paired = median_turnover.notna() & previous.notna()
    coverage = paired.sum(axis=1)
    liquid = median_turnover.ge(1e7)
    was_liquid = previous.ge(1e7)
    entrants = (paired & liquid & ~was_liquid).sum(axis=1)
    exits = (paired & ~liquid & was_liquid).sum(axis=1)
    daily_net = (entrants - exits).where(coverage.ge(60))
    return daily_net.rolling(7, min_periods=7).sum() / coverage.where(coverage.ge(60))


def set_panel(panel):
    global _MARKET
    btc, _ = L101._native_daily(panel['BTCUSDT'])
    dates = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    peer_inputs = {s: L101._native_daily(frame) for s, frame in panel.items() if s != 'BTCUSDT'}
    volumes = pd.DataFrame({s: q.reindex(dates).where(c.reindex(dates).notna())
                            for s, (c, q) in peer_inputs.items()})
    medians = volumes.shift().rolling(30, min_periods=30).median()
    expansion = liquid_net_change(medians)
    onset = expansion.gt(.05) & expansion.shift().le(.05)
    _MARKET = onset & L1.regime_daily(btc.reindex(dates)).isin((0., 1.))


def sig_liquid_expansion(d, params, ctx):
    close, _ = L101._native_daily(d)
    price = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    ready = price.notna().rolling(11, min_periods=11).sum().eq(11)
    optimal = ready & price.gt(price.shift(10)) & L1.regime_daily(price).isin((0., 1.))
    entry = optimal & _MARKET.reindex(price.index, fill_value=False)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(entry.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l130_liquid_market_membership_expansion_1d': dict(
        name='[RESEARCH] L130 liquid market membership expansion', style='momentum',
        fn=sig_liquid_expansion, sides='long', tf='1d', research_only=True,
        params={}, mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
