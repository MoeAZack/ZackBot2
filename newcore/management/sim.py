"""Pure candle simulator for the management core: the golden pack's zb-path/1 path model (FBL-BT01) driving `step`.

The simulator only decides WHEN and AT WHAT PRICE the bracket the core requested would fill; every reaction (stop
resize, re-anchor, break-even, cancels, closes) comes from the same `core.step` the live runner calls. So the same fills
produce the same actions live and in sim.

Path policy zb-path/1 (backtest.path_points):
- green candle (close > open): open -> low -> high -> close; red: open -> high -> low -> close; doji: the worst case
  for the side (long open -> high -> low -> close, short open -> low -> high -> close);
- a level already THROUGH at the current point fills at that point, not at the level: a candle that OPENS past the stop,
  the add or a target fills at the open (gap). At one point the order is stop, TP1, TP2, add (protect first); a stop
  gapped at the open closes the position, so no add fills;
- otherwise a level fills when the path reaches it, nearest first; orders the core requests after a fill (re-anchored
  targets, a raised / break-even stop) are live from that point on, so they can be reached only by later price moves;
- stop-first for the target/stop ambiguity only: when the stop as at the open lies inside the candle's range, no target
  fills in that candle (adds still fill in path order). So stop and TP1 in one candle = no TP1, no break-even;
- every fill is a taker market fill adverse-adjusted by the slippage, with the taker fee on its notional; market closes
  the core requests at the candle close (time exit, stop crossed, requested exit) fill at the close.
"""
from __future__ import annotations

from ..domain.base import CTX, Record, record, req
from ..domain.orders import Side
from .actions import MARKET, ManagementAction
from .core import initial_state, step
from .plan import market_fill
from .state import Candle, ConfirmedFill, Leg, PositionState, Stage

PRIORITY = (Leg.STOP, Leg.TP1, Leg.TP2, Leg.ADD)
MAX_EVENTS = 64


@record
class SimStep(Record):
    state: PositionState
    fills: tuple[ConfirmedFill, ...]
    actions: tuple[ManagementAction, ...]     # in emission order (each step's actions already ordered)


def path_points(candle, side):
    o, h, l, c = candle.open, candle.high, candle.low, candle.close
    if c == o:
        return (o, h, l, c) if side is Side.LONG else (o, l, h, c)
    return (o, l, h, c) if c > o else (o, h, l, c)


def _levels(plan, st, targets_blocked):
    """(leg, price, falls) of every resting level; `falls` = it triggers when price falls to / below it."""
    long = plan.side is Side.LONG
    out = []
    if st.stop is not None:
        out.append((Leg.STOP, st.stop.price, long))
    if not targets_blocked:
        for leg in (Leg.TP1, Leg.TP2):
            o = getattr(st, leg.value)
            if o is not None:
                out.append((leg, o.price, not long))
    if st.add is not None:
        out.append((Leg.ADD, st.add.price, long != plan.add_is_pyramid))
    return out


def _through(price, falls, x):
    return x <= price if falls else x >= price


def _fill(plan, st, leg, px):
    o = getattr(st, leg.value)
    fp = market_fill(px, plan.side, plan.costs.slip, opening=leg is Leg.ADD)
    return ConfirmedFill(leg=leg, qty=o.qty, price=fp, fee=CTX.multiply(plan.costs.taker_fee, CTX.multiply(o.qty, fp)))


def _apply(plan, st, fill, px, out_fills, out_actions, candle=None, close_request=None):
    """Step with `fill` (or a candle close), then fill any market close the core asked for at `px` right away."""
    r = step(plan, st, (fill,) if fill is not None else (), candle, close_request=close_request)
    if fill is not None:
        out_fills.append(fill)
    out_actions.extend(r.actions)
    st = r.state
    for a in r.actions:
        if a.kind in MARKET and st.qty > 0:
            q = min(a.qty, st.qty)
            fp = market_fill(px, plan.side, plan.costs.slip, opening=False)
            f = ConfirmedFill(leg=Leg.CLOSE, qty=q, price=fp, fee=CTX.multiply(plan.costs.taker_fee, CTX.multiply(q, fp)))
            st = _apply(plan, st, f, px, out_fills, out_actions)
    return st


def simulate_candle(plan, state, candle, *, close_request=None):
    """Walk one CLOSED candle along zb-path/1 against the requested bracket, then run the candle-close step."""
    req(type(candle) is Candle, 'simulate_candle.candle', 'not a Candle')
    req(state.stage is Stage.ACTIVE, 'simulate_candle.state', 'needs an ACTIVE bracket (run the first step)')
    fills, actions = [], []
    st = state
    blocked = st.stop is not None and candle.low <= st.stop.price <= candle.high
    pts = path_points(candle, plan.side)
    x, n = pts[0], 0
    for y in pts:                                        # y == open first: the gap check at the open
        while st.stage is not Stage.DONE:
            n += 1
            req(n <= MAX_EVENTS, 'simulate_candle', 'too many fills in one candle')
            lv = _levels(plan, st, blocked)
            hit = next(((g, x) for g in PRIORITY for leg, p, falls in lv if leg is g and _through(p, falls, x)), None)
            if hit is None:
                cand = [(abs(p - x), PRIORITY.index(leg), leg, p) for leg, p, falls in lv
                        if (falls and y <= p < x) or (not falls and x < p <= y)]
                if not cand:
                    break
                _, _, leg, p = min(cand)
                hit, x = (leg, p), p
            leg, px = hit
            st = _apply(plan, st, _fill(plan, st, leg, px), px, fills, actions)
        x = y
    if st.stage is not Stage.DONE:
        st = _apply(plan, st, None, candle.close, fills, actions, candle=candle, close_request=close_request)
    return SimStep(state=st, fills=tuple(fills), actions=tuple(actions))


@record
class SimRun(Record):
    state: PositionState
    fills: tuple[ConfirmedFill, ...]
    actions: tuple[ManagementAction, ...]
    exit_candle: int | None              # index into `candles` of the candle the position went flat in


def run(plan, candles, *, exits=None):
    """Entry fill (taker fee) at the open of candles[0] (the entry candle), the first step (bracket), then one
    `simulate_candle` per candle until flat. `exits` = {candle index: exit.* reason} requested at that candle's close."""
    req(candles and candles[0].open_ms == plan.entry_candle_open_ms, 'run.candles', 'candles[0] is the entry candle')
    exits = exits or {}
    fee = CTX.multiply(plan.costs.taker_fee, CTX.multiply(plan.entry_qty, plan.entry_price))
    first = step(plan, initial_state(plan, fee))
    st, fills, actions, out = first.state, [], list(first.actions), None
    for i, c in enumerate(candles):
        r = simulate_candle(plan, st, c, close_request=exits.get(i))
        st = r.state
        fills.extend(r.fills)
        actions.extend(r.actions)
        if st.stage is Stage.DONE:
            out = i
            break
    return SimRun(state=st, fills=tuple(fills), actions=tuple(actions), exit_candle=out)
