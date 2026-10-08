"""Fold: the Runner's state, rebuilt by folding the journal (restart = fold(journal.read())).

Nothing here is stored outside the journal. Every view is derived from NC-01 events:
  decisions   decision_id -> Decision
  intents     intent_id -> IntentView (the durable OrderIntent, its current state, results, final result)
  mode        EntriesMode / HoldKind / reasons from the last ModeChanged (ACTIVE before any)
  lots        one per ENTRY intent whose FINAL result executed > 0 (lot id = ids.lot_id(entry intent)); closing fills
              are the FINAL executions of the lot's PROTECT / CLOSE / REDUCE intents (owner_id = the lot)
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from decimal import Decimal

from newcore.domain import (Action, DecisionRecorded, EntriesMode, IntentRecorded, IntentState, IntentStateChanged,
                            ModeChanged, OrderIntent, OrderResult, Purpose, ReasonCode, ResultObserved, ResultPhase)
from newcore.domain.orders import TERMINAL

from . import ids

ZERO = Decimal(0)
OPEN_STATES = frozenset({IntentState.SUBMITTED, IntentState.WORKING, IntentState.UNKNOWN, IntentState.CANCELLING})


@dataclass
class IntentView:
    intent: OrderIntent                       # as recorded (state DURABLE)
    state: IntentState
    sent_at_ms: int | None = None
    results: list = field(default_factory=list)
    final: OrderResult | None = None

    @property
    def intent_id(self):
        return self.intent.intent_id

    @property
    def purpose(self):
        return self.intent.purpose

    @property
    def live(self):
        return self.state not in TERMINAL

    @property
    def executed(self):
        return self.final.executed_qty if self.final is not None else ZERO

    def current(self) -> OrderIntent:
        """The intent record with its current state (what a Portfolio holds)."""
        return dataclasses.replace(self.intent, state=self.state)


@dataclass(frozen=True, slots=True)
class ClosingFill:
    intent_id: str
    qty: Decimal
    price: Decimal
    at_ms: int
    result_id: str
    exchange_order_id: str
    reason: ReasonCode


@dataclass
class LotView:
    lot_id: str
    entry: IntentView
    symbol: str
    side: str
    opened_at_ms: int
    initial_qty: Decimal
    avg_price: Decimal
    closings: list = field(default_factory=list)              # ClosingFill
    protects: list = field(default_factory=list)              # IntentView (PROTECT, recorded order)
    closes: list = field(default_factory=list)                # IntentView (CLOSE / REDUCE, recorded order)

    @property
    def qty(self):
        q = self.initial_qty
        for c in self.closings:
            q -= c.qty
        return q

    @property
    def open(self):
        return self.qty > 0

    @property
    def live_stop(self):
        live = [p for p in self.protects if p.live]
        return live[-1] if live else None

    @property
    def closing(self):
        live = [c for c in self.closes if c.live]
        return live[-1] if live else None

    @property
    def closed_at_ms(self):
        return self.closings[-1].at_ms if self.closings and not self.open else None


class Fold:
    def __init__(self, account_id, portfolio_id):
        self.account_id, self.portfolio_id = account_id, portfolio_id
        self.decisions = {}
        self.pending_closes = {}                # lot id -> CLOSE decision whose intent is not recorded yet
        self.intents = {}
        self.by_client_id = {}
        self.client_ids_recorded = []           # every client id, in record order (duplicates would show here)
        self.mode = EntriesMode.ACTIVE
        self.hold = None
        self.mode_reasons = ()
        self.mode_since_ms = None
        self.first_at_ms = None
        self.last_sequence = 0

    @classmethod
    def replay(cls, account_id, portfolio_id, events):
        f = cls(account_id, portfolio_id)
        for ev in events:
            f.apply(ev)
        return f

    def apply(self, ev):
        if ev.sequence != self.last_sequence + 1:
            raise ValueError(f'fold: sequence {ev.sequence} after {self.last_sequence}')
        if self.first_at_ms is None:
            self.first_at_ms = ev.at_ms
        if isinstance(ev, DecisionRecorded):
            d = ev.decision
            self.decisions[d.decision_id] = d
            if d.action is Action.CLOSE and d.intents:
                self.pending_closes[d.subject_id] = d
        elif isinstance(ev, IntentRecorded):
            d = self.pending_closes.get(ev.intent.owner_id or '')
            if d is not None and d.intents[0].intent_id == ev.intent.intent_id:
                del self.pending_closes[ev.intent.owner_id]
            iv = IntentView(intent=ev.intent, state=ev.intent.state)
            self.intents[iv.intent_id] = iv
            for c in ev.intent.client_ids:
                self.by_client_id[c] = iv
                self.client_ids_recorded.append(c)
        elif isinstance(ev, IntentStateChanged):
            iv = self.intents[ev.intent_id]
            iv.state = ev.to_state
            if ev.to_state is IntentState.SUBMITTED and iv.sent_at_ms is None:
                iv.sent_at_ms = ev.at_ms
        elif isinstance(ev, ResultObserved):
            iv = self.intents[ev.result.intent_id]
            iv.results.append(ev.result)
            if ev.result.phase is ResultPhase.FINAL:
                iv.final = ev.result
        elif isinstance(ev, ModeChanged):
            self.mode, self.hold, self.mode_reasons, self.mode_since_ms = ev.to_mode, ev.to_hold, ev.reasons, ev.at_ms
        self.last_sequence = ev.sequence

    # ------------------------------------------------------------------------------------------------ derived
    def lots(self):
        """Every lot ever opened, in entry order (open and closed)."""
        out, by_id = [], {}
        for iv in self.intents.values():
            if iv.purpose is Purpose.ENTRY and iv.final is not None and iv.executed > 0:
                lot = LotView(lot_id=ids.lot_id(iv.intent_id), entry=iv, symbol=iv.intent.symbol,
                              side=str(iv.intent.side), opened_at_ms=iv.final.observed_at_ms, initial_qty=iv.executed,
                              avg_price=iv.final.avg_price)
                out.append(lot)
                by_id[lot.lot_id] = lot
        for iv in self.intents.values():
            lot = by_id.get(iv.intent.owner_id or '')
            if lot is None:
                continue
            (lot.protects if iv.purpose is Purpose.PROTECT else lot.closes).append(iv)
            if iv.final is not None and iv.executed > 0:
                reason = ReasonCode.EXIT_STOP if iv.purpose is Purpose.PROTECT else iv.intent.reason
                lot.closings.append(ClosingFill(intent_id=iv.intent_id, qty=iv.executed, price=iv.final.avg_price,
                                                at_ms=iv.final.observed_at_ms, result_id=iv.final.result_id,
                                                exchange_order_id=iv.final.exchange_order_id, reason=reason))
        for lot in out:
            lot.closings.sort(key=lambda c: c.at_ms)
        return out

    def open_lots(self):
        return [x for x in self.lots() if x.open]

    def live_intents(self):
        return [iv for iv in self.intents.values() if iv.live]

    def live_entries(self, symbol, side):
        return [iv for iv in self.live_intents() if iv.purpose is Purpose.ENTRY and iv.intent.symbol == symbol
                and str(iv.intent.side) == side]

    def entry_key(self, entry_iv):
        return self.decisions[entry_iv.intent.decision_id].key
