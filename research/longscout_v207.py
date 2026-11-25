"""Research-only two-sided range fade on reentry into forecast-error bounds.

Forecast each daily log close with the preceding ten closes' median. Use
the preceding 60 actual one-step forecast errors to form empirical 10th
and 90th percentile bounds. After an error breached its contemporaneous
bound, fade when the next close moves back inside both bounds while its
error remains on the original side of zero. The coin must be sideways
and BTC must be nonbear. Serial dependence limits coverage interpretation.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Calibrates entry with the realized errors of past one-step forecasts and '
                  'uses causal reentry into asymmetric empirical bounds. V3 uses Bollinger '
                  'bands for rolling price dispersion and V129 uses a latent-state innovation model.')
_BTC = None


def forecast_bounds(price):
    log_price = np.log(price.where(np.isfinite(price) & price.gt(0)))
    forecast = log_price.shift().rolling(10, min_periods=10).median()
    error = log_price - forecast
    calibration = error.shift().rolling(60, min_periods=60)
    return error, calibration.quantile(.10, interpolation='lower'), calibration.quantile(.90, interpolation='higher')


def reentry_flags(price, error, lower, upper):
    valid_bounds = lower.lt(0) & upper.gt(0)
    inside = valid_bounds & error.ge(lower) & error.le(upper)
    outside_lower = (valid_bounds & error.lt(lower)).shift(fill_value=False)
    outside_upper = (valid_bounds & error.gt(upper)).shift(fill_value=False)
    buy = inside & outside_lower & error.lt(0) & price.diff().gt(0)
    sell = inside & outside_upper & error.gt(0) & price.diff().lt(0)
    return buy, sell


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _BTC = L1.regime_daily(btc).isin((0., 1.))


def sig_forecast_band_reentry(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    buy, sell = reentry_flags(grid, *forecast_bounds(grid))
    eligible = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).eq(0)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series((buy & eligible).reindex(close.index).to_numpy(), index=d.index)),
                se=S.X(pd.Series((sell & eligible).reindex(close.index).to_numpy(), index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r207_causal_forecast_interval_reentry_1d': dict(
        name='[RESEARCH] R207 Range fade on reentry into calibrated forecast-error interval',
        style='range', fn=sig_forecast_band_reentry, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
