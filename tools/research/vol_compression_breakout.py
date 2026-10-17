"""C2 `vol_compression_breakout.v0`: research-only signal detector + per-symbol simulator (synthetic fixtures first).

Frozen contract (later overrides earlier): Build draft #13 6229540622, Cowork A1-A11 / fixture list 6229554095, Build
defaults 6229577375, Codex FREEZE 6229577171 (authoritative). DEVELOPMENT ONLY: this module reads no cache, store, archive
or holdout and is registered nowhere (not strategies.STRATEGIES, not engine slots / sleeves / presets, not the app). It runs
on an in-memory frame (columns o, h, l, c; optional v / t; optional `atr` to inject ATR14 in fixtures).

Why a local simulator and not backtest.run: same reasons as C1 (`liquidity_sweep_fvg`): close-rule exits fill at the NEXT
open, the stop is structural (box midpoint - 0.10 ATR14[b]), the target is a per-trade price rebased from the actual entry,
and there is a gap-at-entry skip and an entry-based cost guard. Cost constants are imported from backtest (FEE taker 5 bps
per fill, SLIP 2 bps per fill, FUND_PER_BAR 5e-5 per 4h bar = 3.125e-6 per 15m x 16); ATR14 is strategies.indicators()
['atr'] (Wilder TR-EWM alpha 1/14, adjust=False; index 0 defined).

Rules (long; short is the exact price mirror, implemented by running the long code on (-o, -l, -h, -c)):
  box at candidate b: bars b-12..b-1, Hb = max H, Lb = min L.
  eligibility: b >= 120 and every ATR14[b-120..b-1] finite and > 0; ATR14[b] finite.
  compression: ATR14[b-1] <= 0.75 x median(ATR14[b-120..b-1]) (ordinary median, mean of the two middle values);
    Hb - Lb <= 3.0 x ATR14[b-1].
  breakout at close b: C[b] > Hb + 0.10 ATR14[b-1] (strict); C[b] - O[b] >= 0.5 ATR14[b-1];
    H[b] > L[b] and C[b] - L[b] >= 0.70 (H[b] - L[b]).  Filters use ATR14[b-1]; only the stop buffer uses ATR14[b].
  stop = (Hb + Lb)/2 - 0.10 ATR14[b].  entry = open[b+1] = E.  Skips: E <= stop (gap_stop); R = E - stop,
    R < 0.84% x E (real price) (cost_guard).  target = E + 3R (rebased from the actual entry).
  exits (bar k >= b+1): at open[k], a gap through stop (O <= stop) or target (O >= target) fills at the open and owns
    the label, even over a pending close-rule exit; else a pending close-rule exit fills at open[k]; else intrabar stop
    (L <= stop) beats target (H >= target), both at their level; then at close[k]: C[k] < Hb -> invalidation (equality
    does not invalidate), k == b+18 -> time; invalidation label wins; fills at open[k+1].
  box consumption (symbol level, both sides): a candidate that passes every compression + breakout test is a signal iff
    b >= previous signal b + 13; every signal consumes (taken, gap-skipped, cost-skipped, overlap-ignored, no next bar);
    a blocked candidate or a failed filter consumes nothing.
  one trade per symbol: a signal at b is taken only when no trade is open at the close of b (a trade whose exit fills at
    open[b+1] still owns the slot); otherwise it is ignored (not queued).
  same-b long/short conflict is impossible (C > Hb + buf and C < Lb - buf with Hb >= Lb); it is asserted, not handled.
Every threshold comparison is exact (fractions of the float inputs and of the decimal constants), so equality fixtures
follow the written operators exactly."""
from __future__ import annotations

import os
import sys
from fractions import Fraction

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest as BT                                                                       # noqa: E402
import strategies as ST                                                                     # noqa: E402

FAMILY = 'vol_compression_breakout'
CANDIDATE = 'vol_compression_breakout.v0'
DEV_LABEL = 'DEVELOPMENT-ONLY-SYNTHETIC'
TF = '4h'
BOX = 12                         # box = b-12..b-1
ATR_HIST = 120                   # ATR14[b-120..b-1] all finite and > 0; earliest b = 120
RATIO_MAX = Fraction('0.75')
WIDTH_ATR = Fraction('3.0')
BUF_ATR = Fraction('0.10')
BODY_ATR = Fraction('0.5')
CLOC_MIN = Fraction('0.70')
STOP_ATR = Fraction('0.10')
COST_GUARD = Fraction('0.0084')
TARGET_R = 3
CAP_BARS = 18                    # time cap decided at close of b+18, exit at open of b+19
CONSUME = BOX + 1                # next signal needs b' >= b + 13 (box window entirely after b)
FEE, SLIP, FUND_PER_BAR = BT.FEE, BT.SLIP, BT.FUND_PER_BAR
SIDES = ('long', 'short')


