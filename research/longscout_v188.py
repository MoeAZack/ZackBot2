"""Research-only long on directed cross-coin price leadership.

Remove each coin's BTC beta estimated on the previous 60 daily returns.
For every target, rank five fixed liquid peers by the correlation of a
peer's prior return with the target's next residual. Fit on the 60 fully
observed liquid pairs ending yesterday. Require lagged correlation at least .25
and at least .10 above absolute contemporaneous correlation. Buy after
the chosen peer gains two residual percentage points while the target
residual has moved at most +.5%, with nonbear BTC and target R1.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Selects a directed one-session leader for an individual target. V101 selects a '
                  'contemporaneous peer for spread reversion, V147 uses spot and perpetual BTC, '
                  'and V160 estimates a common own-residual response from other coins.')
RUNNER = 'longscout_flow_run.py'
LEADERS = ('AAVEUSDT', 'ATOMUSDT', 'BCHUSDT', 'LTCUSDT', 'XLMUSDT')
_SIGNAL = None


def bitcoin_residuals(prices):
    returns = np.log(prices.where(np.isfinite(prices) & prices.gt(0))).diff()
    market = returns.BTCUSDT
    targets = returns.drop(columns='BTCUSDT')
    covariance = targets.rolling(60, min_periods=60).cov(market).shift()
    variance = market.rolling(60, min_periods=60).var().shift()
    beta = covariance.div(variance.where(variance.gt(1e-12)), axis=0)
    return targets - beta.mul(market, axis=0)


def directed_strength(targets, leader):
    lag = targets.rolling(60, min_periods=60).corr(leader.shift()).shift()
    same_day = targets.rolling(60, min_periods=60).corr(leader).shift().abs()
    return lag.where(lag.ge(.25) & (lag - same_day).ge(.10))


def select_leader_move(residuals, leaders):
    best = pd.DataFrame(-np.inf, index=residuals.index, columns=residuals.columns)
    move = pd.DataFrame(np.nan, index=residuals.index, columns=residuals.columns)
    for symbol in sorted(leaders):
        leader = residuals[symbol]
        rank = directed_strength(residuals, leader)
        rank[symbol] = np.nan
        better = rank.gt(best)
        best = best.mask(better, rank)
        move = move.mask(better, leader, axis=0)
    return move


def set_panel(panel):
    global _SIGNAL
    btc, _ = L101._native_daily(panel['BTCUSDT'])
    days = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    native = {symbol: L101._native_daily(frame) for symbol, frame in panel.items()}
    closes = pd.DataFrame({symbol: values[0].reindex(days) for symbol, values in native.items()}, index=days)
    residuals = bitcoin_residuals(closes)
    if not set(LEADERS).issubset(residuals.columns):
        raise ValueError('required native cross-coin leaders are missing')
    liquidity = pd.DataFrame({symbol: values[1].reindex(days) for symbol, values in native.items() if symbol != 'BTCUSDT'})
    liquid = liquidity.shift().rolling(30, min_periods=30).median().ge(1e7)
    expected = select_leader_move(residuals.where(liquid), LEADERS)
    confirm = residuals.le(.005) & closes[residuals.columns].pct_change(fill_method=None).ge(-.02)
    market_ok = L1.regime_daily(btc.reindex(days)).isin((0., 1.))
    _SIGNAL = (expected.ge(.02) & confirm & liquid).where(market_ok, False, axis=0)


def sig_directed_leadership(d, params, ctx):
    symbol = L101._context_symbol(ctx)
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _SIGNAL[symbol].reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    edge = active & ~active.shift(fill_value=False)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(edge.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l188_cross_coin_directed_leadership_1d': dict(
        name='[RESEARCH] L188 Directed cross-coin price leadership long', style='momentum',
        fn=sig_directed_leadership, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
