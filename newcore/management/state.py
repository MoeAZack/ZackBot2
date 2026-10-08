"""Position state, inputs and the candle record of the management core (NC-07).

`PositionState` is the whole memory of one managed position: what is open, the basket cost, which bracket orders the core
has requested (stop / add / TP1 / TP2), what market close is in flight, and the accounting fields (exit value, fees,
funding). It is a frozen domain Record, so it can be persisted as-is, and it is also exactly reconstructible by folding
`step` over the durable input log from `initial_state(plan, entry_fee)` (see core.replay). Inputs:

- `ConfirmedFill(leg, qty, price, fee)`: a FINAL execution of one bracket leg (never a touch, never an unconfirmed order);
- `Rejected(leg)`: the venue refused the latest request for that leg (the previous order, if any, is still working);
- `Candle`: a CLOSED candle (open time + OHLC).

Invariants checked by the constructor (any violation is an InvalidRecord, so no step can return a bad state):
- once ACTIVE, stop qty <= position qty (protection never exceeds exposure) and stop qty >= position qty - closing (the
  stop covers everything that is not already being closed at market);
- targets never exceed the position that is not being closed; an add order only while the add phase is WORKING.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from ..domain.base import CTX, ZERO, Record, non_negative, positive, record, req
from ..domain.reasons import ReasonCode


class Leg(enum.StrEnum):
    STOP = 'stop'
    ADD = 'add'
    TP1 = 'tp1'
    TP2 = 'tp2'
    CLOSE = 'close'          # a market close / reduce the core requested (time exit, stop crossed / failed, flatten)


class AddPhase(enum.StrEnum):
    PENDING = 'pending'      # planned, not requested yet
    WORKING = 'working'      # requested (part may have filled)
    CLOSED = 'closed'        # filled, cancelled, refused, skipped or never planned: never requested again (cap = 1)


class Stage(enum.StrEnum):
    NEW = 'new'              # entry confirmed, no bracket requested yet (the first step places it)
    ACTIVE = 'active'
    DONE = 'done'            # flat and every order released


@record
class Order(Record):
    """A bracket order as the core requested it: trigger price + remaining quantity."""
    price: Decimal
    qty: Decimal

    def _validate(self, p):
        positive(self.price, p + '.price')
        positive(self.qty, p + '.qty')


@record
class ConfirmedFill(Record):
    leg: Leg
    qty: Decimal
    price: Decimal
    fee: Decimal

    def _validate(self, p):
        positive(self.qty, p + '.qty')
        positive(self.price, p + '.price')
        non_negative(self.fee, p + '.fee')


@record
class Rejected(Record):
    leg: Leg


@record
class Candle(Record):
    open_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def _validate(self, p):
        positive(self.low, p + '.low')
        req(self.low <= min(self.open, self.close) and self.high >= max(self.open, self.close), p + '.high',
            'open / close outside the high-low range')


@record
class PositionState(Record):
    stage: Stage
    qty: Decimal                 # open position
    basket_qty: Decimal          # total opened (entry + add fills)
    basket_cost: Decimal         # sum of qty x price of the opening fills (avg = basket_cost / basket_qty)
    exit_qty: Decimal            # total closed
    exit_value: Decimal          # sum of qty x price of the closing fills
    fees: Decimal                # every fill's fee, entry included
    funding: Decimal             # funding paid (+) / received (-)
    add_phase: AddPhase
    add_filled: Decimal
    tp1_filled: Decimal
    tp1_confirmed: bool          # a TP1 fill was confirmed: BE (if planned), no add, trail may start
    targets_off: bool            # a target was refused: no more targets (stop / time exit manage the rest)
    stop_locked: bool            # a stop request was refused: the old stop stays and is only ever shrunk
    time_exit_done: bool
    closing: Decimal             # market close quantity requested and not yet filled
    close_reason: ReasonCode | None   # reason of the close in flight
    stop: Order | None
    stop_prev: Order | None      # the stop before the latest replace (what the venue keeps if that replace is refused)
    add: Order | None
    tp1: Order | None
    tp2: Order | None
    last_candle_open_ms: int | None

    def _validate(self, p):
        for f in ('qty', 'basket_qty', 'basket_cost', 'exit_qty', 'exit_value', 'fees', 'add_filled', 'tp1_filled',
                  'closing'):
            non_negative(getattr(self, f), f'{p}.{f}')
        positive(self.basket_qty, p + '.basket_qty')
        req(CTX.add(self.qty, self.exit_qty) == self.basket_qty, p + '.qty', 'open + closed != opened')
        req(self.closing <= self.qty, p + '.closing', 'closing more than the position')
        req((self.closing > 0) == (self.close_reason is not None), p + '.close_reason', 'a close in flight has a reason')
        req((self.add is not None) <= (self.add_phase is AddPhase.WORKING), p + '.add', 'an add order outside WORKING')
        req(self.tp1_confirmed == (self.tp1_filled > 0), p + '.tp1_confirmed', 'confirmed <=> a TP1 quantity filled')
        stop_q = self.stop.qty if self.stop is not None else ZERO
        live = CTX.subtract(self.qty, self.closing)
        if self.stage is Stage.DONE:
            req(self.qty == 0 and all(getattr(self, f) is None for f in ('stop', 'add', 'tp1', 'tp2')), p + '.stage',
                'DONE = flat with every order released')
        if self.stage is Stage.ACTIVE:
            req(stop_q <= self.qty, p + '.stop', f'stop {stop_q} exceeds the position {self.qty}')
            req(stop_q >= live, p + '.stop', f'stop {stop_q} does not cover the position {live} not being closed')
        tq = sum((o.qty for o in (self.tp1, self.tp2) if o is not None), ZERO)
        req(tq <= live, p + '.tp1', f'targets {tq} above the position {live} not being closed')
