"""Research-only range fade using learned cross-coin residual reversal.

Fit next-day intraday residual returns against current close-to-close BTC
residuals, pooling the preceding 60 days and excluding the target coin.
Use only labels that have completed at the decision. Train on liquid coins
in sideways R1 with nonbear BTC. Require a peer response slope at most -0.15.
Fade a target dislocation of at least 2% when the fitted reversal is at least
0.5%. The modeled position remains an unhedged Binance futures position.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v145 as L145
import strategies as S

FAMILY_NOVELTY = ('Learns a lead-response slope from other coins’ already completed next-day '
                  'returns and applies it to a held-out instrument. V97 thresholds its own '
                  'BTC residual, V94 uses concurrent cross-sectional dispersion, and V133 '
                  'matches ordered paths within the same instrument.')
RUNNER = 'longscout_flow_run.py'
_LONG = _SHORT = None


def peer_response(displacement, response, liquid_range):
    valid = liquid_range & np.isfinite(displacement) & np.isfinite(response)
    x = displacement.clip(-.20, .20).where(valid)
    y = response.clip(-.20, .20).where(valid)
    moments = [valid.astype(float), x.fillna(0), y.fillna(0), (x * x).fillna(0), (x * y).fillna(0)]
    left_out = []
    for values in moments:
        past = values.shift().rolling(60, min_periods=60).sum()
        left_out.append(past.rsub(past.sum(axis=1), axis=0))
    count, sx, sy, sxx, sxy = left_out
    n = count.where(count.ge(600))
    variance = sxx - sx * sx / n
    slope = (sxy - sx * sy / n) / variance.where(variance.gt(1e-12))
    intercept = (sy - slope * sx) / n
    return slope, intercept


def range_decisions(displacement, slope, intercept, eligible):
    estimate = intercept + slope * displacement.clip(-.20, .20)
    learned = eligible & slope.le(-.15)
    long = learned & displacement.le(-.02) & estimate.ge(.005)
    short = learned & displacement.ge(.02) & estimate.le(-.005)
    return long, short


def set_panel(panel):
    global _LONG, _SHORT
    btc_frame = panel['BTCUSDT']
    btc, _ = L101._native_daily(btc_frame)
    dates = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    btc_open = pd.Series(btc_frame.o.to_numpy(), index=btc.index).reindex(dates)
    btc = btc.reindex(dates)
    frames = {s: f for s, f in sorted(panel.items()) if s != 'BTCUSDT'}
    coins = {s: L101._native_daily(f) for s, f in frames.items()}
    closes = pd.DataFrame({s: close.reindex(dates) for s, (close, _) in coins.items()}, index=dates)
    opens = pd.DataFrame({s: pd.Series(f.o.to_numpy(), index=coins[s][0].index).reindex(dates)
                          for s, f in frames.items()}, index=dates)
    turnover = pd.DataFrame({s: q.reindex(dates) for s, (_, q) in coins.items()}, index=dates)
    returns = closes.pct_change(fill_method=None)
    btc_return = btc.pct_change(fill_method=None)
    beta = L145.prior_betas(returns, btc_return)
    displacement = returns - beta.mul(btc_return, axis=0)
    future_btc = (btc / btc_open.where(btc_open.gt(0)) - 1).shift(-1)
    future_return = (closes / opens.where(opens.gt(0)) - 1).shift(-1)
    response = future_return - beta.mul(future_btc, axis=0)
    liquid = turnover.shift().rolling(30, min_periods=30).median().ge(1e7)
    sideways = closes.apply(lambda values: L1.regime_daily(values).eq(0))
    eligible = (liquid & sideways).where(L1.regime_daily(btc).isin((0., 1.)), False, axis=0)
    slope, intercept = peer_response(displacement, response, eligible)
    _LONG, _SHORT = range_decisions(displacement, slope, intercept, eligible)


def sig_peer_reversal(d, params, ctx):
    close, _ = L101._native_daily(d)
    symbol = L101._context_symbol(ctx)
    buy = _LONG[symbol].reindex(close.index, fill_value=False).to_numpy()
    sell = _SHORT[symbol].reindex(close.index, fill_value=False).to_numpy()
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(buy, index=d.index)), se=S.X(pd.Series(sell, index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r160_cross_coin_residual_reversal_1d': dict(
        name='[RESEARCH] R160 Cross-coin learned residual reversal', style='range',
        fn=sig_peer_reversal, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
