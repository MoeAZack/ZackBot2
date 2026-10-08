"""The pure management transition (NC-07): `step(plan, state, confirmed, closed_candle) -> Step(state, actions)`.

No IO, no clock, no venue. The same inputs always give the same state and the same ordered actions, so live (fills
confirmed by the venue) and sim (fills produced by `sim.py` from the zb-path/1 candle path) run the SAME transition.

When the runner calls it:
- on every batch of CONFIRMED fills / refusals, with `closed_candle=None` (protection follows a fill at once, not at
  the next candle close), and
- at every candle close, with that CLOSED candle (time exit, trail, stop-crossed check).

One step:
1. applies the inputs in order: `ConfirmedFill` (FINAL executions only), `Rejected` (the latest request for a leg was
   refused; the previous order still works), then `funding`;
2. derives the DESIRED bracket from the plan + state: stop (basket stop, tightened to break-even only after a CONFIRMED
   TP1 fill, ratcheted by the trail at candle closes; never widened; qty = the position), the ONE add (until TP1 / time
   exit / any close / a stop refusal; never requested twice), TP1 (floor(frac x position) at the step, skipped when that
   floors to zero) and TP2 (the remainder), both re-anchored to the current basket average; market closes (time exit,
   stop crossed, stop failed, requested exit; a late add or an add the stop cannot cover is flattened with REDUCE);
3. diffs desired against requested and returns the actions in the one order of `actions.ordered`
   (protect > close > reduce > add > release).

Quantities are always rounded DOWN at the step; partial fills of the add or TP1 recompute the average, the stop quantity
and the targets from the ACTUAL fills. The stop covers the position after every step (PositionState invariant).

Restart: `PositionState` is a frozen Record holding everything step needs, so the runner persists the plan, the latest
state and, per leg, the OrderIntent ids it created from the actions (NC-08 owns that map). Equivalently the state is
`replay(plan, entry_fee, inputs)` over the durable input log; tests prove both give identical results.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

from ..domain.base import CTX, ZERO, Record, record, req
from ..domain.instrument import Rounding
from ..domain.reasons import ReasonCode
from .actions import ActionKind, ManagementAction, ordered
from .plan import DIV, ManagementError, ManagementPlan, better, sgn, toward_loss, toward_profit
from .state import AddPhase, Candle, ConfirmedFill, Leg, Order, PositionState, Rejected, Stage

R = ReasonCode
K = ActionKind


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
                         fees=entry_fee, funding=ZERO,
                         add_phase=AddPhase.PENDING if plan.has_add else AddPhase.CLOSED, add_filled=ZERO,
                         tp1_filled=ZERO, tp1_confirmed=False, targets_off=False, stop_locked=False,
                         time_exit_done=False, closing=ZERO, close_reason=None, stop=None, stop_prev=None, add=None,
                         tp1=None, tp2=None, last_candle_open_ms=None)


def average(state):
    """Basket average entry price from the ACTUAL opening fills (reductions do not move it)."""
    return DIV.divide(state.basket_cost, state.basket_qty)


def realized_pnl(plan, state):
    """Net realized PnL of a FLAT position: exit value - entry cost (side-signed) - fees - funding. Exact."""
    req(state.qty == 0, 'realized_pnl', 'the position is still open')
    gross = CTX.multiply(sgn(plan.side), CTX.subtract(state.exit_value, state.basket_cost))
    return CTX.subtract(CTX.subtract(gross, state.fees), state.funding)


def _fills(plan, w, confirmed, flags):
    rules = plan.rules
    for i, ev in enumerate(confirmed):
        p = f'step.confirmed[{i}]'
        if type(ev) is Rejected:
            _reject(w, ev.leg, p, flags)
            continue
        req(type(ev) is ConfirmedFill, p, 'only ConfirmedFill / Rejected are inputs')
        q, px, leg = ev.qty, ev.price, ev.leg
        req(rules.on_step(q), p + '.qty', f'{q} is not on the step {rules.step_size}')
        w['fees'] = CTX.add(w['fees'], ev.fee)
        if leg is Leg.ADD:
            o = w['add']
            if o is not None:
                req(q <= o.qty, p + '.qty', f'an ADD fill of {q} beyond its order {o.qty}')
                w['add'] = Order(price=o.price, qty=CTX.subtract(o.qty, q)) if q < o.qty else None
                if w['add'] is None:
                    w['add_phase'] = AddPhase.CLOSED
            req(CTX.add(w['add_filled'], q) <= (plan.add_qty or ZERO), p + '.qty', 'more than the ONE planned add')
            w['qty'] = CTX.add(w['qty'], q)
            w['basket_qty'] = CTX.add(w['basket_qty'], q)
            w['basket_cost'] = CTX.add(w['basket_cost'], CTX.multiply(q, px))
            w['add_filled'] = CTX.add(w['add_filled'], q)
            if o is None or w['tp1_confirmed'] or w['time_exit_done'] or w['closing'] > 0 or w['stop_locked']:
                flags['flatten'] = CTX.add(flags['flatten'], q)        # a late / unwanted add never stays open
            continue
        req(q <= w['qty'], p + '.qty', f'a {leg} fill of {q} beyond the position {w["qty"]}')
        w['qty'] = CTX.subtract(w['qty'], q)
        w['exit_qty'] = CTX.add(w['exit_qty'], q)
        w['exit_value'] = CTX.add(w['exit_value'], CTX.multiply(q, px))
        if leg is Leg.CLOSE:
            w['closing'] = max(ZERO, CTX.subtract(w['closing'], q))
        else:
            o = w[leg.value]                                         # stop / tp1 / tp2 (None = raced with a cancel)
            if o is not None:
                req(q <= o.qty, p + '.qty', f'a {leg} fill of {q} beyond its order {o.qty}')
                w[leg.value] = Order(price=o.price, qty=CTX.subtract(o.qty, q)) if q < o.qty else None
            if leg is Leg.TP1:
                w['tp1_filled'] = CTX.add(w['tp1_filled'], q)
                w['tp1_confirmed'] = True
        w['closing'] = min(w['closing'], w['qty'])


def _reject(w, leg, p, flags):
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
    else:                                                 # a refused market close: retry it now
        req(w['closing'] > 0, p, 'a close refusal with no close requested')
        flags['close_all'] = flags['close_all'] or w['close_reason']
        w['closing'], w['close_reason'] = ZERO, None


def _desired_stop(plan, w, candle, avg):
    if w['qty'] == 0:
        return None
    if w['stop_locked']:
        o = w['stop']
        return None if o is None else Order(price=o.price, qty=min(o.qty, w['qty']))
    side, rules = plan.side, plan.rules
    price = w['stop'].price if w['stop'] is not None else plan.stop_price
    if plan.be_after_tp1 and w['tp1_confirmed']:              # ONLY a confirmed TP1 fill moves the stop to BE
        price = better(side, price, rules.quantize_price(avg, toward_profit(side)))
    if candle is not None and plan.trail_offset is not None and (w['tp1_confirmed'] or plan.tp1_frac is None):
        lvl = CTX.subtract(candle.close, CTX.multiply(sgn(side), plan.trail_offset))
        if lvl > 0:
            price = better(side, price, rules.quantize_price(lvl, toward_loss(side)))
    return Order(price=price, qty=w['qty'])


def _target(plan, avg, offset, qty):
    lvl = CTX.add(avg, CTX.multiply(sgn(plan.side), offset))
    if qty <= 0 or lvl <= 0:
        return None
    return Order(price=plan.rules.quantize_price(lvl, toward_profit(plan.side)), qty=qty)


def step(plan, state, confirmed=(), closed_candle=None, *, close_request=None, funding=None):
    """One pure transition. See the module docstring. Raises ManagementError / InvalidRecord on impossible input."""
    req(isinstance(plan, ManagementPlan) and isinstance(state, PositionState), 'step', 'needs a plan and a state')
    req(type(confirmed) is tuple, 'step.confirmed', 'a tuple of ConfirmedFill / Rejected')
    if state.stage is Stage.DONE:
        req(not confirmed and close_request is None and funding is None, 'step', 'the position is done')
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
    flags = {'flatten': ZERO, 'close_all': None}
    _fills(plan, w, confirmed, flags)
    if funding is not None:
        w['funding'] = CTX.add(w['funding'], funding)
    if close_request is not None:
        req(isinstance(close_request, ReasonCode) and close_request.namespace == 'exit', 'step.close_request',
            'an exit.* reason')
        flags['close_all'] = flags['close_all'] or close_request
    if state.stage is Stage.DONE:
        return Step(state=PositionState(**w), actions=())

    avg = DIV.divide(w['basket_cost'], w['basket_qty'])
    des_stop = _desired_stop(plan, w, candle, avg)
    if w['qty'] > 0 and candle is not None:
        if des_stop is not None and CTX.multiply(s, CTX.subtract(candle.close, des_stop.price)) <= 0:
            flags['close_all'] = flags['close_all'] or R.EXIT_STOP_CROSSED     # the level is crossed at the close
        if plan.time_exit_candles is not None and not w['time_exit_done']:
            if (candle.open_ms - plan.entry_candle_open_ms) // plan.tf_ms + 1 >= plan.time_exit_candles:
                w['time_exit_done'] = True
                flags['close_all'] = flags['close_all'] or R.EXIT_TIME

    acts = []
    live = CTX.subtract(w['qty'], w['closing'])
    if w['qty'] == 0:
        w['closing'], w['close_reason'] = ZERO, None
    elif flags['close_all'] is not None and live > 0:
        reason = flags['close_all']
        acts.append(ManagementAction(kind=K.TIME_EXIT if reason is R.EXIT_TIME else K.CLOSE, leg=Leg.CLOSE, qty=live,
                                     price=None, reason=reason))
        w['closing'], w['close_reason'] = w['qty'], w['close_reason'] or reason
    else:
        covered = des_stop.qty if des_stop is not None else ZERO
        cut = min(live, max(flags['flatten'], CTX.subtract(live, covered)))
        if cut > 0:
            reason = R.EXIT_STOP_FAILED if covered < live else R.EXIT_FLATTEN
            acts.append(ManagementAction(kind=K.REDUCE, leg=Leg.CLOSE, qty=cut, price=None, reason=reason))
            w['closing'], w['close_reason'] = CTX.add(w['closing'], cut), w['close_reason'] or reason
    if w['closing'] == 0:
        w['close_reason'] = None
    live = CTX.subtract(w['qty'], w['closing'])

    # ------------------------------------------------------------------------------------------------ the add (cap 1)
    want_add = (plan.has_add and w['add_phase'] is not AddPhase.CLOSED and not w['tp1_confirmed']
                and not w['time_exit_done'] and w['closing'] == 0 and not w['stop_locked'] and live > 0)
    if w['add_phase'] is AddPhase.PENDING:
        des_add = Order(price=plan.add_price, qty=plan.add_qty) if want_add else None
        w['add_phase'] = AddPhase.WORKING if want_add else AddPhase.CLOSED
    elif w['add_phase'] is AddPhase.WORKING and want_add:
        des_add = w['add']
    else:
        des_add, w['add_phase'] = None, AddPhase.CLOSED

    # ------------------------------------------------------------------------------- targets (re-anchored to the avg)
    des_tp1 = des_tp2 = None
    if live > 0 and not w['targets_off']:
        if plan.tp1_frac is not None:
            if not w['tp1_confirmed']:
                q = plan.rules.quantize_qty(CTX.multiply(plan.tp1_frac, live), Rounding.DOWN)
                des_tp1 = _target(plan, avg, plan.tp1_offset, q)          # floors to zero: skipped (C25)
            elif w['tp1'] is not None:
                des_tp1 = Order(price=w['tp1'].price, qty=min(w['tp1'].qty, live))
        if plan.tp2_offset is not None:
            des_tp2 = _target(plan, avg, plan.tp2_offset, CTX.subtract(live, des_tp1.qty if des_tp1 else ZERO))

    # ------------------------------------------------------------------------------------------------------ the diff
    cancel_reason = (R.LIFECYCLE_ORPHAN_CANCEL if w['qty'] == 0 else w['close_reason'] or
                     (R.EXIT_TP1 if w['tp1_confirmed'] else R.LIFECYCLE_ORPHAN_CANCEL))
    cur = w['stop']
    if des_stop != cur:
        if des_stop is None:
            acts.append(ManagementAction(kind=K.CANCEL_STOP, leg=Leg.STOP, qty=None, price=None,
                                         reason=R.LIFECYCLE_ORPHAN_CANCEL))
        elif cur is None:
            acts.append(ManagementAction(kind=K.PLACE_STOP, leg=Leg.STOP, qty=des_stop.qty, price=des_stop.price,
                                         reason=R.PROTECT_PLACE))
        else:
            acts.append(ManagementAction(kind=K.REPLACE_STOP, leg=Leg.STOP, qty=des_stop.qty, price=des_stop.price,
                                         reason=R.PROTECT_RESIZE if des_stop.price == cur.price else R.PROTECT_REPLACE))
        w['stop_prev'] = cur if des_stop is not None else None
        w['stop'] = des_stop
    if w['add'] is not None and des_add is None:
        acts.append(ManagementAction(kind=K.CANCEL_ADD, leg=Leg.ADD, qty=None, price=None, reason=cancel_reason))
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
        else:
            acts.append(ManagementAction(kind=K.PLACE_TARGET if cur is None else K.REPLACE_TARGET, leg=leg,
                                         qty=des.qty, price=des.price, reason=why))
        w[leg.value] = des
    w['stage'] = Stage.DONE if w['qty'] == 0 else Stage.ACTIVE
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