def fr(x) -> Fraction:
    return Fraction(float(x))


def atr14(df) -> np.ndarray:
    """ATR14 exactly as strategies.indicators (a frame that already carries `atr` keeps it: fixture injection)."""
    d = df if 'atr' in df else df.assign(v=df['v'] if 'v' in df else 0.0)
    return np.asarray(ST.indicators(d)['atr'], dtype=float)


def _arrays(df, side):
    o, h, l, c = (np.asarray(df[k], dtype=float) for k in ('o', 'h', 'l', 'c'))
    if side == 'long':
        return o, h, l, c
    if side == 'short':
        return -o, -l, -h, -c
    raise ValueError(f'side must be long|short, not {side!r}')


def _real(side, x):
    return -x if side == 'short' else x


def median_fr(vals) -> Fraction:
    s = sorted(fr(v) for v in vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _atr_window(A, b):
    """ATR14[b-120..b-1] when b is eligible (all 120 finite and > 0, ATR14[b] finite), else None."""
    if b < ATR_HIST or b >= len(A) or not np.isfinite(A[b]):
        return None
    w = A[b - ATR_HIST:b]
    return w if bool(np.all(np.isfinite(w)) and np.all(w > 0)) else None


def detect(df, side: str, atr=None) -> list[dict]:
    """Raw candidates for one side (every compression + breakout test passed). A candidate at b reads bars 0..b only.
    Box consumption and the slot are applied by run_symbol."""
    O, H, L, C = _arrays(df, side)
    A = atr14(df) if atr is None else np.asarray(atr, dtype=float)
    out = []
    for b in range(len(C)):
        w = _atr_window(A, b)
        if w is None:
            continue
        a1 = fr(A[b - 1])
        hb, lb = fr(max(H[b - BOX:b])), fr(min(L[b - BOX:b]))
        cb, ob, hh, ll = fr(C[b]), fr(O[b]), fr(H[b]), fr(L[b])
        if not cb > hb + BUF_ATR * a1:
            continue
        if not cb - ob >= BODY_ATR * a1:
            continue
        if not (hh > ll and cb - ll >= CLOC_MIN * (hh - ll)):
            continue
        if not hb - lb <= WIDTH_ATR * a1:
            continue
        if not a1 <= RATIO_MAX * median_fr(w):
            continue
        stop = (hb + lb) / 2 - STOP_ATR * fr(A[b])
        bx_hi, bx_lo = (hb, lb) if side == 'long' else (-lb, -hb)                # public box in real prices, hi >= lo
        out.append(dict(side=side, b=b, box_hi=float(bx_hi), box_lo=float(bx_lo),
                        stop=float(_real(side, stop)), atr_prev=float(A[b - 1]), atr_b=float(A[b]),
                        _stop=stop, _hb=hb))
    return out


def simulate_trade(df, sig: dict) -> dict:
    """One trade from a signal at b (no slot logic). Returns a trade dict or a skip dict (key 'skip')."""
    side, b = sig['side'], sig['b']
    O, H, L, C = _arrays(df, side)
    Creal = np.asarray(df['c'], dtype=float)
    N, e = len(O), b + 1
    if e >= N:
        return dict(skip='no_next_bar', side=side, b=b)
    stop, E = sig['_stop'], fr(O[e])
    if E <= stop:
        return dict(skip='gap_stop', side=side, b=b)
    Ereal = abs(E)
    R = E - stop
    if R < COST_GUARD * Ereal:
        return dict(skip='cost_guard', side=side, b=b)
    target = E + TARGET_R * R
    hb = sig['_hb']
    pending, x, X, label, gap = None, None, None, None, False
    for k in range(e, N):
        ok = fr(O[k])
        if ok <= stop:                                               # protective gap first, owns the label
            x, X, label, gap = k, ok, 'stop', True
            break
        if ok >= target:
            x, X, label, gap = k, ok, 'target', True
            break
        if pending:
            x, X, label, gap = k, ok, pending, False
            break
        if fr(L[k]) <= stop:
            x, X, label = k, stop, 'stop'
            break
        if fr(H[k]) >= target:
            x, X, label = k, target, 'target'
            break
        if fr(C[k]) < hb:
            pending = 'invalidation'
        elif k >= b + CAP_BARS:
            pending = 'time'
    tr = dict(side=side, b=b, entry_i=e, entry=float(Ereal), stop=sig['stop'], target=float(_real(side, target)),
              R_unit=float(R))
    if x is None:
        tr.update(exit_i=None, exit=None, label='open_end', R_gross=None, R_net=None, lt_minus1=False)
        return tr
    gross = (X - E) / R                                             # transformed-space long R == real R for both sides
    Xreal = float(abs(X))
    d = 1 if side == 'long' else -1
    ef, xf = float(Ereal) * (1 + d * SLIP), Xreal * (1 - d * SLIP)
    slip = (float(Ereal) + Xreal) * SLIP
    fee = (ef + xf) * FEE
    fund = FUND_PER_BAR * float(np.sum(Creal[e:x + 1]))             # backtest.run parity: every managed bar e..x
    Ru = float(R)
    tr.update(exit_i=x, exit=Xreal, label=label, gap_exit=gap, entry_fill=ef, exit_fill=xf, R_gross=float(gross),
              slip_R=slip / Ru, fee_R=fee / Ru, fund_R=fund / Ru, R_net=float(gross) - (slip + fee + fund) / Ru,
              hold_bars=x - e, lt_minus1=float(gross) < -1)
    return tr


def run_symbol(df, sides=SIDES) -> dict:
    """Box consumption + one slot per symbol, both sides. Overlapping signals are ignored (not queued) and consume."""
    A = atr14(df)
    cands = sorted((g for sd in sides for g in detect(df, sd, atr=A)), key=lambda g: (g['b'], g['side']))
    by_b = {}
    for g in cands:
        by_b.setdefault(g['b'], []).append(g)
    signals, blocked, trades, skips = [], [], [], []
    next_ok, free_from = 0, 0                                       # signal iff b >= next_ok; taken iff b >= free_from
    for b in sorted(by_b):
        gs = by_b[b]
        if len(gs) != 1:                                            # frozen invariant: unreachable on valid bars
            raise AssertionError(f'same-bar long/short candidates at b={b}: impossible on valid bars')
        g = gs[0]
        if b < next_ok:
            blocked.append(dict(side=g['side'], b=b, consumed_by=next_ok - CONSUME))
            continue
        signals.append(g)
        next_ok = b + CONSUME
        if b < free_from:
            skips.append(dict(skip='overlap', side=g['side'], b=b))
            continue
        t = simulate_trade(df, g)
        if 'skip' in t:
            skips.append(t)
            continue
        trades.append(t)
        # exit filled on bar x (at its open or intrabar): flat at the close of x, so a signal at b >= x is taken
        free_from = float('inf') if t['exit_i'] is None else t['exit_i']
    for g in cands:
        g.pop('_stop', None), g.pop('_hb', None)
    return dict(candidate=CANDIDATE, label=DEV_LABEL, signals=signals, blocked=blocked, trades=trades, skips=skips)


def summarize(trades) -> dict:
    done = [t for t in trades if t['R_net'] is not None]
    out = {}
    for name, sel in (('total', done), ('long', [t for t in done if t['side'] == 'long']),
                      ('short', [t for t in done if t['side'] == 'short'])):
        w = sum(t['R_net'] for t in sel if t['R_net'] > 0)
        lo = -sum(t['R_net'] for t in sel if t['R_net'] < 0)
        out[name] = dict(n=len(sel), net_R=sum(t['R_net'] for t in sel), gross_R=sum(t['R_gross'] for t in sel),
                         fee_R=sum(t['fee_R'] for t in sel), slip_R=sum(t['slip_R'] for t in sel),
                         fund_R=sum(t['fund_R'] for t in sel), pf=(w / lo) if lo > 0 else None,
                         lt_minus1=sum(1 for t in sel if t['lt_minus1']),
                         lt_minus1_by_label={k: sum(1 for t in sel if t['lt_minus1'] and t['label'] == k)
                                             for k in ('stop', 'target', 'invalidation', 'time')})
    out['open_end'] = sum(1 for t in trades if t['R_net'] is None)
    return out
