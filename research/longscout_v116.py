"""Research-only daily fade on a concentrated historical price cycle.

Use the last 96 completed, consecutive UTC daily log closes. Remove their
least-squares linear trend and measure energy in four prespecified cycles:
12, 16, 24 and 32 days. When one cycle accounts for at least 35% of the
detrended variance, fade its outer half-amplitude after it turns toward
its mean. The completed-day return must agree with the turn. Require
sideways BTC and coin R1, and model the next daily open.
"""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Locates the turning phase of a dominant frequency in a completed price history. '
                  'V21 measures one-lag autocorrelation, V81 compares time-domain variances, '
                  'and V96 counts ordinal patterns. None fits and trades the phase of an observed cycle.')
WINDOW = 96
CYCLES = np.array([3, 4, 6, 8])
_BTC = None


def set_btc(frame):
    global _BTC
    close, _ = L101._native_daily(frame)
    close = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    _BTC = L1.regime_daily(close)


def cycle_features(price):
    available = np.full(len(price), np.nan)
    phase = np.full(len(price), np.nan)
    slope = np.full(len(price), np.nan)
    if len(price) < WINDOW:
        return available, phase, slope
    log_price = np.log(price.to_numpy())
    windows = sliding_window_view(log_price, WINDOW)
    valid = np.isfinite(windows).all(axis=1)
    at = np.flatnonzero(valid) + WINDOW - 1
    past = windows[valid]
    time = np.arange(WINDOW) - (WINDOW - 1) / 2
    trend = (past @ time) / (time @ time)
    centered = past - past.mean(axis=1, keepdims=True) - trend[:, None] * time
    variance = np.mean(centered * centered, axis=1)
    coeff = np.fft.rfft(centered, axis=1)[:, CYCLES]
    dominant = np.abs(coeff).argmax(axis=1)
    weights = coeff[np.arange(len(at)), dominant]
    rate = 2 * np.pi * CYCLES[dominant] / WINDOW
    last_phase = np.angle(weights) + rate * (WINDOW - 1)
    loaded = variance > 1e-12
    available[at[loaded]] = 2 * (np.abs(weights[loaded]) / WINDOW)**2 / variance[loaded]
    phase[at[loaded]] = np.cos(last_phase[loaded])
    slope[at[loaded]] = np.cos(last_phase[loaded] + rate[loaded]) - phase[at[loaded]]
    return available, phase, slope


def sig_cycle_fade(d, params, ctx):
    price, _ = L101._native_daily(d)
    grid = price.reindex(pd.date_range(price.index[0], price.index[-1], freq='D'))
    share, phase, slope = cycle_features(grid)
    sideways = L1.regime_daily(grid).eq(0) & _BTC.reindex(grid.index).eq(0)
    change = grid.diff()
    long = sideways & (share >= .35) & (phase <= -.50) & (slope > 0) & change.gt(0)
    short = sideways & (share >= .35) & (phase >= .50) & (slope < 0) & change.lt(0)
    flat = pd.Series(False, index=d.index)
    rising_edge = lambda x: (x & ~x.shift(fill_value=False)).reindex(price.index).to_numpy()
    return dict(le=S.X(pd.Series(rising_edge(long), index=d.index)),
                se=S.X(pd.Series(rising_edge(short), index=d.index)), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r116_daily_spectral_cycle_fade_1d': dict(
        name='[RESEARCH] R116 dominant-cycle range fade', style='range', fn=sig_cycle_fade,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=7),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
