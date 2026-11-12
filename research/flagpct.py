"""FLAG-PCT (Claude Projects, 2026-11-12). FROZEN before any run. Source idea: the "bull_flag" daily scan rule of the
public day-trader-skill repo (US stocks, long only), ported literally to crypto and mirrored for shorts.

Differs from the rejected CP-N5 (1h, ATR-sized impulse/flag, EMA trend filter): percentage rule, no trend filter,
4h and daily only.

Rule (both sides, on closed candles only):
  impulse  : close 5 bars ago vs close 15 bars ago >= +10 % (long) / <= -10 % (short)
  flag     : the last 5 closes before the signal bar sit inside a 5 % band (max/min - 1 <= 0.05)
  trigger  : signal close above the flag's highest high (long) / below its lowest low (short)
  stop     : other side of the flag -/+ 0.1 ATR, at least 0.5 ATR away
  manage   : target 2R (repo default), half off at +1R and rest to breakeven (house giveback rule), time cap
CP-FP1 flag_pct_4h (cap 30 bars), CP-FP2 flag_pct_1d (cap 20 bars). No parameter is tuned.
"""
import numpy as np
import pandas as pd
import strategies as S

PROTECT = dict(tp1_r=1.0, tp1_frac=0.5, be_r=1.0)
PARAMS = dict(flag=5, impulse=10, imp_pct=0.10, band_pct=0.05, buf_atr=0.1, min_atr=0.5)


def sig_flag_pct(d, p, ctx):
    f, k = p['flag'], p['impulse']
    fc_max = d.c.rolling(f).max().shift(); fc_min = d.c.rolling(f).min().shift()
    tight = (fc_max / fc_min - 1.0) <= p['band_pct']
    fh = d.h.rolling(f).max().shift(); fl = d.l.rolling(f).min().shift()
    imp = d.c.shift(f + 1) / d.c.shift(f + 1 + k) - 1.0
    le = tight & (imp >= p['imp_pct']) & (d.c > fh)
    se = tight & (imp <= -p['imp_pct']) & (d.c < fl)
    dist = np.where(se, (fh + p['buf_atr'] * d.atr) - d.c, np.where(le, d.c - (fl - p['buf_atr'] * d.atr), np.nan))
    dist = np.maximum(dist, p['min_atr'] * d.atr.values)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z, atr=dist)


CANDIDATES = {
    'cp_fp1_flag_pct_4h': dict(
        name='[RESEARCH] CP-FP1 Percent flag continuation 4h', style='trend_continuation', fn=sig_flag_pct,
        sides='both', tf='4h', research_only=True, params=dict(PARAMS),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=30, **PROTECT)),
    'cp_fp2_flag_pct_1d': dict(
        name='[RESEARCH] CP-FP2 Percent flag continuation daily', style='trend_continuation', fn=sig_flag_pct,
        sides='both', tf='1d', research_only=True, params=dict(PARAMS),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=20, **PROTECT)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
