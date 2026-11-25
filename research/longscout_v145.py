"""Research-only long when exposure to BTC risk resumes leading the market.

Sort contracts by their BTC beta through yesterday, using 60 complete daily
returns. Use the top and bottom quartiles of at least 60 liquid contracts.
Subtract each contract's modeled concurrent BTC contribution before comparing
the quartiles. After five negative-average spread days and a nonpositive
previous day, require a spread of at least +0.5% and buy rising high-beta
coins in nonbear coin and BTC regimes. Effects use the next modeled open.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Times a cross-sectional factor formed by prior BTC-beta quartiles, after '
                  'removing each cohort’s modeled BTC move. V93 and V114 use individual-coin '
                  'beta or convexity. V108 ranks realized returns. V115 uses peer correlations.')
RUNNER = 'longscout_flow_run.py'
_ENTRY = None


def prior_betas(returns, btc):
    covariance = returns.rolling(60, min_periods=60).cov(btc)
    variance = btc.rolling(60, min_periods=60).var().where(lambda value: value > 0)
    return covariance.div(variance, axis=0).shift()


def risk_spread(returns, btc, betas, liquid):
    eligible = liquid & returns.notna() & np.isfinite(betas)
    beta = betas.where(eligible)
    rank = beta.rank(axis=1, method='average', pct=True)
    top, bottom = rank.gt(.75), rank.le(.25)
    residual = returns - beta.mul(btc, axis=0)
    enough = eligible.sum(axis=1).ge(60) & top.sum(axis=1).ge(15) & bottom.sum(axis=1).ge(15)
    spread = residual.where(top).mean(axis=1) - residual.where(bottom).mean(axis=1)
    return spread.where(enough), top


def factor_resumes(spread):
    previous = spread.shift()
    return previous.rolling(5, min_periods=5).mean().lt(0) & previous.le(0) & spread.ge(.005)


def set_panel(panel):
    global _ENTRY
    btc, _ = L101._native_daily(panel['BTCUSDT'])
    dates = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    btc = btc.reindex(dates)
    coins = {s: L101._native_daily(frame) for s, frame in panel.items() if s != 'BTCUSDT'}
    closes = pd.DataFrame({s: close.reindex(dates) for s, (close, _) in coins.items()}, index=dates)
    turnover = pd.DataFrame({s: q.reindex(dates) for s, (_, q) in coins.items()}, index=dates)
    returns = closes.pct_change(fill_method=None)
    btc_return = btc.pct_change(fill_method=None)
    liquid = turnover.shift().rolling(30, min_periods=30).median().ge(1e7)
    spread, top = risk_spread(returns, btc_return, prior_betas(returns, btc_return), liquid)
    market = factor_resumes(spread) & L1.regime_daily(btc).isin((0., 1.))
    _ENTRY = (top & returns.gt(0)).where(market, False, axis=0)


def sig_beta_factor_turn(d, params, ctx):
    close, _ = L101._native_daily(d)
    symbol = L101._context_symbol(ctx)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _ENTRY[symbol].reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l145_btc_beta_factor_leadership_turn_1d': dict(
        name='[RESEARCH] L145 BTC beta-factor leadership turn long', style='momentum',
        fn=sig_beta_factor_turn, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
