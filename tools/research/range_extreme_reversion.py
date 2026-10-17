"""C4 `range_extreme_reversion.v0`: research-only signal detector + per-symbol simulator (synthetic fixtures first).

Frozen contract (later overrides earlier): Build draft #13 6232236186, Codex FREEZE 6232261614 (authoritative; it pins
the short mirror explicitly, the exact-Fraction threshold rule and the skip order).
DEVELOPMENT ONLY: this module reads no cache, store, archive or holdout and is registered nowhere (not
strategies.STRATEGIES, not engine slots / sleeves / presets, not the app). It runs on an in-memory frame (columns o, h, l,
c; optional v / t; optional `e50` / `atr` to inject the indicators in fixtures).

Indicators (the existing strategies implementation, pinned, as in C3):
  EMA50 = strategies.ema(close, 50) = pandas ewm(span=50, adjust=False), seeded with EMA[0] = close[0].
  ATR14 = strategies.indicators()['atr']: TR = max(H-L, |H-C[-1]|, |L-C[-1]|) (TR[0] = H[0]-L[0]), Wilder ewm
    alpha 1/14, adjust=False, seeded with TR[0].
Cost constants are imported from backtest (FEE taker 5 bps per fill, SLIP 2 bps per fill, FUND_PER_BAR 5e-5 per 4h
bar on the real closes of bars e..x inclusive, always a cost) - the accepted C2/C3 parity convention.

Rules (long; the short is implemented by running the long code on (-o, -l, -h, -c), which yields exactly the pinned
short rules below; ATR, the |EMA50 slope| and ER are mirror-invariant). Signal bar b, 4h closed bars.
  eligibility: b >= 220; EMA50[b-1], EMA50[b-13] finite; ATR14[b-1], ATR14[b] finite and > 0.
  box (bars b-30..b-1 inclusive; bar b excluded): RH = max high, RL = min low, W = RH - RL, M = (RH + RL) / 2.
  regime: 4.0 ATR[b-1] <= W <= 12.0 ATR[b-1]; |EMA50[b-1] - EMA50[b-13]| <= 0.5 ATR[b-1];
    ER = |C[b-1] - C[b-31]| / sum_{i=b-30..b-1} |C[i] - C[i-1]| <= 0.30 (zero denominator fails).
  long trigger at close b: RL - 1.50 ATR[b-1] <= low[b] <= RL - 0.10 ATR[b-1]; C[b] > RL (strict);
    H[b] > L[b] and (C[b] - L[b]) >= 0.60 (H[b] - L[b]); C[b] < M (strict).
  short trigger (pinned): RH + 0.10 ATR[b-1] <= high[b] <= RH + 1.50 ATR[b-1]; C[b] < RH; H[b] > L[b] and
    (C[b] - L[b]) <= 0.40 (H[b] - L[b]); C[b] > M.
  trade: entry E = open[b+1]; stop = low[b] - 0.25 ATR[b] (short high[b] + 0.25 ATR[b]); target = M (frozen).
    Skip order: E <= stop (gap_stop) -> R = E - stop > 2.5 ATR[b] (quality; equality passes) -> M - E < 1.0 R
    (room; equality passes; a gap entry at/beyond the mean fails here) -> R < 1.0% x E (real price) (cost_guard).
  exits (bar k >= b+1): at open[k], a gap through stop (O <= stop) or target (O >= M) fills at the open and owns the
    label, even over a pending close-rule exit; else a pending close-rule exit fills at open[k]; else intrabar stop
    (L <= stop) beats target (H >= M), both at their level; then at close[k]: C[k] < low[b] -> invalidation (strict;
    short C[k] > high[b]), k == b+24 -> time; invalidation wins; fills at open[k+1] (time: open b+25, 25 funding bars).
  cooldown / consumption (symbol level, both sides): a candidate passing every regime and trigger rule is a signal iff
    b >= previous signal b + 7; every signal consumes (taken, no-next-bar, gap/quality/room/cost-skipped,
    overlap-ignored); a failed gate consumes nothing and a cooldown-blocked candidate does not extend the cooldown.
  one trade per symbol: a signal at b is taken only when no trade is open at the close of b (a trade whose exit fills at
    open[b+1] still owns the slot; an open_end trade owns it for the rest of the dataset); otherwise it is ignored.
  same-b long/short conflict is unreachable (close location >= 0.60 and <= 0.40 on one H > L bar); asserted.
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

FAMILY = 'range_extreme_reversion'
CANDIDATE = 'range_extreme_reversion.v0'
DEV_LABEL = 'DEVELOPMENT-ONLY-SYNTHETIC'
TF = '4h'
WARM = 220                       # earliest b
BOX = 30                         # box b-30..b-1; ER closes b-31..b-1
SLOPE_LAG = 12                   # EMA50[b-1] - EMA50[b-13]
W_MIN_ATR = Fraction('4.0')
W_MAX_ATR = Fraction('12.0')
SLOPE_ATR = Fraction('0.5')
ER_MAX = Fraction('0.30')
PROBE_MIN_ATR = Fraction('0.10')
PROBE_MAX_ATR = Fraction('1.50')
CLOC_MIN = Fraction('0.60')      # long; the pinned short bound <= 0.40 is 1 - CLOC_MIN in the mirrored space
STOP_ATR = Fraction('0.25')
RCAP_ATR = Fraction('2.5')
ROOM_R = Fraction('1.0')
COST_GUARD = Fraction('0.01')
CAP_BARS = 24                    # time cap decided at close of b+24, exit at open of b+25
COOLDOWN = 7                     # next signal (either side) needs b' >= b + 7
FEE, SLIP, FUND_PER_BAR = BT.FEE, BT.SLIP, BT.FUND_PER_BAR
SIDES = ('long', 'short')


def fr(x) -> Fraction:
    return Fraction(float(x))


def atr14(df) -> np.ndarray:
    """ATR14 exactly as strategies.indicators (a frame that already carries `atr` keeps it: fixture injection)."""
    d = df if 'atr' in df else df.assign(v=df['v'] if 'v' in df else 0.0)
    return np.asarray(ST.indicators(d)['atr'], dtype=float)


def indicators(df):
    """(EMA50, ATR14) as float arrays in real prices; injected `e50` / `atr` columns win."""
    c = df['c'].astype(float)
    e50 = df['e50'] if 'e50' in df else ST.ema(c, 50)
    return np.asarray(e50, dtype=float), atr14(df)


def _arrays(df, side):
    o, h, l, c = (np.asarray(df[k], dtype=float) for k in ('o', 'h', 'l', 'c'))
    if not all(bool(np.all(x > 0)) for x in (o, h, l, c)):
        raise ValueError('prices must be > 0 (real-price abs() shortcuts assume it)')
    if side == 'long':
        return o, h, l, c
    if side == 'short':
        return -o, -l, -h, -c
    raise ValueError(f'side must be long|short, not {side!r}')


def _real(side, x):
    return -x if side == 'short' else x


def _eligible(E50, A, b):
    if b < WARM or b >= len(A):
        return False
    if not (np.isfinite(E50[b - 1]) and np.isfinite(E50[b - 1 - SLOPE_LAG])):
        return False
    return all(np.isfinite(A[i]) and A[i] > 0 for i in (b - 1, b))


def detect(df, side: str, ind=None) -> list[dict]:
    """Raw candidates for one side (every regime and trigger rule passed). A candidate at b reads bars 0..b only.
    Cooldown consumption and the slot are applied by run_symbol."""
    O, H, L, C = _arrays(df, side)
    E50, A = indicators(df) if ind is None else ind
    out = []
    for b in range(WARM, len(C)):
        if not _eligible(E50, A, b):
            continue
        a1, ab = fr(A[b - 1]), fr(A[b])
        rh, rl = fr(H[b - BOX:b].max()), fr(L[b - BOX:b].min())
        W, M = rh - rl, (rh + rl) / 2
        cb, hh, ll = fr(C[b]), fr(H[b]), fr(L[b])
        # trigger (evaluated first only for speed: every rule must pass, order does not change the outcome)
        if not ll <= rl - PROBE_MIN_ATR * a1:
            continue
        if not ll >= rl - PROBE_MAX_ATR * a1:
            continue
        if not cb > rl:
            continue
        if not (hh > ll and cb - ll >= CLOC_MIN * (hh - ll)):
            continue
        if not cb < M:
            continue
        # regime
        if not (W_MIN_ATR * a1 <= W <= W_MAX_ATR * a1):
            continue
        if not abs(fr(E50[b - 1]) - fr(E50[b - 1 - SLOPE_LAG])) <= SLOPE_ATR * a1:
            continue
        num = abs(fr(C[b - 1]) - fr(C[b - 1 - BOX]))
        den = sum(abs(fr(C[i]) - fr(C[i - 1])) for i in range(b - BOX, b))
        if den == 0 or not num <= ER_MAX * den:
            continue
        stop = ll - STOP_ATR * ab
        out.append(dict(side=side, b=b, box_hi=float(_real(side, rl if side == 'short' else rh)),
                        box_lo=float(_real(side, rh if side == 'short' else rl)), mean=float(_real(side, M)),
                        extreme=float(_real(side, ll)), stop=float(_real(side, stop)), atr_prev=float(A[b - 1]),
                        atr_b=float(A[b]), _stop=stop, _M=M, _ext=ll, _ab=ab))
    return out


def simulate_trade(df, sig: dict, ind=None) -> dict:
    """One trade from a signal at b (no slot logic). Returns a trade dict or a skip dict (key 'skip')."""
    side, b = sig['side'], sig['b']
    O, H, L, C = _arrays(df, side)
    Creal = np.asarray(df['c'], dtype=float)
    N, e = len(O), b + 1
    if e >= N:
        return dict(skip='no_next_bar', side=side, b=b)
    stop, target, ext, E = sig['_stop'], sig['_M'], sig['_ext'], fr(O[e])
    if E <= stop:
        return dict(skip='gap_stop', side=side, b=b)
    Ereal = abs(E)
    R = E - stop
    if R > RCAP_ATR * sig['_ab']:
        return dict(skip='quality', side=side, b=b)
    if target - E < ROOM_R * R:
        return dict(skip='room', side=side, b=b)
    if R < COST_GUARD * Ereal:
        return dict(skip='cost_guard', side=side, b=b)
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
        if fr(C[k]) < ext:
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
    """Cooldown consumption + one slot per symbol, both sides. Overlapping signals are ignored (not queued) and consume."""
    ind = indicators(df)
    cands = sorted((g for sd in sides for g in detect(df, sd, ind=ind)), key=lambda g: (g['b'], g['side']))
    by_b = {}
    for g in cands:
        by_b.setdefault(g['b'], []).append(g)
    signals, blocked, trades, skips = [], [], [], []
    last_b, free_from = None, 0                                     # signal iff b >= last_b + 7; taken iff b >= free_from
    for b in sorted(by_b):
        gs = by_b[b]
        if len(gs) != 1:                                            # frozen invariant: unreachable on valid bars
            raise AssertionError(f'same-bar long/short candidates at b={b}: impossible (close location)')
        g = gs[0]
        if last_b is not None and b < last_b + COOLDOWN:
            blocked.append(dict(side=g['side'], b=b, consumed_by=last_b))
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
        for k in ('_stop', '_M', '_ext', '_ab'):
            g.pop(k, None)
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
