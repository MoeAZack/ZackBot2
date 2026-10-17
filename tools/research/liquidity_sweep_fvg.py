"""C1 `liquidity_sweep_fvg.v0`: research-only signal detector + per-symbol simulator (synthetic fixtures first).

Frozen contract (later overrides earlier): Build draft #13 6228630862, Codex ruling 6228657686 (fixes 1-4), Cowork
6228664249 (a-e), Build adoption 6228666901. DEVELOPMENT ONLY: this module reads no cache, store, archive or holdout and is
registered nowhere (not strategies.STRATEGIES, not engine SCALP_SLOTS / sleeves / presets, not the app). It runs on an
in-memory frame (columns o, h, l, c; optional v / t; optional `atr` to inject ATR14 in fixtures).

Why a local simulator and not backtest.run: backtest.run fills signal / time exits at the CLOSE of the deciding bar (the
contract fills close-based invalidation and the time cap at the NEXT open), sizes the stop as stop_atr x ATR from the fill
(the contract stop is structural: highest high s..n + 0.10 ATR14[n]), has no per-trade stop / target prices, and has no
gap-at-entry skip or entry-based cost guard. Its existing keys are untouched; the cost constants are imported from it
(backtest.FEE taker 0.0005, backtest.SLIP 2 bps per fill, backtest.FUND_PER_BAR 5e-5 per 4h bar = 3.125e-6 per 15m x 16),
and ATR14 is strategies.indicators()['atr'] (Wilder TR-EWM, alpha 1/14), the same function the runner's book uses.

Rules (short; long is the exact price mirror, implemented by running the short code on (-o, -l, -h, -c)):
  pivot high j: H[j] > H[j+-k], k = 1..3 (strict); known only from the close of j+3; eligible at sweep bar s when
    s-60 <= j <= s-4 and not breached (breach = any closed bar k >= j+4, k < s, with H[k] > H[j], strict); the latest
    eligible pivot by (confirmation time, index) is P. A breached pivot is never reused (breach is a pure function of
    the bars, so a swept / failed / overlap-ignored setup consumes its pivot automatically).
  sweep at s: H[s] > P, H[s] - P >= 0.10 ATR14[s], C[s] < P.
  FVG at n, s+1 <= n <= s+3: L[n-2] > H[n]; bar n-1 bearish (C < O) with O-C >= 0.8 ATR14[n-1]; no close > P in s+1..n.
  entry: market at open[n+1] = E. stop = max H[s..n] + 0.10 ATR14[n]. Skips: E >= stop (gap_stop); E <= Tref where
    Tref = C[n] - 2 (stop - C[n]) (gap_target); stop - E < 0.84% E (cost_guard). R = stop - E; target = E - 2R.
  exits (bar k >= n+1): pending close-rule exit fills at open[k]; else (k > n+1) open through stop / target fills at
    open[k]; else intrabar stop (H >= stop) beats target (L <= target), both at their level; then at close[k]:
    C[k] > L[n-2] -> invalidation, k == n+12 -> time; invalidation label wins; fills at open[k+1].
  one trade per symbol (both sides share the slot): a signal at n is taken only when no trade is open at the close of n
    (a trade whose close-rule exit fills at open[n+1] is still open); otherwise it is ignored (not queued).
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

FAMILY = 'liquidity_sweep_fvg'
CANDIDATE = 'liquidity_sweep_fvg.v0'
DEV_LABEL = 'DEVELOPMENT-ONLY-SYNTHETIC'
TF = '4h'
PIVOT_K = 3                      # bars each side; confirmation lag = PIVOT_K
PIVOT_LOOKBACK = 60              # s-60 <= j
WICK_ATR = Fraction('0.10')
BODY_ATR = Fraction('0.8')
STOP_ATR = Fraction('0.10')
COST_GUARD = Fraction('0.0084')
TARGET_R = 2
FVG_MAX = 3                      # n <= s+3
CAP_BARS = 12                    # time cap decided at close of n+12, exit at open of n+13
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
    if side == 'short':
        return o, h, l, c
    if side == 'long':
        return -o, -l, -h, -c
    raise ValueError(f'side must be long|short, not {side!r}')


def _is_pivot(H, j) -> bool:
    if j - PIVOT_K < 0 or j + PIVOT_K >= len(H):
        return False
    return all(H[j] > H[j - k] and H[j] > H[j + k] for k in range(1, PIVOT_K + 1))


def _pivot_for(H, s):
    """Latest eligible unbreached pivot for a sweep at s: uses bars < s only (j + 3 <= s - 1)."""
    for j in range(s - PIVOT_K - 1, max(PIVOT_K, s - PIVOT_LOOKBACK) - 1, -1):
        if not _is_pivot(H, j):
            continue
        if any(H[k] > H[j] for k in range(j + PIVOT_K + 1, s)):
            continue                                                # breached after confirmation: never reused
        return j
    return None


def detect(df, side: str, atr=None) -> list[dict]:
    """Signals for one side. A signal at n reads bars 0..n only. One signal per n (the earliest sweep s wins)."""
    O, H, L, C = _arrays(df, side)
    A = atr14(df) if atr is None else np.asarray(atr, dtype=float)
    N, out, seen = len(H), [], set()
    for s in range(N):
        if not np.isfinite(A[s]):
            continue
        j = _pivot_for(H, s)
        if j is None:
            continue
        P = H[j]
        if not (H[s] > P and fr(H[s]) - fr(P) >= WICK_ATR * fr(A[s]) and C[s] < P):
            continue
        for n in range(s + 1, min(s + FVG_MAX + 1, N)):
            if C[n] > P:
                break                                               # closed back beyond P before an FVG: setup dead
            b = n - 1
            if n - 2 < 0 or not np.isfinite(A[b]) or not np.isfinite(A[n]):
                continue
            if L[n - 2] > H[n] and C[b] < O[b] and fr(O[b]) - fr(C[b]) >= BODY_ATR * fr(A[b]):
                if n not in seen:
                    seen.add(n)
                    stop = fr(max(H[s:n + 1])) + STOP_ATR * fr(A[n])
                    out.append(dict(side=side, s=s, n=n, j=j, P=float(-P if side == 'long' else P),
                                    fvg_edge=float(-L[n - 2] if side == 'long' else L[n - 2]),
                                    stop=float(-stop if side == 'long' else stop), _stop=stop, _edge=L[n - 2],
                                    atr_s=float(A[s]), atr_n=float(A[n])))
                break
    return out


def _real(side, x):
    return -x if side == 'long' else x


def simulate_trade(df, sig: dict) -> dict:
    """One trade from a signal at n (no slot logic). Returns a trade dict or a skip dict (key 'skip')."""
    side, n = sig['side'], sig['n']
    O, H, L, C = _arrays(df, side)
    Creal = np.asarray(df['c'], dtype=float)
    N, e = len(O), n + 1
    if e >= N:
        return dict(skip='no_next_bar', side=side, n=n)
    stop, E = sig['_stop'], fr(O[e])
    if E >= stop:
        return dict(skip='gap_stop', side=side, n=n)
    Cn = fr(C[n])
    if E <= Cn - TARGET_R * (stop - Cn):
        return dict(skip='gap_target', side=side, n=n)
    Ereal = abs(E)
    R = stop - E
    if R < COST_GUARD * Ereal:
        return dict(skip='cost_guard', side=side, n=n)
    target = E - TARGET_R * R
    edge = sig['_edge']
    pending, x, X, label, gap = None, None, None, None, False
    for k in range(e, N):
        if pending:
            x, X, label, gap = k, fr(O[k]), pending, False
            break
        if k > e and fr(O[k]) >= stop:
            x, X, label, gap = k, fr(O[k]), 'stop', True
            break
        if k > e and fr(O[k]) <= target:
            x, X, label, gap = k, fr(O[k]), 'target', True
            break
        if fr(H[k]) >= stop:
            x, X, label = k, stop, 'stop'
            break
        if fr(L[k]) <= target:
            x, X, label = k, target, 'target'
            break
        if C[k] > edge:
            pending = 'invalidation'
        elif k >= n + CAP_BARS:
            pending = 'time'
    tr = dict(side=side, s=sig['s'], n=n, j=sig['j'], entry_i=e, entry=float(Ereal), stop=sig['stop'],
              target=float(_real(side, target)), R_unit=float(R))
    if x is None:
        tr.update(exit_i=None, exit=None, label='open_end', R_gross=None, R_net=None, lt_minus1=False)
        return tr
    gross = (E - X) / R                                             # transformed-space short R == real R for both sides
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
    """Both sides share one slot per symbol. Overlapping setups are ignored (not queued); their pivots stay consumed."""
    A = atr14(df)
    sigs = sorted((g for sd in sides for g in detect(df, sd, atr=A)), key=lambda g: (g['n'], g['side']))
    by_n = {}
    for g in sigs:
        by_n.setdefault(g['n'], []).append(g)
    trades, skips, free_from = [], [], 0                            # a signal at n is taken iff n >= free_from
    for n in sorted(by_n):
        gs = by_n[n]
        if n < free_from:
            skips += [dict(skip='overlap', side=g['side'], n=n) for g in gs]
            continue
        if len(gs) > 1:
            skips += [dict(skip='conflict', side=g['side'], n=n) for g in gs]
            continue
        t = simulate_trade(df, gs[0])
        if 'skip' in t:
            skips.append(t)
            continue
        trades.append(t)
        # exit filled on bar x (at its open or intrabar): flat at the close of x, so a signal at n >= x is taken
        free_from = float('inf') if t['exit_i'] is None else t['exit_i']
    for g in sigs:
        g.pop('_stop', None), g.pop('_edge', None)
    return dict(candidate=CANDIDATE, label=DEV_LABEL, signals=sigs, trades=trades, skips=skips)


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
                         lt_minus1=sum(1 for t in sel if t['lt_minus1']))
    out['open_end'] = sum(1 for t in trades if t['R_net'] is None)
    return out
