"""PFX-1H premium-index flush reversal (P3_P4_FROZEN_RULING @458011c1, P4). Research only, default OFF.
Premium is a SIGNED fraction (may be negative/zero). At closed hour t: L_t/H_t premium low/high. Over the prior 168
CONSECUTIVE valid premium hours (excluding t): mL = median(lows), MADL = median(|low - mL|) raw/unscaled in that SAME window;
mH/MADH likewise on highs. Long: L_t <= mL - 4*MADL and futures c_t - o_t <= -ATR14_t. Short: H_t >= mH + 4*MADH and
c_t - o_t >= ATR14_t (price units). LEVEL events. MAD nonfinite/<=0 skips that side. No other filter.
Field 8 ('count'/ignore) is never used."""
import numpy as np, pandas as pd

WIN, K = 168, 4.0


def premium_valid(p):
    """p: hourly premium frame (o,h,l,c) on a complete grid; validity = finite and OHLC ordering only (no sign rule)."""
    f = np.isfinite(p[['o', 'h', 'l', 'c']]).all(axis=1)
    return f & (p.h >= p[['o', 'c']].max(axis=1)) & (p.l <= p[['o', 'c']].min(axis=1))


def _med_mad(x):
    m = np.median(x); return m, np.median(np.abs(x - m))


def rolling_med_mad(s, ok):
    """Prior-only (excluding t) median and raw MAD over exactly WIN consecutive valid values; NaN otherwise."""
    v = s.where(ok).to_numpy(dtype=float); n = len(v)
    med = np.full(n, np.nan); mad = np.full(n, np.nan)
    good = np.isfinite(v).astype(int); run = np.convolve(good, np.ones(WIN, int), 'full')[:n]  # count in [k-WIN+1, k]
    for t in range(WIN, n):
        if run[t - 1] == WIN:                      # window t-WIN .. t-1 all valid
            med[t], mad[t] = _med_mad(v[t - WIN:t])
    return pd.Series(med, index=s.index), pd.Series(mad, index=s.index)


def signal(prem, fut, atr):
    """prem: premium (o,h,l,c) and fut: futures (o,h,l,c) frames on the SAME complete hourly grid; atr aligned to the grid.
    Returns side series (+1 long, -1 short, 0) and diagnostics."""
    assert prem.index.equals(fut.index)
    pok = premium_valid(prem)
    fok = (np.isfinite(fut[['o', 'h', 'l', 'c']]).all(axis=1) & (fut[['o', 'h', 'l', 'c']] > 0).all(axis=1)
           & (fut.h >= fut[['o', 'c']].max(axis=1)) & (fut.l <= fut[['o', 'c']].min(axis=1)))
    mL, madL = rolling_med_mad(prem.l, pok); mH, madH = rolling_med_mad(prem.h, pok)
    a = pd.Series(np.asarray(atr, dtype=float), index=fut.index)
    disp = fut.c - fut.o
    base = pok & fok & np.isfinite(a) & (a > 0)
    lo = base & np.isfinite(madL) & (madL > 0) & (prem.l <= mL - K * madL) & (disp <= -a)
    sh = base & np.isfinite(madH) & (madH > 0) & (prem.h >= mH + K * madH) & (disp >= a)
    assert not (lo & sh).any()
    side = pd.Series(0, index=fut.index, dtype=int); side[lo] = 1; side[sh] = -1
    return side, dict(mL=mL, madL=madL, mH=mH, madH=madH, pok=pok)
