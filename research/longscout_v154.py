"""Research-only range fade as non-BTC risk spreads across more factors.

Remove each coin's BTC return contribution using its trailing, prior-only
60-day beta. Select the 48 most liquid contracts with complete observations
in the two preceding 20-day windows. Use the same contracts in both windows.
Measure entropy effective rank of standardized residual returns. After rank
rises from at most four to at least six, fade a sideways coin's daily 2%
move when BTC is nonbear. Only the first qualifying daily close can enter.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v145 as L145
import strategies as S

FAMILY_NOVELTY = ('Measures how many independent residual return factors carry variation after '
                  'removing BTC. V115 measures average correlation without isolating the '
                  'residual eigenspectrum. V145 tests a signed high-versus-low beta return spread.')
_BTC = None


def effective_rank(residuals):
    centered = residuals - residuals.mean(axis=0)
    scale = centered.std(axis=0)
    if (scale <= 1e-10).any():
        return np.nan
    singular = np.linalg.svd(centered / scale, full_matrices=False, compute_uv=False)
    variance = singular ** 2
    positive = variance[variance > 1e-10]
    weights = positive / positive.sum()
    return float(np.exp(-np.dot(weights, np.log(weights))))


def factor_diffusion(residuals, liquidity):
    spread = np.full(len(residuals), np.nan)
    history = residuals.to_numpy(dtype=float)
    scores = liquidity.to_numpy(dtype=float)
    for current in range(40, len(history)):
        window = history[current-40:current]
        traded = scores[current]
        observed = np.isfinite(window).all(axis=0) & np.isfinite(traded) & (traded >= 1e7)
        eligible = np.flatnonzero(observed)
        if len(eligible) < 48:
            continue
        selected = eligible[np.argsort(-traded[eligible], kind='stable')[:48]]
        earlier = effective_rank(window[:20, selected])
        recent = effective_rank(window[20:, selected])
        if np.isfinite(earlier) and np.isfinite(recent):
            spread[current] = earlier <= 4. and recent >= 6.
    condition = pd.Series(spread, index=residuals.index)
    return condition.eq(1.) & condition.shift().eq(0.)


def set_panel(panel):
    global _BTC
    btc, _ = L101._native_daily(panel['BTCUSDT'])
    dates = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    btc = btc.reindex(dates)
    coins = {s: L101._native_daily(frame) for s, frame in sorted(panel.items()) if s != 'BTCUSDT'}
    closes = pd.DataFrame({s: close.reindex(dates) for s, (close, _) in coins.items()}, index=dates)
    turnover = pd.DataFrame({s: q.reindex(dates) for s, (_, q) in coins.items()}, index=dates)
    returns = closes.pct_change(fill_method=None)
    btc_return = btc.pct_change(fill_method=None)
    prior_liquidity = turnover.shift().rolling(30, min_periods=30).median()
    residuals = returns - L145.prior_betas(returns, btc_return).mul(btc_return, axis=0)
    onset = factor_diffusion(residuals, prior_liquidity)
    _BTC = onset & L1.regime_daily(btc).isin((0., 1.))


def sig_factor_diffusion(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).eq(0)
    move = grid.pct_change(fill_method=None)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series((active & move.le(-.02)).reindex(close.index).to_numpy(), index=d.index)),
                se=S.X(pd.Series((active & move.ge(.02)).reindex(close.index).to_numpy(), index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r154_residual_factor_diffusion_1d': dict(
        name='[RESEARCH] R154 Residual factor diffusion range fade', style='range',
        fn=sig_factor_diffusion, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
