"""`trend_ema_mom` v1: EMA20/50 cross + EMA200 trend + 7d/30d momentum (slice plan section 2.2).

Pure and deterministic: no IO, no clock, no randomness, no ambient Decimal context. Input is CLOSED candles only, as
numeric sequences with integer UTC-ms open times; the caller states the decision time (`as_of_ms`) and a candle that has
not closed by then is refused (FormingCandle), never silently used. Output is typed signal decisions.

Rule (evaluated at the close of bar i; acted on at the open of bar i+1 by the runner, not here):
  long  entry  le = cross_up(EMA20, EMA50) and c > EMA200 and ret7d > 0 and ret30d > 0
  long  exit   lx = EMA20 < EMA50 or not (ret7d > 0 and ret30d > 0)
  short entry  se = cross_up(EMA50, EMA20) and c < EMA200 and ret7d < 0 and ret30d < 0      (MECHANICS TEST ONLY)
  short exit   sx = EMA20 > EMA50 or not (ret7d < 0 and ret30d < 0)
  stop         stop_atr (2.5) x ATR14[i] from the FILL price; R = that distance. Exit on lx / sx ("reverse signal" = the
               legacy exit, which includes the momentum filter turning off, not only the opposite EMA cross).
  ret7d / ret30d are defined in DAYS and converted to bars by the timeframe (4h: 42 / 180 = legacy; 1h: 168 / 720).

Differences from legacy strategies.sig_ema_mom (all intentional):
  1. Lookbacks in days, not bars (legacy hard-codes 42 / 180 bars, which is 7d / 30d only on 4h).
  2. No signal before the effective warmup max(warmup_bars, ret30d bars); legacy emits lx=True during warmup (NaN
     returns) and leaves warmup to backtest.run (220).
  3. The short side is computed always but only EMITTED when Params.enable_short; it is labelled mechanics_only.
  4. Gapped / misaligned / forming candles are refused (legacy aligns symbols on common timestamps instead).
  5. stop_distance is a Decimal: stop_atr (Decimal) x Decimal(repr(ATR float)), in an explicit context. Legacy computes
     the float 2.5 * atr; the two agree to float precision.
EMA, ATR (Wilder, alpha 1/14, TR0 = h0-l0) and the cross definition are identical to legacy (see indicators.py).
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from decimal import Context, Decimal, ROUND_HALF_EVEN
from typing import Optional, Sequence, Tuple

from ..domain.reasons import ReasonCode
from . import indicators as ind

RULE_ID = 'trend_ema_mom.v1'
DAY_MS = 86_400_000
# Reason codes from the NC-01 registry (newcore.domain.reasons, pure stdlib): members, never string literals, so an
# unregistered code cannot slip in (test_no_literal_reason_bypasses_the_registry). StrEnum: equal to their values.
REASON_ENTRY = ReasonCode.ENTRY_SIGNAL
REASON_EXIT = ReasonCode.EXIT_SIGNAL

_DEC = Context(prec=34, rounding=ROUND_HALF_EVEN)     # explicit: never the ambient decimal context


class Side(enum.StrEnum):            # values equal newcore.domain.orders.Side
    LONG = 'LONG'
    SHORT = 'SHORT'


class SignalAction(enum.StrEnum):    # values equal newcore.domain.decision.Action.ENTER / CLOSE
    ENTER = 'enter'
    CLOSE = 'close'


class FormingCandle(ValueError):
    """A candle in the input had not closed at the stated decision time."""


class BadBars(ValueError):
    """Input candles are malformed: lengths, types, ordering, gaps, alignment or OHLC sanity."""


def lookback_bars(days: int, tf_ms: int) -> int:
    """Whole-day lookback converted to bars. The timeframe must divide a day exactly (no rounding, ever)."""
    if type(days) is not int or days < 1:
        raise ValueError(f'lookback days must be a positive int, got {days!r}')
    if type(tf_ms) is not int or tf_ms <= 0 or DAY_MS % tf_ms:
        raise ValueError(f'timeframe {tf_ms!r} ms does not divide a day')
    return days * DAY_MS // tf_ms


@dataclass(frozen=True)
class Bars:
    """Closed candles of one symbol, oldest first. t_ms = candle OPEN time (UTC ms); a candle closes at t + tf_ms."""
    symbol: str
    tf_ms: int
    t_ms: Tuple[int, ...]
    o: Tuple[float, ...]
    h: Tuple[float, ...]
    l: Tuple[float, ...]
    c: Tuple[float, ...]

    def __post_init__(self):
        if type(self.tf_ms) is not int or self.tf_ms <= 0 or DAY_MS % self.tf_ms:
            raise BadBars(f'tf_ms must be a positive int that divides a day, got {self.tf_ms!r}')
        n = len(self.t_ms)
        for name in ('o', 'h', 'l', 'c'):
            if len(getattr(self, name)) != n:
                raise BadBars(f'{name} has {len(getattr(self, name))} values, t_ms has {n}')
        object.__setattr__(self, 't_ms', tuple(self.t_ms))
        for name in ('o', 'h', 'l', 'c'):
            vals = tuple(float(v) for v in getattr(self, name))
            if any(not math.isfinite(v) or v <= 0 for v in vals):
                raise BadBars(f'{name} has a non-finite or non-positive price')
            object.__setattr__(self, name, vals)
        for i, t in enumerate(self.t_ms):
            if type(t) is not int:
                raise BadBars(f't_ms[{i}] is {type(t).__name__}, not int')
            if t % self.tf_ms:
                raise BadBars(f't_ms[{i}]={t} is not aligned to the {self.tf_ms} ms timeframe')
            if i and t - self.t_ms[i - 1] != self.tf_ms:
                raise BadBars(f'gap or disorder at t_ms[{i}]: step {t - self.t_ms[i - 1]} ms != {self.tf_ms}')
            if not (self.l[i] <= min(self.o[i], self.c[i]) and self.h[i] >= max(self.o[i], self.c[i])):
                raise BadBars(f'bar {i} OHLC is inconsistent')

    def __len__(self):
        return len(self.t_ms)

    def close_ms(self, i: int) -> int:
        return self.t_ms[i] + self.tf_ms

    def head(self, n: int) -> 'Bars':
        """The first n candles (a truncation of the future)."""
        return Bars(self.symbol, self.tf_ms, self.t_ms[:n], self.o[:n], self.h[:n], self.l[:n], self.c[:n])


@dataclass(frozen=True)
class Params:
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    atr_len: int = 14
    stop_atr: Decimal = Decimal('2.5')
    mom_short_days: int = 7
    mom_long_days: int = 30
    warmup_bars: int = 220
    enable_short: bool = False       # the short mirror is a mechanics test, never an edge claim

    def __post_init__(self):
        if type(self.stop_atr) is not Decimal or not self.stop_atr.is_finite() or self.stop_atr <= 0:
            raise ValueError('stop_atr must be a positive finite Decimal')
        if not (0 < self.ema_fast < self.ema_slow):
            raise ValueError('need 0 < ema_fast < ema_slow')


@dataclass(frozen=True)
class Evaluation:
    """Every indicator and boolean signal for every bar. Signals are False before `start`."""
    bars: Bars
    params: Params
    k_short: int
    k_long: int
    start: int
    ema_fast: Tuple[float, ...]
    ema_slow: Tuple[float, ...]
    ema_trend: Tuple[float, ...]
    atr: Tuple[float, ...]
    ret_short: Tuple[Optional[float], ...]
    ret_long: Tuple[Optional[float], ...]
    le: Tuple[bool, ...]
    se: Tuple[bool, ...]
    lx: Tuple[bool, ...]
    sx: Tuple[bool, ...]


@dataclass(frozen=True)
class SignalDecision:
    rule: str
    symbol: str
    action: SignalAction
    side: Side
    bar_index: int
    bar_open_ms: int
    signal_time_ms: int              # the signal candle's close; the order goes at the next candle's open
    atr: Decimal                     # ATR of the signal candle (exact repr of the float)
    stop_distance: Optional[Decimal]  # ENTER only: stop_atr x atr, measured from the fill price
    reason: str
    mechanics_only: bool = field(default=False)   # True on every SHORT decision: no edge claim


def _check_closed(bars: Bars, as_of_ms: int) -> None:
    if type(as_of_ms) is not int:
        raise TypeError('as_of_ms must be an int (UTC ms)')
    if len(bars) and bars.close_ms(len(bars) - 1) > as_of_ms:
        raise FormingCandle(f'{bars.symbol}: last candle opened {bars.t_ms[-1]} closes at {bars.close_ms(len(bars) - 1)}'
                            f' > as_of {as_of_ms}; only closed candles are accepted')


def evaluate(bars: Bars, params: Params = Params(), *, as_of_ms: int) -> Evaluation:
    _check_closed(bars, as_of_ms)
    p = params
    k_s, k_l = lookback_bars(p.mom_short_days, bars.tf_ms), lookback_bars(p.mom_long_days, bars.tf_ms)
    start = max(p.warmup_bars, k_s, k_l, 1)
    c = bars.c
    ef, es, et = ind.ema(c, p.ema_fast), ind.ema(c, p.ema_slow), ind.ema(c, p.ema_trend)
    atr = ind.wilder_atr(bars.h, bars.l, c, p.atr_len)
    rs, rl = ind.pct_return(c, k_s), ind.pct_return(c, k_l)
    up, dn = ind.cross_up(ef, es), ind.cross_up(es, ef)
    n = len(c)
    le, se, lx, sx = [False] * n, [False] * n, [False] * n, [False] * n
    for i in range(start, n):
        mom_l = rs[i] is not None and rl[i] is not None and rs[i] > 0 and rl[i] > 0
        mom_s = rs[i] is not None and rl[i] is not None and rs[i] < 0 and rl[i] < 0
        le[i] = up[i] and c[i] > et[i] and mom_l
        se[i] = dn[i] and c[i] < et[i] and mom_s
        lx[i] = ef[i] < es[i] or not mom_l
        sx[i] = ef[i] > es[i] or not mom_s
    return Evaluation(bars, p, k_s, k_l, start, ef, es, et, atr, rs, rl, tuple(le), tuple(se), tuple(lx), tuple(sx))


def to_decimal(x: float) -> Decimal:
    """Exact, deterministic float -> Decimal text (shortest round-trip repr)."""
    if not math.isfinite(x):
        raise ValueError(f'non-finite value {x!r}')
    return Decimal(repr(float(x)))


def stop_distance(atr: float, stop_atr: Decimal) -> Decimal:
    return _DEC.multiply(stop_atr, to_decimal(atr))


def decisions_at(ev: Evaluation, i: int) -> Tuple[SignalDecision, ...]:
    """Decisions at the close of bar i: CLOSE first (long, short), then ENTER (long, short). Stateless: a CLOSE says
    'a lot on this side must exit'; whether one exists is the runner's business."""
    b, p = ev.bars, ev.params
    if not (0 <= i < len(b)) or i < ev.start:
        return ()
    atr = to_decimal(ev.atr[i])
    mk = lambda action, side, stop, reason: SignalDecision(RULE_ID, b.symbol, action, side, i, b.t_ms[i], b.close_ms(i),
                                                           atr, stop, reason, side is Side.SHORT)
    out = []
    if ev.lx[i]:
        out.append(mk(SignalAction.CLOSE, Side.LONG, None, REASON_EXIT))
    if p.enable_short and ev.sx[i]:
        out.append(mk(SignalAction.CLOSE, Side.SHORT, None, REASON_EXIT))
    if ev.le[i]:
        out.append(mk(SignalAction.ENTER, Side.LONG, stop_distance(ev.atr[i], p.stop_atr), REASON_ENTRY))
    if p.enable_short and ev.se[i]:
        out.append(mk(SignalAction.ENTER, Side.SHORT, stop_distance(ev.atr[i], p.stop_atr), REASON_ENTRY))
    return tuple(out)


def entries(ev: Evaluation) -> Tuple[SignalDecision, ...]:
    """Every ENTER decision over the history (research / causality checks)."""
    return tuple(d for i in range(ev.start, len(ev.bars)) for d in decisions_at(ev, i) if d.action is SignalAction.ENTER)


def decide(bars: Bars, params: Params = Params(), *, as_of_ms: int) -> Tuple[SignalDecision, ...]:
    """Runtime entry point: the decisions at the close of the LAST closed candle."""
    ev = evaluate(bars, params, as_of_ms=as_of_ms)
    return decisions_at(ev, len(bars) - 1)
