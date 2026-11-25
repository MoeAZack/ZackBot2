"""Fade an outlier in a triangle of mutually correlated coin returns.

Each Monday, use the preceding 45 complete daily BTC-adjusted returns for
coins with prior 20-day median quote turnover of at least $10 million.
Choose the target's highest-correlated peer, then its highest-correlated
second peer that also correlates with the first. Require all three
correlations at least 0.65. Hold the fitted peer identities for that UTC
week. When both peers' daily returns are within 0.75 percentage points
and the target is at least two percentage points away, fade the outlier
in a sideways target and nonbear BTC. The simulator holds the target only.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Requires a mutually correlated triangle before fading one coin away from two '
                  'agreeing peers. V99 selects directed lag leaders independently and V101 fits '
                  'an OU spread against one highest-correlated peer.')
WINDOW = 45
_BTC = _SIGNALS = None


def choose_neighbors(correlation):
    values = np.where(np.isfinite(correlation), np.round(correlation, 12), -np.inf)
    np.fill_diagonal(values, -np.inf)
    ranked = np.argmax(values, axis=1)
    first_ok = values[np.arange(len(values)), ranked] >= .65
    second_candidates = (values >= .65) & (values[ranked] >= .65)
    second_scores = np.where(second_candidates, values, -np.inf)
    second = np.argmax(second_scores, axis=1)
    complete = first_ok & np.isfinite(second_scores[np.arange(len(values)), second])
    return np.where(complete, ranked, -1), np.where(complete, second, -1)


def disagreement_flags(own, first, second):
    agree = np.abs(first - second) <= .0075
    deviation = own - (first + second) / 2.
    return agree & (deviation <= -.02), agree & (deviation >= .02)


def paired_dislocations(returns, liquid):
    market = returns.to_numpy(dtype=float)
    days, coins = market.shape
    can_trade = liquid.to_numpy(dtype=bool)
    buys = np.zeros((days, coins), dtype=bool)
    sells = np.zeros((days, coins), dtype=bool)
    targets, first, second = (np.empty(0, dtype=int) for _ in range(3))
    for i in range(WINDOW, days):
        if returns.index[i].dayofweek == 0:
            training = market[i - WINDOW:i]
            complete = np.isfinite(training).all(axis=0) & can_trade[i]
            valid = np.flatnonzero(complete)
            targets = np.empty(0, dtype=int)
            if len(valid) >= 3:
                observations = training[:, valid]
                centered = observations - observations.mean(axis=0)
                norms = np.linalg.norm(centered, axis=0)
                standardized = centered / np.where(norms > 1e-12, norms, np.nan)
                first_pick, second_pick = choose_neighbors(standardized.T @ standardized)
                ready = first_pick >= 0
                targets = valid[ready]
                first, second = valid[first_pick[ready]], valid[second_pick[ready]]
        if not targets.size:
            continue
        buy, sell = disagreement_flags(market[i, targets], market[i, first], market[i, second])
        buys[i, targets] = buy
        sells[i, targets] = sell
    return pd.DataFrame(buys, index=returns.index, columns=returns.columns), pd.DataFrame(
        sells, index=returns.index, columns=returns.columns)


def set_panel(panel):
    global _SIGNALS
    btc, _ = L101._native_daily(panel['BTCUSDT'])
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    coins = {symbol: L101._native_daily(frame) for symbol, frame in sorted(panel.items()) if symbol != 'BTCUSDT'}
    closes = pd.DataFrame({symbol: close for symbol, (close, _) in coins.items()}, index=btc.index)
    volumes = pd.DataFrame({symbol: volume for symbol, (_, volume) in coins.items()}, index=btc.index)
    liquid = volumes.shift().rolling(20, min_periods=20).median().ge(1e7)
    returns = np.log(closes).diff().sub(np.log(btc).diff(), axis=0)
    _SIGNALS = paired_dislocations(returns, liquid)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _BTC = L1.regime_daily(btc).isin((0., 1.))


def sig_triangle_fade(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    eligible = (_BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).eq(0)).reindex(close.index)
    buy, sell = (signal[ctx['research_symbol']].reindex(close.index, fill_value=False) for signal in _SIGNALS)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series((buy & eligible).to_numpy(), index=d.index)),
                se=S.X(pd.Series((sell & eligible).to_numpy(), index=d.index)), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r212_mutual_peer_triangle_outlier_fade_1d': dict(
        name='[RESEARCH] R212 Fade return outlier against mutually correlated peers', style='range',
        fn=sig_triangle_fade, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
