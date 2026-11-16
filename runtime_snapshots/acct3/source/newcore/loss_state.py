"""RISK-LOSS-01 loss-state producer for the NC-06 risk gateway.

Pure and deterministic: the caller passes the clock (epoch ms), live equity
and unrealized PnL; nothing here reads a clock, the network or a store.  The
runtime persists :class:`LossTracker` and rebuilds it only through these
functions:

* :func:`start` creates the tracker for an account.
* :func:`observe` rolls the Africa/Cairo trading day, tracks the equity peak
  and sets the daily-halt and drawdown-kill latches.  Call it before every
  :func:`snapshot` and persist the result.
* :func:`record_close` adds realized PnL (net of fees and funding) and logs
  stop-outs for the loss-streak cooldown.
* :func:`record_entry` logs an ENTRY for the same-candle cluster limit.
* :func:`owner_resume` is the only way to clear the drawdown-kill latch.
* :func:`snapshot` builds the ``LossState`` for ``RiskEvaluation``.

Every function raises ``ValueError`` on malformed input.  A caller that
cannot build a ``LossState`` cannot build a ``RiskEvaluation``, so no new risk
is authorized: fail-closed.
"""
from __future__ import annotations

import dataclasses
import datetime
import functools
from dataclasses import dataclass
from decimal import (Decimal, DecimalException, DivisionByZero, InvalidOperation, Overflow,
                     ROUND_HALF_EVEN, localcontext)
from zoneinfo import ZoneInfo

from newcore.risk import (
    ClusterEntry, LossLimits, LossState, PositionSide, daily_loss_breached,
    drawdown_kill_breached, strategy_paused,
)

CAIRO = ZoneInfo('Africa/Cairo')
_ZERO = Decimal('0')
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
_MAX_MS = 253402300799999                      # 9999-12-31T23:59:59.999Z
_HORIZON_MS = 2 * 24 * 3600 * 1000              # bookkeeping kept at least this long


def _fail_closed(function):
    """Exact Decimal arithmetic; any arithmetic fault becomes ValueError."""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        with localcontext() as context:
            context.prec = 64
            context.rounding = ROUND_HALF_EVEN
            for signal in (InvalidOperation, Overflow, DivisionByZero):
                context.traps[signal] = True
            try:
                return function(*args, **kwargs)
            except (DecimalException, OverflowError) as exc:
                raise ValueError('arithmetic out of range') from exc
    return wrapper


@dataclass(frozen=True, slots=True, kw_only=True)
class StopOut:
    strategy_id: str
    at_ms: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LoggedEntry:
    entry: ClusterEntry
    at_ms: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LossTracker:
    account_id: str
    trading_day: str
    day_start_equity: Decimal
    day_start_unrealized: Decimal
    realized_pnl: Decimal
    halt_latched: bool
    peak_equity: Decimal
    drawdown_kill_latched: bool
    last_ms: int
    stop_outs: tuple[StopOut, ...]
    entries: tuple[LoggedEntry, ...]


def _ms(value: object) -> int:
    if type(value) is not int or not 0 < value <= _MAX_MS:
        raise ValueError('epoch ms must be a positive int')
    return value


def _dec(value: object, *, positive: bool = False) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or (positive and value <= 0):
        raise ValueError('expected a finite Decimal' + (' > 0' if positive else ''))
    return value


def _symbol(value: object) -> str:
    if (type(value) is not str or not 2 <= len(value) <= 32 or not value.isascii()
            or not value.isalnum() or value.upper() != value):
        raise ValueError('expected a symbol')
    return value


