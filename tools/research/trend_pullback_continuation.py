"""C3 `trend_pullback_continuation.v0`: research-only signal detector + per-symbol simulator (synthetic fixtures first).

Frozen contract (later overrides earlier): Build draft #13 6230936151, Cowork fixture plan 6230959827, Codex FREEZE
6230952961 (authoritative; where Cowork differs, Codex wins: an overlap-ignored signal DOES consume its setup).
DEVELOPMENT ONLY: this module reads no cache, store, archive or holdout and is registered nowhere (not
strategies.STRATEGIES, not engine slots / sleeves / presets, not the app). It runs on an in-memory frame (columns o, h, l,
c; optional v / t; optional `e20` / `e50` / `atr` to inject the indicators in fixtures).

Indicators (the existing strategies implementation, pinned):
  EMA20 / EMA50 = strategies.ema(close, n) = pandas ewm(span=n, adjust=False): alpha = 2/(n+1), recursive,
    seeded with EMA[0] = close[0] (no SMA seed, no bias adjustment).
  ATR14 = strategies.indicators()['atr']: TR = max(H-L, |H-C[-1]|, |L-C[-1]|) (TR[0] = H[0]-L[0]), Wilder ewm
    alpha 1/14, adjust=False, seeded with TR[0].
  The 220-bar warm-up makes both seeds immaterial in practice; the rule is stated so a re-implementation matches.
Cost constants are imported from backtest (FEE taker 5 bps per fill, SLIP 2 bps per fill, FUND_PER_BAR 5e-5 per 4h
bar on the real closes of bars e..x inclusive, always a cost) - the accepted C2 parity convention.

Rules (long; short is the exact price mirror, implemented by running the long code on (-o, -l, -h, -c) with
-EMA20 / -EMA50; ATR is mirror-invariant). Signal bar b, 4h closed bars.
  eligibility: b >= 220; EMA20[b-11..b], EMA50[b-12..b-1] finite (every EMA a gate reads); ATR14[b-1], ATR14[b] finite and > 0.
  trend (at b-1): EMA20[b-1] > EMA50[b-1]; EMA50[b-1] - EMA50[b-7] >= 0.5 ATR[b-1];
    C[j] > EMA50[j] (per bar, strict) on at least 10 of j = b-12..b-1.
  swing: H = max(high[b-12..b-1]); h_idx = the LATEST bar holding that max (ties -> latest).
  pullback bars P = h_idx+1..b-1, len(P) >= 2:
    depth  H - min(low[P]) >= 1.0 ATR[b-1];
    touch  some j in P: low[j] <= EMA20[j] + 0.25 ATR[b-1]  (per-bar EMA, single ATR at b-1);
    structure  no j in P with C[j] < EMA50[j] (per bar; equality holds) and min(low[P]) >= EMA50[b-1] - 0.5 ATR[b-1].
    The trigger bar's low is deliberately not checked against the floor (the R cap bites instead).
  trigger at close b: C[b] > high[b-1] (strict); C[b] > EMA20[b]; C[b] - O[b] >= 0.3 ATR[b-1];
    H[b] > L[b] and C[b] - L[b] >= 0.60 (H[b] - L[b]); C[b] < H (strict: before the breakout, distinct from C2).
  L = min(low[h_idx+1..b]) (includes the trigger bar).  stop = L - 0.2 ATR[b].
  entry = open[b+1] = E.  Skips: E <= stop (gap_stop); R = E - stop > 3.0 ATR[b] (quality; equality passes);
    R < 1.0% x E (real price) (cost_guard; equality passes).  target = E + 2.5R (rebased from the actual entry).
  exits (bar k >= b+1): at open[k], a gap through stop (O <= stop) or target (O >= target) fills at the open and owns
    the label, even over a pending close-rule exit; else a pending close-rule exit fills at open[k]; else intrabar stop
    (L <= stop) beats target (H >= target), both at their level; then at close[k]: C[k] < EMA50[k] -> invalidation
    (equality does not invalidate), k == b+30 -> time; invalidation label wins; fills at open[k+1] (time: open b+31).
  setup consumption (symbol level, both sides): a candidate that passes every gate is a signal iff its swing index
    h_idx > the previous signal's b (long: swing-high index, short: swing-low index); every signal consumes (taken,
    no-next-bar, gap-skipped, quality/cost-skipped, overlap-ignored); a blocked candidate or a failed gate consumes
    nothing (a blocked candidate does not move the marker).
  one trade per symbol: a signal at b is taken only when no trade is open at the close of b (a trade whose exit fills at
    open[b+1] still owns the slot; an open_end trade owns it for the rest of the dataset); otherwise it is ignored.
  same-b long/short conflict is unreachable (EMA20[b-1] > EMA50[b-1] and < at once); it is asserted, not handled.
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

FAMILY = 'trend_pullback_continuation'
CANDIDATE = 'trend_pullback_continuation.v0'
DEV_LABEL = 'DEVELOPMENT-ONLY-SYNTHETIC'
TF = '4h'
WARM = 220                       # earliest b
SWING = 12                       # swing window b-12..b-1 (also the trend-persistence window)
SLOPE_LAG = 6                    # EMA50[b-1] - EMA50[b-7]
PERSIST_MIN = 10                 # C > EMA50 on >= 10 of 12
PB_MIN = 2                       # pullback bars h_idx+1..b-1
SLOPE_ATR = Fraction('0.5')
DEPTH_ATR = Fraction('1.0')
TOUCH_ATR = Fraction('0.25')
FLOOR_ATR = Fraction('0.5')
BODY_ATR = Fraction('0.3')
CLOC_MIN = Fraction('0.60')
STOP_ATR = Fraction('0.2')
RCAP_ATR = Fraction('3.0')
COST_GUARD = Fraction('0.01')
TARGET_R = Fraction('2.5')
CAP_BARS = 30                    # time cap decided at close of b+30, exit at open of b+31
FEE, SLIP, FUND_PER_BAR = BT.FEE, BT.SLIP, BT.FUND_PER_BAR
SIDES = ('long', 'short')


def fr(x) -> Fraction:
    return Fraction(float(x))


def atr14(df) -> np.ndarray:
    """ATR14 exactly as strategies.indicators (a frame that already carries `atr` keeps it: fixture injection)."""
    d = df if 'atr' in df else df.assign(v=df['v'] if 'v' in df else 0.0)
    return np.asarray(ST.indicators(d)['atr'], dtype=float)


def indicators(df):
    """(EMA20, EMA50, ATR14) as float arrays in real prices; injected `e20` / `e50` / `atr` columns win."""
    c = df['c'].astype(float)
    e20 = df['e20'] if 'e20' in df else ST.ema(c, 20)
    e50 = df['e50'] if 'e50' in df else ST.ema(c, 50)
    return np.asarray(e20, dtype=float), np.asarray(e50, dtype=float), atr14(df)


def _arrays(df, side):
    o, h, l, c = (np.asarray(df[k], dtype=float) for k in ('o', 'h', 'l', 'c'))
    if not all(bool(np.all(x > 0)) for x in (o, h, l, c)):
        raise ValueError('prices must be > 0 (real-price abs() shortcuts assume it)')
    if side == 'long':
        return o, h, l, c
    if side == 'short':
        return -o, -l, -h, -c
    raise ValueError(f'side must be long|short, not {side!r}')


def _side_ind(ind, side):
    e20, e50, A = ind
    return (e20, e50, A) if side == 'long' else (-e20, -e50, A)


def _real(side, x):
    return -x if side == 'short' else x


def _eligible(E20, E50, A, b):
    if b < WARM or b >= len(A):
        return False
    if not (np.all(np.isfinite(E20[b - SWING + 1:b + 1])) and np.all(np.isfinite(E50[b - SWING:b]))):
        return False
    return all(np.isfinite(A[i]) and A[i] > 0 for i in (b - 1, b))


def detect(df, side: str, ind=None) -> list[dict]:
    """Raw candidates for one side (every gate passed). A candidate at b reads bars 0..b only.
    Setup consumption and the slot are applied by run_symbol."""
    O, H, L, C = _arrays(df, side)
    E20, E50, A = _side_ind(indicators(df) if ind is None else ind, side)
    out = []
    for b in range(WARM, len(C)):
        if not _eligible(E20, E50, A, b):
            continue
        a1, ab = fr(A[b - 1]), fr(A[b])
        if not fr(E20[b - 1]) > fr(E50[b - 1]):
            continue
        if not fr(E50[b - 1]) - fr(E50[b - 1 - SLOPE_LAG]) >= SLOPE_ATR * a1:
            continue
        if sum(1 for j in range(b - SWING, b) if fr(C[j]) > fr(E50[j])) < PERSIST_MIN:
            continue
        win = H[b - SWING:b]
        hs = win.max()
        h_idx = b - SWING + int(np.flatnonzero(win == hs)[-1])                  # latest tied max
        pb = range(h_idx + 1, b)
        if len(pb) < PB_MIN:
            continue
        Hs = fr(hs)
        mlow = min(fr(L[j]) for j in pb)
        if not Hs - mlow >= DEPTH_ATR * a1:
            continue
        if not any(fr(L[j]) <= fr(E20[j]) + TOUCH_ATR * a1 for j in pb):
            continue
        if any(fr(C[j]) < fr(E50[j]) for j in pb):
            continue
        if not mlow >= fr(E50[b - 1]) - FLOOR_ATR * a1:
            continue
        cb, ob, hh, ll = fr(C[b]), fr(O[b]), fr(H[b]), fr(L[b])
        if not cb > fr(H[b - 1]):
            continue
        if not cb > fr(E20[b]):
            continue
        if not cb - ob >= BODY_ATR * a1:
            continue
        if not (hh > ll and cb - ll >= CLOC_MIN * (hh - ll)):
            continue
        if not cb < Hs:
            continue
        Lp = min(mlow, ll)
        stop = Lp - STOP_ATR * ab
        out.append(dict(side=side, b=b, swing_i=h_idx, swing=float(_real(side, Hs)), pb_extreme=float(_real(side, Lp)),
                        stop=float(_real(side, stop)), atr_prev=float(A[b - 1]), atr_b=float(A[b]),
                        _stop=stop, _ab=ab))
    return out


def simulate_trade(df, sig: dict, ind=None) -> dict:
    """One trade from a signal at b (no slot logic). Returns a trade dict or a skip dict (key 'skip')."""
    side, b = sig['side'], sig['b']
    O, H, L, C = _arrays(df, side)
    _, E50, _ = _side_ind(indicators(df) if ind is None else ind, side)
    Creal = np.asarray(df['c'], dtype=float)
    N, e = len(O), b + 1
    if e >= N:
        return dict(skip='no_next_bar', side=side, b=b)
    stop, E = sig['_stop'], fr(O[e])
    if E <= stop:
        return dict(skip='gap_stop', side=side, b=b)
    Ereal = abs(E)
    R = E - stop
    if R > RCAP_ATR * sig['_ab']:
        return dict(skip='quality', side=side, b=b)
    if R < COST_GUARD * Ereal:
        return dict(skip='cost_guard', side=side, b=b)
    target = E + TARGET_R * R
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
        if not np.isfinite(E50[k]):
            raise ValueError(f'EMA50 not finite at managed bar {k}')
        if fr(C[k]) < fr(E50[k]):
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
    """Setup consumption + one slot per symbol, both sides. Overlapping signals are ignored (not queued) and consume."""
    ind = indicators(df)
    cands = sorted((g for sd in sides for g in detect(df, sd, ind=ind)), key=lambda g: (g['b'], g['side']))
    by_b = {}
    for g in cands:
        by_b.setdefault(g['b'], []).append(g)
    signals, blocked, trades, skips = [], [], [], []
    last_b, free_from = -1, 0                                       # signal iff swing_i > last_b; taken iff b >= free_from
    for b in sorted(by_b):
        gs = by_b[b]
        if len(gs) != 1:                                            # frozen invariant: unreachable on valid bars
            raise AssertionError(f'same-bar long/short candidates at b={b}: impossible under the trend filters')
        g = gs[0]
        if g['swing_i'] <= last_b:
            blocked.append(dict(side=g['side'], b=b, swing_i=g['swing_i'], consumed_by=last_b))
            continue
        signals.append(g)
        last_b = b
        if b < free_from:
            skips.append(dict(skip='overlap', side=g['side'], b=b))
            continue
        t = simulate_trade(df, g, ind=ind)
        if 'skip' in t:
            skips.append(t)
            continue
        trades.append(t)
        # exit filled on bar x (at its open or intrabar): flat at the close of x, so a signal at b >= x is taken
        free_from = float('inf') if t['exit_i'] is None else t['exit_i']
    for g in cands:
        g.pop('_stop', None), g.pop('_ab', None)
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
