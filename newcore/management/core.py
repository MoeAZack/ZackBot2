"""The pure management transition (NC-07): `step(plan, state, confirmed, closed_candle) -> Step(state, actions)`.

No IO, no clock, no venue. The same inputs always give the same state and the same ordered actions, so live (fills
confirmed by the venue) and sim (fills produced by `sim.py` from the zb-path/1 candle path) run the SAME transition.

When the runner calls it:
- on every batch of CONFIRMED fills / refusals / cancel confirmations, with `closed_candle=None` (protection follows a
  fill at once, not at the next candle close), and
- at every candle close, with that CLOSED candle (time exit, trail, stop-crossed check).

One step:
1. applies the inputs in order (state.py): `ConfirmedFill` (FINAL executions of an OUTSTANDING authorised leg only,
   deduped by venue fill id), `Rejected` (the latest request for a leg was refused; the previous order still works),
   `Cancelled` (a cancelled leg is retired), then `funding`;
2. a TERMINAL exit dominates: once the protective stop has filled completely (or the position closed) in this batch or
   before, a racing add fill never re-opens the plan: the residual is closed at market under the last protective state
   (stage EXITING), no stop is re-placed and nothing is loosened;
3. otherwise derives the DESIRED bracket from the plan + state: stop (basket stop; tightened to a fee-aware NET
   break-even only once the TP1 leg is COMPLETE; ratcheted by the trail at candle closes; never widened; qty = the
   position), the ONE add (until TP1 / time exit / any close / a stop refusal; never requested twice), TP1
   (floor(frac x position) at the step) and TP2 (the remainder), re-anchored to the current basket average and only when
   every target leg AND the remainder are venue-feasible (min qty / notional; else the partial is skipped for one single
   exit); market closes (time exit, stop crossed, stop failed, requested exit; a late add, an add the stop cannot cover,
   or the part of an ACTUAL add fill that puts the risk to the stop above the cap is reduced at market);
4. diffs desired against requested and returns the actions in the one order of `actions.ordered`
   (protect > close > reduce > add > release). A cancelled leg keeps a racing allowance until `Cancelled(leg)`.

Quantities are always rounded DOWN at the step; partial fills of the add or TP1 recompute the average, the stop quantity
and the targets from the ACTUAL fills. The stop covers the position after every step (PositionState invariant).

Runner contract (HOLD and confirmation stay OUTSIDE the core, Codex ruling 10): the core emits REQUESTS. The runner
counts only CONFIRMED (WORKING) stop coverage as protection, sends no ADD until the stop that covers the position is
confirmed, keeps an old stop until its replacement is confirmed, dedupes fills by venue fill id before calling step, and
applies hard-HOLD (protect / close / reduce only) itself.

Restart: `PositionState` is a frozen Record holding everything step needs, including the applied fill log (dedupe and
per-leg cumulative state) and the racing allowances, so the runner persists the plan, the latest state and, per leg, the
OrderIntent ids it created from the actions (NC-08 owns that map). Equivalently the state is
`replay(plan, entry_fee, inputs)` over the durable input log; tests prove both give identical results.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

from ..domain.base import CTX, ZERO, Record, record, req
from ..domain.instrument import Rounding
from ..domain.orders import Side
from ..domain.reasons import ReasonCode
from .actions import ActionKind, ManagementAction, ordered
from .plan import (DIV, ONE, ManagementError, ManagementPlan, TrailMode, better, market_fill, sgn, toward_loss,
                   toward_profit)
from .state import AddPhase, Cancelled, Candle, ConfirmedFill, LegQty, Leg, Order, PositionState, Rejected, Stage

R = ReasonCode
K = ActionKind
ORDER_LEGS = (Leg.STOP, Leg.ADD, Leg.TP1, Leg.TP2)


@record
class Step(Record):
    state: PositionState
    actions: tuple[ManagementAction, ...]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class StepInput:
    """One durable input of the log `replay` folds (what the runner persists before calling step). Its parts are domain
    Records; NC-02 owns how the log is stored."""
    confirmed: tuple
    candle: Candle | None
    close_request: ReasonCode | None
    funding: Decimal | None


def initial_state(plan, entry_fee):
    """The state at the confirmed entry fill: the entry is open, nothing is requested yet (the first step places the
    bracket, stop first)."""
    req(isinstance(plan, ManagementPlan), 'initial_state.plan', 'not a ManagementPlan')
    return PositionState(stage=Stage.NEW, qty=plan.entry_qty, basket_qty=plan.entry_qty,
                         basket_cost=CTX.multiply(plan.entry_qty, plan.entry_price), exit_qty=ZERO, exit_value=ZERO,
                         fees=entry_fee, open_fees=entry_fee, funding=ZERO,
                         add_phase=AddPhase.PENDING if plan.has_add else AddPhase.CLOSED, add_filled=ZERO,
                         tp1_filled=ZERO, tp1_confirmed=False, tp1_done=False, targets_off=False, stop_locked=False,
                         time_exit_done=False, closing=ZERO, close_reason=None, stop=None, stop_prev=None, add=None,
                         tp1=None, tp2=None, racing=(), fills=(), last_candle_open_ms=None)


def average(state):
    """Basket average entry price from the ACTUAL opening fills (reductions do not move it)."""
    return DIV.divide(state.basket_cost, state.basket_qty)


def _csum(values):
    """Exact sum in the explicit context (builtin sum() would round in the AMBIENT decimal context)."""
    out = ZERO
    for v in values:
        out = CTX.add(out, v)
    return out


def _canonical_zero(x):
    return ZERO if x == 0 else x


def realized_pnl(plan, state):
    """Net realized PnL of a FLAT position: exit value - entry cost (side-signed) - fees - funding. Exact; zero is the
    one canonical Decimal('0') (never -0 / 0.0)."""
    req(state.qty == 0, 'realized_pnl', 'the position is still open')
    gross = CTX.multiply(sgn(plan.side), CTX.subtract(state.exit_value, state.basket_cost))
    return _canonical_zero(CTX.subtract(CTX.subtract(gross, state.fees), state.funding))


# ------------------------------------------------------------------------------------------------------- inputs
def _set_racing(w, leg, qty):
    rest = [r for r in w['racing'] if r.leg is not leg]
    if qty > 0:
        rest.append(LegQty(leg=leg, qty=qty))
    w['racing'] = tuple(sorted(rest, key=lambda r: tuple(Leg).index(r.leg)))


def _racing(w, leg):
    return next((r.qty for r in w['racing'] if r.leg is leg), ZERO)


def _consume(w, leg, q, p):
    """An outstanding authorised leg absorbs `q`: its open order first, then its racing allowance for the rest (what a
    cancelled order, or the larger order a replacement shrank, may still execute until the venue confirms the cancel -
    requested in an EARLIER step). Anything beyond both is refused."""
    if leg is Leg.CLOSE:
        # authorised while a close is requested; a reduce-only close of an EARLIER request (retried after a short
        # fill) may execute more than the current request - bounded by the position (checked by the caller)
        if not (q > 0 and (w['closing'] > 0 or _racing(w, Leg.CLOSE) > 0)):
            raise ManagementError(p, f'a CLOSE fill of {q} with {w["closing"]} close requested')
        w['closing'] = max(ZERO, CTX.subtract(w['closing'], q))     # (CLOSE racing: cleared by Cancelled only)
        return
    o, left = w[leg.value], _racing(w, leg)
    have = o.qty if o is not None else ZERO
    if leg is Leg.STOP and q > 0 and (o is not None or left > 0):
        # protective stops are reduce-only and an old stop stays live until its replacement is confirmed, so the old
        # AND the new stop may both execute: bounded by the position (checked by the caller), not by one order
        # (a stop's racing allowance is an authorisation that an older stop may still execute: only Cancelled(STOP),
        # the venue's confirmation that no older stop remains, clears it - every trade of that order is accepted)
        take = min(q, have)
        if o is not None:
            w[leg.value] = Order(price=o.price, qty=CTX.subtract(o.qty, take)) if take < o.qty else None
        return
    if not (0 < q <= CTX.add(have, left)):
        what = f'beyond its order {have} (+ racing {left})' if o is not None else \
            'on a leg with no outstanding order (unrequested / retired)'
        raise ManagementError(p, f'a {leg} fill of {q} {what}')
    take = min(q, have)
    if o is not None:
        w[leg.value] = Order(price=o.price, qty=CTX.subtract(o.qty, take)) if take < o.qty else None
    if q > take:
        _set_racing(w, leg, CTX.subtract(left, CTX.subtract(q, take)))


def _fills(plan, w, confirmed, flags):
    rules = plan.rules
    seen = {f.fill_id: f for f in w['fills']}
    for i, ev in enumerate(confirmed):
        p = f'step.confirmed[{i}]'
        if type(ev) is Rejected:
            _reject(w, ev.leg, p, flags, ev.qty)
            continue
        if type(ev) is Cancelled:
            if ev.leg in (Leg.ADD, Leg.TP1, Leg.TP2, Leg.STOP, Leg.CLOSE):
                _set_racing(w, ev.leg, ZERO)
            continue
        req(type(ev) is ConfirmedFill, p, 'only ConfirmedFill / Rejected / Cancelled are inputs')
        if ev.fill_id in seen:
            if seen[ev.fill_id] != ev:
                raise ManagementError(p + '.fill_id', f'fill id {ev.fill_id!r} re-used with different content')
            continue                                                  # the same venue fill again: idempotent
        q, px, leg = ev.qty, ev.price, ev.leg
        req(rules.on_step(q), p + '.qty', f'{q} is not on the step {rules.step_size}')
        if leg is Leg.ADD:
            wanted_before = w['add'] is not None
            _consume(w, leg, q, p)
            if w['add'] is None:
                w['add_phase'] = AddPhase.CLOSED
            if CTX.add(w['add_filled'], q) > (plan.add_qty or ZERO):
                raise ManagementError(p + '.qty', 'more than the ONE planned add')
            w['qty'] = CTX.add(w['qty'], q)
            w['basket_qty'] = CTX.add(w['basket_qty'], q)
            w['basket_cost'] = CTX.add(w['basket_cost'], CTX.multiply(q, px))
            w['add_filled'] = CTX.add(w['add_filled'], q)
            w['open_fees'] = CTX.add(w['open_fees'], ev.fee)
            flags['added'] = True
            if (not wanted_before or w['tp1_confirmed'] or w['time_exit_done'] or w['closing'] > 0
                    or w['stop_locked'] or flags['terminal']):
                flags['flatten'] = CTX.add(flags['flatten'], q)        # a late / unwanted add never stays open
        else:
            if q > w['qty']:
                raise ManagementError(p + '.qty', f'a {leg} fill of {q} beyond the position {w["qty"]}')
            _consume(w, leg, q, p)
            w['qty'] = CTX.subtract(w['qty'], q)
            w['exit_qty'] = CTX.add(w['exit_qty'], q)
            w['exit_value'] = CTX.add(w['exit_value'], CTX.multiply(q, px))
            if leg is Leg.TP1:
                w['tp1_filled'] = CTX.add(w['tp1_filled'], q)
                w['tp1_confirmed'] = True
            if (leg is Leg.STOP and w['stop'] is None) or w['qty'] == 0:
                flags['terminal'] = True                               # the protective exit is complete
            if w['closing'] > w['qty']:           # requested closes now exceed the position: the orders may still
                _set_racing(w, Leg.CLOSE, CTX.add(_racing(w, Leg.CLOSE), CTX.subtract(w['closing'], w['qty'])))
                w['closing'] = w['qty']        # execute: that part races until Cancelled(CLOSE)
        w['fees'] = CTX.add(w['fees'], ev.fee)
        seen[ev.fill_id] = ev
        w['fills'] = w['fills'] + (ev,)


def _reject(w, leg, p, flags, dead_qty=None):
    if leg is Leg.STOP:
        cur, prev = w['stop'], w['stop_prev']
        req(cur is not None, p, 'a stop refusal with no stop requested')
        w['stop_locked'] = True
        if prev is None:                                  # the placement itself was refused: nothing protects
            w['stop'] = None
            flags['close_all'] = flags['close_all'] or R.EXIT_STOP_FAILED
        else:                                             # the venue keeps the previous stop
            w['stop'], w['stop_prev'] = prev, None
            if prev.price != cur.price:                   # a BE / trail move refused: the level is crossed or invalid
                flags['close_all'] = flags['close_all'] or R.EXIT_STOP_FAILED
    elif leg is Leg.ADD:
        req(w['add'] is not None, p, 'an add refusal with no add requested')
        w['add'], w['add_phase'] = None, AddPhase.CLOSED
    elif leg in (Leg.TP1, Leg.TP2):
        req(w[leg.value] is not None, p, f'a {leg} refusal with no {leg} requested')
        w[leg.value], w['targets_off'] = None, True
    else:                                                 # a refused / short market close: retry ONLY what died
        req(w['closing'] > 0, p, 'a close refusal with no close requested')
        dead = w['closing'] if dead_qty is None else min(dead_qty, w['closing'])
        whole = w['closing'] >= w['qty']                  # the close in flight covered the whole position
        reason = w['close_reason']
        w['closing'] = CTX.subtract(w['closing'], dead)   # other close orders still in flight keep their part
        if w['closing'] == 0:
            w['close_reason'] = None
        if whole and w['closing'] == 0:                   # close it all again
            flags['close_all'] = flags['close_all'] or reason
        else:                                             # a partial REDUCE (flatten / cap / stop failed): only its rest
            flags['retry'] = CTX.add(flags['retry'], dead)
            flags['retry_reason'] = flags['retry_reason'] or reason


# ---------------------------------------------------------------------------------------------------- levels
def _feasible(plan, q, px):
    r = plan.rules
    return q >= r.min_qty and CTX.multiply(q, px) >= r.min_notional


def _net_break_even(plan, w, avg):
    """The stop level at which closing the remaining position (exit fee + slippage) still covers every booked opening fee
    and any funding paid: never the raw average. Rounded away from the average (long UP, short DOWN)."""
    q = w['qty']
    cover = DIV.divide(CTX.add(w['open_fees'], max(w['funding'], ZERO)), q)
    fee, slip = plan.costs.taker_fee, plan.costs.slip
    if plan.side is Side.LONG:
        lvl = DIV.divide(DIV.add(avg, cover), DIV.multiply(DIV.subtract(ONE, slip), DIV.subtract(ONE, fee)))
    else:
        lvl = DIV.divide(DIV.subtract(avg, cover), DIV.multiply(DIV.add(ONE, slip), DIV.add(ONE, fee)))
    return plan.rules.quantize_price(lvl, toward_profit(plan.side)) if lvl > 0 else None


def _desired_stop(plan, w, candle, avg):
    if w['qty'] == 0:
        return None
    if w['stop_locked']:
        o = w['stop']
        return None if o is None else Order(price=o.price, qty=min(o.qty, w['qty']))
    side, rules = plan.side, plan.rules
    price = w['stop'].price if w['stop'] is not None else plan.stop_price
    if plan.be_after_tp1 and w['tp1_done']:                   # ONLY a complete, confirmed TP1 leg arms break-even
        be = _net_break_even(plan, w, avg)
        if be is not None:
            price = better(side, price, be)
    if candle is not None and plan.trail_offset is not None and (w['tp1_done'] or plan.tp1_frac is None):
        if plan.trail_mode is TrailMode.HIGHEST_HIGH_ATR:     # no ATR at this close: the trail holds (never guessed)
            lvl = (None if candle.atr is None or w['trail_extreme'] is None else
                   CTX.subtract(w['trail_extreme'], CTX.multiply(sgn(side), CTX.multiply(plan.trail_offset, candle.atr))))
        else:
            lvl = CTX.subtract(candle.close, CTX.multiply(sgn(side), plan.trail_offset))
        if lvl is not None and lvl > 0:
            price = better(side, price, rules.quantize_price(lvl, toward_loss(side)))
    return Order(price=price, qty=w['qty'])


def _level(plan, avg, offset):
    lvl = CTX.add(avg, CTX.multiply(sgn(plan.side), offset))
    return plan.rules.quantize_price(lvl, toward_profit(plan.side)) if lvl > 0 else None


def risk_to_stop(plan, state_or_w, stop_price, q):
    """Loss if `q` of the position closes at `stop_price` now: side x (average cost - exit) with adverse exit slippage,
    the exit fee, plus every booked opening fee (entry + add), priced from the ACTUAL fills (Codex ruling 1). Exact while
    nothing was reduced (q == basket_qty), otherwise at 28 significant digits."""
    g = state_or_w.__getitem__ if isinstance(state_or_w, dict) else lambda k: getattr(state_or_w, k)
    if q <= 0:
        return ZERO
    ctx = CTX if q == g('basket_qty') else DIV
    cost = g('basket_cost') if ctx is CTX else DIV.divide(DIV.multiply(g('basket_cost'), q), g('basket_qty'))
    ex = market_fill(stop_price, plan.side, plan.costs.slip, opening=False)
    out_value = ctx.multiply(q, ex)
    loss = ctx.multiply(sgn(plan.side), ctx.subtract(cost, out_value))
    return ctx.add(ctx.add(loss, ctx.multiply(plan.costs.taker_fee, out_value)), g('open_fees'))


def _excess_risk(plan, w, stop, live):
    """How much of `live` must go so the risk to the stop is back within the cap (zero when it already is)."""
    if live <= 0 or stop is None or risk_to_stop(plan, w, stop.price, live) <= plan.risk_cap:
        return ZERO
    step_size = plan.rules.step_size
    unit = DIV.divide(DIV.subtract(risk_to_stop(plan, w, stop.price, live), w['open_fees']), live)
    if unit <= 0:
        return ZERO                     # booked fees alone: reducing the position cannot lower them
    room = DIV.subtract(plan.risk_cap, w['open_fees'])
    keep = min(live, plan.rules.quantize_qty(DIV.divide(room, unit), Rounding.DOWN) if room > 0 else ZERO)
    while keep > 0 and risk_to_stop(plan, w, stop.price, keep) > plan.risk_cap:
        keep = CTX.subtract(keep, step_size)                      # rounding guard: never keep more than the cap allows
    return CTX.subtract(live, max(keep, ZERO))


# --------------------------------------------------------------------------------------------------------- step
def step(plan, state, confirmed=(), closed_candle=None, *, close_request=None, funding=None):
    """One pure transition. See the module docstring. Raises ManagementError / InvalidRecord on impossible input."""
    req(isinstance(plan, ManagementPlan) and isinstance(state, PositionState), 'step', 'needs a plan and a state')
    req(type(confirmed) is tuple, 'step.confirmed', 'a tuple of ConfirmedFill / Rejected / Cancelled')
    if state.stage is Stage.DONE:
        req(close_request is None and funding is None, 'step', 'the position is done')
    w = {f.name: getattr(state, f.name) for f in dataclasses.fields(state)}
    s, side = sgn(plan.side), plan.side
    candle = closed_candle
    if candle is not None:
        req(type(candle) is Candle, 'step.closed_candle', 'not a Candle')
        d = candle.open_ms - plan.entry_candle_open_ms
        if d < 0 or d % plan.tf_ms:
            raise ManagementError('step.closed_candle', 'not a candle of this plan (before the entry / off the grid)')
        if w['last_candle_open_ms'] is not None and candle.open_ms <= w['last_candle_open_ms']:
            raise ManagementError('step.closed_candle', 'candles only go forward')
        w['last_candle_open_ms'] = candle.open_ms
        if plan.trail_mode is TrailMode.HIGHEST_HIGH_ATR and w['qty'] > 0:   # the chandelier's best price since entry
            best = w['trail_extreme'] if w['trail_extreme'] is not None else plan.entry_price
            w['trail_extreme'] = better(side, best, candle.high if side is Side.LONG else candle.low)
    flags = {'flatten': ZERO, 'close_all': None, 'retry': ZERO, 'retry_reason': None,
             'terminal': state.stage in (Stage.EXITING, Stage.DONE),
             'added': False}
    _fills(plan, w, confirmed, flags)
    if funding is not None:
        w['funding'] = CTX.add(w['funding'], funding)
    if close_request is not None:
        req(isinstance(close_request, ReasonCode) and close_request.namespace == 'exit', 'step.close_request',
            'an exit.* reason')
        flags['close_all'] = flags['close_all'] or close_request
    if w['tp1_confirmed'] and w['tp1'] is None and _racing(w, Leg.TP1) == 0:
        w['tp1_done'] = True                                     # cumulative TP1 execution reached the TP1 leg
    if state.stage is Stage.DONE and w['qty'] == 0:
        return Step(state=PositionState(**w), actions=())

    acts = []
    avg = DIV.divide(w['basket_cost'], w['basket_qty'])
    exiting = flags['terminal'] and w['qty'] > 0
    des_add = des_tp1 = des_tp2 = None
    if exiting:
        # -------------------------------------------------------------- a terminal exit dominates a racing add
        des_stop = None if w['stop'] is None else Order(price=w['stop'].price, qty=min(w['stop'].qty, w['qty']))
        live = CTX.subtract(w['qty'], w['closing'])
        if live > 0:
            acts.append(ManagementAction(kind=K.CLOSE, leg=Leg.CLOSE, qty=live, price=None, reason=R.EXIT_FLATTEN))
            w['closing'], w['close_reason'] = w['qty'], w['close_reason'] or R.EXIT_FLATTEN
        w['add_phase'] = AddPhase.CLOSED
    else:
        # -------------------------------------------------------------- TP1 leg completion (venue tolerance)
        if w['tp1_confirmed'] and not w['tp1_done'] and w['tp1'] is not None:
            live0 = CTX.subtract(CTX.subtract(w['qty'], w['closing']), flags['flatten'])
            q1 = min(w['tp1'].qty, max(live0, ZERO))
            rest = CTX.subtract(live0, q1)
            rest_px = _level(plan, avg, plan.tp2_offset) if plan.tp2_offset is not None else avg
            if not (q1 > 0 and _feasible(plan, q1, w['tp1'].price) and (rest <= 0 or _feasible(plan, rest, rest_px))):
                w['tp1_done'] = True         # the rest of the TP1 leg cannot be sent alone: it merges into one exit
        des_stop = _desired_stop(plan, w, candle, avg)
        if w['qty'] > 0 and candle is not None:
            if des_stop is not None and CTX.multiply(s, CTX.subtract(candle.close, des_stop.price)) <= 0:
                flags['close_all'] = flags['close_all'] or R.EXIT_STOP_CROSSED     # the level is crossed at the close
            if plan.time_exit_candles is not None and not w['time_exit_done']:
                if (candle.open_ms - plan.entry_candle_open_ms) // plan.tf_ms + 1 >= plan.time_exit_candles:
                    w['time_exit_done'] = True
                    flags['close_all'] = flags['close_all'] or R.EXIT_TIME
        live = CTX.subtract(w['qty'], w['closing'])
        if w['qty'] == 0:
            w['closing'], w['close_reason'] = ZERO, None
        elif flags['close_all'] is not None and live > 0:
            reason = flags['close_all']
            acts.append(ManagementAction(kind=K.TIME_EXIT if reason is R.EXIT_TIME else K.CLOSE, leg=Leg.CLOSE,
                                         qty=live, price=None, reason=reason))
            w['closing'], w['close_reason'] = w['qty'], w['close_reason'] or reason
        else:
            covered = des_stop.qty if des_stop is not None else ZERO
            cut = min(live, max(flags['flatten'], CTX.subtract(live, covered), flags['retry']))
            over = _excess_risk(plan, w, des_stop, CTX.subtract(live, cut)) if flags['added'] else ZERO
            if cut > 0 or over > 0:
                reason = (R.EXIT_STOP_FAILED if covered < live else flags['retry_reason'] or R.EXIT_FLATTEN)
                cut = CTX.add(cut, over)
                acts.append(ManagementAction(kind=K.REDUCE, leg=Leg.CLOSE, qty=cut, price=None, reason=reason))
                w['closing'], w['close_reason'] = CTX.add(w['closing'], cut), w['close_reason'] or reason
        if w['closing'] == 0:
            w['close_reason'] = None
        live = CTX.subtract(w['qty'], w['closing'])

        # ------------------------------------------------------------------------------------------ the add (cap 1)
        want_add = (plan.has_add and w['add_phase'] is not AddPhase.CLOSED and not w['tp1_confirmed']
                    and not w['time_exit_done'] and w['closing'] == 0 and not w['stop_locked'] and live > 0)
        if w['add_phase'] is AddPhase.PENDING:
            des_add = Order(price=plan.add_price, qty=plan.add_qty) if want_add else None
            w['add_phase'] = AddPhase.WORKING if want_add else AddPhase.CLOSED
        elif w['add_phase'] is AddPhase.WORKING and want_add:
            des_add = w['add']
        else:
            w['add_phase'] = AddPhase.CLOSED

        # -------------------------------------------- targets: re-anchored to the avg, every leg venue-feasible
        if live > 0 and not w['targets_off']:
            p2 = _level(plan, avg, plan.tp2_offset) if plan.tp2_offset is not None else None
            if plan.tp1_frac is not None and not w['tp1_done']:
                p1 = _level(plan, avg, plan.tp1_offset)
                if not w['tp1_confirmed']:
                    q1 = plan.rules.quantize_qty(CTX.multiply(plan.tp1_frac, live), Rounding.DOWN)
                elif w['tp1'] is not None:                   # retained remainder: re-anchored only to the profit side
                    q1 = min(w['tp1'].qty, live)
                    if CTX.multiply(s, CTX.subtract(w['tp1'].price, avg)) > 0:
                        p1 = w['tp1'].price
                else:
                    q1 = ZERO
                rest = CTX.subtract(live, q1)
                rest_px = p2 if p2 is not None else avg
                if (q1 > 0 and p1 is not None and _feasible(plan, q1, p1)
                        and (rest == 0 or _feasible(plan, rest, rest_px))):
                    des_tp1 = Order(price=p1, qty=q1)
                elif w['tp1_confirmed']:
                    w['tp1_done'] = True     # a retained TP1 remainder merged into one exit: the leg is complete
            if p2 is not None:
                q2 = CTX.subtract(live, des_tp1.qty if des_tp1 else ZERO)
                if q2 > 0 and (q2 == live or _feasible(plan, q2, p2)):   # one full exit is always allowed
                    des_tp2 = Order(price=p2, qty=q2)

    # ------------------------------------------------------------------------------------------------------ the diff
    cancel_reason = (R.LIFECYCLE_ORPHAN_CANCEL if w['qty'] == 0 else w['close_reason'] or
                     (R.EXIT_TP1 if w['tp1_confirmed'] else R.LIFECYCLE_ORPHAN_CANCEL))
    if w['qty'] == 0:
        des_stop = None
    cur = w['stop']
    if des_stop != cur:
        if des_stop is None:
            acts.append(ManagementAction(kind=K.CANCEL_STOP, leg=Leg.STOP, qty=None, price=None,
                                         reason=R.LIFECYCLE_ORPHAN_CANCEL))
            _set_racing(w, Leg.STOP, CTX.add(_racing(w, Leg.STOP), cur.qty))
        elif cur is None:
            acts.append(ManagementAction(kind=K.PLACE_STOP, leg=Leg.STOP, qty=des_stop.qty, price=des_stop.price,
                                         reason=R.PROTECT_PLACE))
        else:
            acts.append(ManagementAction(kind=K.REPLACE_STOP, leg=Leg.STOP, qty=des_stop.qty, price=des_stop.price,
                                         reason=R.PROTECT_RESIZE if des_stop.price == cur.price else R.PROTECT_REPLACE))
            # the old stop stays live until the replacement is confirmed and may execute too: it races until
            # Cancelled(STOP) (bounded by the position, see _consume)
            _set_racing(w, Leg.STOP, CTX.add(_racing(w, Leg.STOP), cur.qty))
        w['stop_prev'] = cur if des_stop is not None else None
        w['stop'] = des_stop
    if w['add'] is not None and des_add is None:
        acts.append(ManagementAction(kind=K.CANCEL_ADD, leg=Leg.ADD, qty=None, price=None, reason=cancel_reason))
        _set_racing(w, Leg.ADD, CTX.add(_racing(w, Leg.ADD), w['add'].qty))
    elif w['add'] is None and des_add is not None:
        acts.append(ManagementAction(kind=K.PLACE_ADD, leg=Leg.ADD, qty=des_add.qty, price=des_add.price,
                                     reason=R.ENTRY_PYRAMID if plan.add_is_pyramid else R.ENTRY_DCA_LEVEL))
    w['add'] = des_add
    for leg, des, why in ((Leg.TP1, des_tp1, R.EXIT_TP1), (Leg.TP2, des_tp2, R.EXIT_TAKE_PROFIT)):
        cur = w[leg.value]
        if des == cur:
            continue
        if des is None:
            acts.append(ManagementAction(kind=K.CANCEL_TARGET, leg=leg, qty=None, price=None, reason=cancel_reason))
            _set_racing(w, leg, CTX.add(_racing(w, leg), cur.qty))
        else:
            acts.append(ManagementAction(kind=K.PLACE_TARGET if cur is None else K.REPLACE_TARGET, leg=leg,
                                         qty=des.qty, price=des.price, reason=why))
            if cur is not None and cur.qty > des.qty:    # the old, larger target may still execute the difference
                _set_racing(w, leg, CTX.add(_racing(w, leg), CTX.subtract(cur.qty, des.qty)))
        w[leg.value] = des
    if w['qty'] == 0:
        w['stage'] = Stage.DONE
    elif exiting:
        w['stage'] = Stage.EXITING
    else:
        w['stage'] = Stage.ACTIVE
    return Step(state=PositionState(**w), actions=ordered(acts))


def replay(plan, entry_fee, inputs):
    """Fold `step` over a durable input log from the entry: the restart path. Returns every Step, in order."""
    st, out = initial_state(plan, entry_fee), []
    for i, x in enumerate(inputs):
        req(type(x) is StepInput, f'replay.inputs[{i}]', 'not a StepInput')
        r = step(plan, st, x.confirmed, x.candle, close_request=x.close_request, funding=x.funding)
        out.append(r)
        st = r.state
    return tuple(out)


# ------------------------------------------------------------------------------------------- exit accounting (R9)
@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ExitLeg:
    """One closing fill: gross = side x (exit - average cost) x qty, its own fee and its share of the opening fees, net,
    R = net / risk_cap, and the running totals up to it."""
    fill_id: str
    leg: Leg
    qty: Decimal
    price: Decimal
    gross: Decimal
    fee: Decimal
    open_fee_share: Decimal
    net: Decimal
    r: Decimal
    running_net: Decimal
    running_r: Decimal


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ExitLedger:
    legs: tuple
    realized_net: Decimal        # running net of every exit leg minus funding (== realized_pnl once flat)
    realized_r: Decimal
    funding: Decimal


def exit_ledger(plan, state):
    """Realized PnL and R per exit leg plus running totals, for an open OR flat position (average-cost accounting; the
    leg that flattens absorbs the rounding so the flat total equals realized_pnl exactly). 1R = plan.risk_cap."""
    s, cap = sgn(plan.side), plan.risk_cap
    entry_fee = CTX.subtract(state.open_fees, _csum(f.fee for f in state.fills if f.leg is Leg.ADD))
    pos, cost, pool = plan.entry_qty, CTX.multiply(plan.entry_qty, plan.entry_price), entry_fee
    opened, exited, fees = cost, ZERO, entry_fee               # exact cumulative totals up to the current fill
    legs, running = [], ZERO
    for f in state.fills:
        fees = CTX.add(fees, f.fee)
        if f.leg is Leg.ADD:
            pos, cost = CTX.add(pos, f.qty), CTX.add(cost, CTX.multiply(f.qty, f.price))
            opened, pool = CTX.add(opened, CTX.multiply(f.qty, f.price)), CTX.add(pool, f.fee)
            continue
        exited = CTX.add(exited, CTX.multiply(f.qty, f.price))
        if f.qty == pos:
            cost_out, share = cost, pool
        else:
            cost_out, share = DIV.divide(DIV.multiply(cost, f.qty), pos), DIV.divide(DIV.multiply(pool, f.qty), pos)
        gross = DIV.multiply(s, DIV.subtract(CTX.multiply(f.qty, f.price), cost_out))
        pos, cost, pool = CTX.subtract(pos, f.qty), DIV.subtract(cost, cost_out), DIV.subtract(pool, share)
        net = DIV.subtract(DIV.subtract(gross, f.fee), share)
        if pos == 0:                     # flat here: the exact total so far; this leg absorbs the rounding
            net = CTX.subtract(CTX.subtract(CTX.multiply(s, CTX.subtract(exited, opened)), fees), running)
            running = CTX.add(running, net)
        else:
            running = DIV.add(running, net)
        legs.append(ExitLeg(fill_id=f.fill_id, leg=f.leg, qty=f.qty, price=f.price, gross=gross, fee=f.fee,
                            open_fee_share=share, net=_canonical_zero(net), r=_canonical_zero(DIV.divide(net, cap)),
                            running_net=_canonical_zero(running), running_r=_canonical_zero(DIV.divide(running, cap))))
    realized = _canonical_zero(CTX.subtract(running, state.funding) if state.qty == 0 else
                               DIV.subtract(running, state.funding))
    return ExitLedger(legs=tuple(legs), realized_net=realized, realized_r=_canonical_zero(DIV.divide(realized, cap)),
                      funding=state.funding)