def _id(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 64:
        raise ValueError('expected an id')
    return value


def _limits(value: object) -> LossLimits:
    if type(value) is not LossLimits:
        raise ValueError('expected LossLimits')
    return value


def _tracker(value: object) -> LossTracker:
    if type(value) is not LossTracker:
        raise ValueError('expected LossTracker')
    return value


def cairo_trading_day(now_ms: int) -> str:
    """The Africa/Cairo calendar day (DST-aware) containing ``now_ms``."""
    instant = _EPOCH + datetime.timedelta(milliseconds=_ms(now_ms))
    return instant.astimezone(CAIRO).date().isoformat()


def _check_clock(tracker: LossTracker, now_ms: int) -> None:
    if _ms(now_ms) < tracker.last_ms:
        raise ValueError('clock went backwards')


@_fail_closed
def start(account_id: str, *, now_ms: int, equity: Decimal, unrealized_pnl: Decimal) -> LossTracker:
    return LossTracker(
        account_id=_id(account_id), trading_day=cairo_trading_day(now_ms),
        day_start_equity=_dec(equity, positive=True), day_start_unrealized=_dec(unrealized_pnl),
        realized_pnl=_ZERO, halt_latched=False, peak_equity=equity,
        drawdown_kill_latched=False, last_ms=now_ms, stop_outs=(), entries=())


@_fail_closed
def observe(tracker: LossTracker, limits: LossLimits, *, now_ms: int, equity: Decimal,
            unrealized_pnl: Decimal) -> LossTracker:
    """Roll the Cairo day, raise the peak and latch the breakers."""
    t, lim = _tracker(tracker), _limits(limits)
    _check_clock(t, now_ms)
    equity, unrealized_pnl = _dec(equity, positive=True), _dec(unrealized_pnl)
    day = cairo_trading_day(now_ms)
    if day != t.trading_day:
        if day < t.trading_day:
            raise ValueError('trading day went backwards')
        t = dataclasses.replace(t, trading_day=day, day_start_equity=equity,
                                day_start_unrealized=unrealized_pnl, realized_pnl=_ZERO,
                                halt_latched=False)
    # The kill is judged against the peak before this observation, so a new
    # high cannot hide a drawdown; then the peak rises to the new high.
    state = _state(t, unrealized_pnl, ())
    halted = daily_loss_breached(lim, state)
    killed = drawdown_kill_breached(lim, state, equity)
    horizon = now_ms - max(lim.cooldown_window_ms + lim.cooldown_pause_ms, _HORIZON_MS)
    # Both helpers already return True while a latch is set, so a latch can
    # only be cleared by the day roll above (daily) or owner_resume (kill).
    return dataclasses.replace(
        t, last_ms=now_ms, halt_latched=halted, drawdown_kill_latched=killed,
        peak_equity=max(t.peak_equity, equity),
        stop_outs=tuple(x for x in t.stop_outs if x.at_ms > horizon),
        entries=tuple(x for x in t.entries if x.at_ms > horizon))


@_fail_closed
def record_close(tracker: LossTracker, *, strategy_id: str, realized_pnl: Decimal,
                 stopped_out: bool, now_ms: int) -> LossTracker:
    t = _tracker(tracker)
    _check_clock(t, now_ms)
    if type(stopped_out) is not bool:
        raise ValueError('stopped_out must be bool')
    strategy_id = _id(strategy_id)
    if cairo_trading_day(now_ms) != t.trading_day:
        raise ValueError('observe() the new trading day before recording into it')
    stops = t.stop_outs + ((StopOut(strategy_id=strategy_id, at_ms=now_ms),) if stopped_out else ())
    return dataclasses.replace(t, realized_pnl=t.realized_pnl + _dec(realized_pnl),
                               stop_outs=stops, last_ms=now_ms)


@_fail_closed
def record_entry(tracker: LossTracker, entry: ClusterEntry, *, now_ms: int) -> LossTracker:
    t = _tracker(tracker)
    _check_clock(t, now_ms)
    if type(entry) is not ClusterEntry or type(entry.side) is not PositionSide:
        raise ValueError('expected ClusterEntry')
    _id(entry.strategy_id)
    _symbol(entry.symbol)
    return dataclasses.replace(t, entries=t.entries + (LoggedEntry(entry=entry, at_ms=now_ms),),
                               last_ms=now_ms)


@_fail_closed
def owner_resume(tracker: LossTracker, *, now_ms: int, equity: Decimal) -> LossTracker:
    """Explicit owner action: clear the drawdown kill and restart the peak.

    The kill is still measured from at least today's day-start equity, so on
    a day that alone lost ``max_drawdown_fraction`` the resume takes effect at
    the next Cairo day roll (the daily halt has stopped that day anyway).
    """
    t = _tracker(tracker)
    _check_clock(t, now_ms)
    return dataclasses.replace(t, drawdown_kill_latched=False, peak_equity=_dec(equity, positive=True),
                               last_ms=now_ms)


def _state(t: LossTracker, unrealized_pnl: Decimal, paused: tuple[str, ...],
           entries: tuple[ClusterEntry, ...] = ()) -> LossState:
    return LossState(
        trading_day=t.trading_day, day_start_equity=t.day_start_equity,
        realized_pnl=t.realized_pnl, unrealized_pnl=unrealized_pnl - t.day_start_unrealized,
        halt_latched=t.halt_latched, peak_equity=t.peak_equity,
        drawdown_kill_latched=t.drawdown_kill_latched, paused_strategies=paused,
        cluster_entries=entries)


@_fail_closed
def snapshot(tracker: LossTracker, limits: LossLimits, *, now_ms: int, unrealized_pnl: Decimal,
             strategy_ids: tuple[str, ...], cluster_window_ms: int) -> LossState:
    """``LossState`` for the gateway.  ``tracker`` must come from
    :func:`observe` at this same ``now_ms`` (record_* calls in between are
    allowed; the gateway recomputes both breakers from the state, and the
    next observe() stores the latches).  ``strategy_ids`` lists every
    strategy the runtime may evaluate; output tuples are canonical."""
    t, lim = _tracker(tracker), _limits(limits)
    if t.last_ms != _ms(now_ms) or cairo_trading_day(now_ms) != t.trading_day:
        raise ValueError('observe() at now_ms before snapshot()')
    window = _ms(cluster_window_ms)
    if window > _HORIZON_MS:
        raise ValueError('cluster window longer than the bookkeeping horizon')
    if type(strategy_ids) is not tuple:
        raise ValueError('strategy_ids must be a tuple')
    paused = tuple(sorted(
        s for s in {_id(x) for x in strategy_ids}
        if strategy_paused(lim, tuple(x.at_ms for x in t.stop_outs if x.strategy_id == s), now_ms)))
    window_start = now_ms - now_ms % window
    entries = tuple(x.entry for x in sorted(
        (x for x in t.entries if x.at_ms >= window_start),
        key=lambda x: (x.at_ms, x.entry.strategy_id, x.entry.symbol, x.entry.side.value)))
    return _state(t, _dec(unrealized_pnl), paused, entries)


__all__ = ['CAIRO', 'LoggedEntry', 'LossTracker', 'StopOut', 'cairo_trading_day', 'observe',
           'owner_resume', 'record_close', 'record_entry', 'snapshot', 'start']
