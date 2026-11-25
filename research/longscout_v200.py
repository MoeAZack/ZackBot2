"""Research-only long fade after the constant-mix history rewards rebalancing.

Over twenty complete daily return pairs, compare a hypothetical 50/50
daily-reset BTC/coin portfolio with a 50/50 portfolio left untouched.
If the reset portfolio gained more and the coin lags BTC over five days,
buy the first qualifying state in a sideways coin with nonbear BTC.
The hypothetical pair is a gross input feature. The strategy holds only
the target, with the frozen simulator's fees, slippage and funding.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Compares daily-rebalanced and untouched BTC/coin pair wealth. V81 and V91 '
                  'measure own serial dependence, V95 ranks recent return and V101 fits a '
                  'peer spread; none measures the relative return of these two portfolios.')
LOG_TWO = np.log(2.)
_BTC = _BTC_REGIME = None


def constant_mix_excess(coin, bitcoin):
    target = np.log(coin.where(np.isfinite(coin) & coin.gt(0)))
    market = np.log(bitcoin.where(np.isfinite(bitcoin) & bitcoin.gt(0)))
    log_mix_daily = np.logaddexp(target.diff(), market.diff()) - LOG_TWO
    daily_reset = log_mix_daily.rolling(20, min_periods=20).sum()
    untouched = np.logaddexp(target.diff(20), market.diff(20)) - LOG_TWO
    return daily_reset - untouched


def long_from_rebalancing(coin, bitcoin, trading_range):
    excess = constant_mix_excess(coin, bitcoin)
    target_change = coin.pct_change(5, fill_method=None)
    btc_change = bitcoin.pct_change(5, fill_method=None)
    relative_lag = target_change.lt(btc_change)
    state = trading_range & excess.gt(1e-12) & relative_lag
    return state & ~state.shift(fill_value=False)


def set_btc(frame):
    global _BTC, _BTC_REGIME
    btc, _ = L101._native_daily(frame)
    _BTC = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _BTC_REGIME = L1.regime_daily(_BTC).isin((0., 1.))


def sig_rebalancing_premium(d, params, ctx):
    close, _ = L101._native_daily(d)
    coin = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    market = _BTC.reindex(coin.index)
    eligible = _BTC_REGIME.reindex(coin.index, fill_value=False) & L1.regime_daily(coin).eq(0)
    buy = long_from_rebalancing(coin, market, eligible).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(buy.to_numpy(), index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l200_coin_btc_rebalancing_premium_fade_1d': dict(
        name='[RESEARCH] L200 Long fade after BTC/coin constant-mix outperformance',
        style='range', fn=sig_rebalancing_premium, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
