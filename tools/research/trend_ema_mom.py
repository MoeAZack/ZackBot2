"""`trend_ema_mom.v1` as a RES-01 evaluator: a pure function of the frozen `pit.View` (RES-01 R4 prep; plan section 7).

The rule is the NEWCORE `trend_ema_mom.v1` (origin commit 45c22df, newcore/strategy/ema_mom.py), re-expressed over the
R3 capability boundary so the research path never imports the NEWCORE runner. At the close of the last closed 4h bar:
  le = cross_up(EMA20, EMA50) and c > EMA200 and ret7d > 0 and ret30d > 0          (long entry, next bar open)
  lx = EMA20 < EMA50 or not (ret7d > 0 and ret30d > 0)                            (long exit,  next bar open)
  stop distance = stop_atr (2.5) x Wilder ATR14 of the signal bar, from the FILL price (Decimal, exact float repr).
Long only (the short mirror is a mechanics test, never an edge claim). No signal before index max(warmup, ret30d bars)
of the bars read; the bars read are the last `window` closed bars (1500, the live kline cap) available at `view.t`.
EMA / ATR / returns / cross are bit-identical to newcore/strategy/indicators.py (same float recursions, same order).

`decide(view, ...)` reads ONLY through the view (`view.bars`, `view.members`), keeps no state and returns plain tuples,
so `pit.evaluate` can re-run it under future perturbation and compare. Positions are capabilities the harness issued
with `View.enter`; they are passed in (`held`) only so a held symbol that left the universe can still be read.
Stdlib only.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from decimal import Context, Decimal, ROUND_HALF_EVEN

RULE_ID = 'trend_ema_mom.v1'
DAY_MS = 86_400_000
TF = '4h'
TF_MS = 14_400_000
WINDOW = 1500                                   # live kline read cap = the M3 runner's signal window
TIME_CAP_BARS = 180                             # cap180 primary: a lot exits at the open of bar entry_index + 180
                                                # (never 181); equals the split horizon_bars / purge horizon
ENTER, CLOSE = 'enter', 'close'
_DEC = Context(prec=34, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class Params:
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    atr_len: int = 14
    stop_atr: str = '2.5'                       # Decimal text (exact)
    mom_short_days: int = 7
    mom_long_days: int = 30
    warmup_bars: int = 220

    def __post_init__(self):
        if not (0 < self.ema_fast < self.ema_slow < self.ema_trend):
            raise ValueError('need 0 < ema_fast < ema_slow < ema_trend')
        d = Decimal(self.stop_atr)
        if not d.is_finite() or d <= 0:
            raise ValueError('stop_atr must be a positive finite decimal')
        for k in ('atr_len', 'mom_short_days', 'mom_long_days', 'warmup_bars'):
            if type(getattr(self, k)) is not int or getattr(self, k) < 1:
                raise ValueError(f'{k} must be an int >= 1')

    def bars_of(self, days: int) -> int:
        if DAY_MS % TF_MS:
            raise ValueError('timeframe must divide a day')
        return days * DAY_MS // TF_MS

    def start(self) -> int:
        """First index (within the bars read) at which a signal may be evaluated."""
        return max(self.warmup_bars, self.bars_of(self.mom_short_days), self.bars_of(self.mom_long_days), 1)

    def doc(self) -> dict:
        return asdict(self)


PRIMARY = Params()
# One-at-a-time +/- one step around the primary (plan section 4: neighbour grid). Selection never uses them; they feed
# the EDGE-00 80% neighbour-stability row and every one is a ledger grid_point.
STEPS = {'ema_fast': 5, 'ema_slow': 10, 'ema_trend': 50, 'stop_atr': '0.5', 'mom_short_days': 2, 'mom_long_days': 10}


def neighbours(p: Params = PRIMARY) -> list[tuple[str, Params]]:
    out = []
    for k, step in STEPS.items():
        for sgn in (-1, 1):
            v = getattr(p, k)
            nv = str(Decimal(v) + sgn * Decimal(step)) if k == 'stop_atr' else v + sgn * step
            out.append((f'{k}{"+" if sgn > 0 else "-"}{step}', replace(p, **{k: nv})))
    return out


def max_lookback_bars(configs) -> int:
    return max(p.start() for p in configs)


# ------------------------------------------------------------------ indicators (parity with newcore indicators.py)
def ema(x, n):
    a, y, out = 2.0 / (n + 1.0), None, []
    for v in x:
        y = v if y is None else (1.0 - a) * y + a * v
        out.append(y)
    return out


def wilder_atr(h, l, c, n):
    a, y, out = 1.0 / n, None, []
    for i in range(len(c)):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        y = tr if y is None else (1.0 - a) * y + a * tr
        out.append(y)
    return out


def pct_return(c, k, i):
    return None if i < k else c[i] / c[i - k] - 1.0


def cross_up(a, b, i):
    return i >= 1 and a[i] > b[i] and a[i - 1] <= b[i - 1]


def contiguous_tail(bars, tf_ms: int = TF_MS):
    """The longest run of consecutive bars ending at the last one (a gap restarts the run; never forward-filled)."""
    j = len(bars) - 1
    while j > 0 and bars[j].open_ms - bars[j - 1].open_ms == tf_ms:
        j -= 1
    return bars[j:]


def signal_at_last(bars, p: Params):
    """(le, lx, atr) at the last bar of `bars`, or None before warm-up. `bars` are closed and contiguous."""
    n = len(bars)
    i = n - 1
    if i < p.start():
        return None
    c = [b.close for b in bars]
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    ef, es, et = ema(c, p.ema_fast), ema(c, p.ema_slow), ema(c, p.ema_trend)
    atr = wilder_atr(h, lo, c, p.atr_len)
    rs, rl = pct_return(c, p.bars_of(p.mom_short_days), i), pct_return(c, p.bars_of(p.mom_long_days), i)
    mom = rs is not None and rl is not None and rs > 0 and rl > 0
    le = cross_up(ef, es, i) and c[i] > et[i] and mom
    lx = ef[i] < es[i] or not mom
    return le, lx, atr[i]


def stop_distance(atr: float, p: Params) -> Decimal:
    if not math.isfinite(atr):
        raise ValueError('non-finite ATR')
    return _DEC.multiply(Decimal(p.stop_atr), Decimal(repr(float(atr))))


def decide(view, *, params: Params, symbols, held=(), window: int = WINDOW) -> tuple:
    """Decisions at `view.t`, in the configured symbol order: ('close', symbol, close_ms) for lx and
    ('enter', symbol, close_ms, stop_distance text, signal close repr) for le. `held` = ((symbol, Position), ...)."""
    pos = dict(held)
    out = []
    members = view.members()
    for s in symbols:
        cap = pos.get(s)
        if cap is None and s not in members:
            continue
        bars = view.bars(s, TF, window, position=cap)
        if not bars or bars[-1].available_ms != view.t:
            continue                                            # no bar closing at t: no decision (never stale)
        sig = signal_at_last(contiguous_tail(bars), params)
        if sig is None:
            continue
        le, lx, atr = sig
        if lx:
            out.append((CLOSE, s, view.t))
        if le:
            out.append((ENTER, s, view.t, str(stop_distance(atr, params)), repr(bars[-1].close)))
    return tuple(out)
