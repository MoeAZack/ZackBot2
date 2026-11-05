"""RT-02 venue evidence: NC-03 ``VenuePort`` reads, through NC-04, folded into one REC-02 ``VenueSnapshot``.

One generation reads, all as ``Action.RECONCILE`` read work:

* ``POSITIONS`` for every symbol (one request);
* ``OPEN_ORDERS`` per watched symbol: the configured symbols plus every symbol the journal has a live order or a
  booked position on (NC-04 refuses a no-symbol open-orders read);
* ``FILLS`` for every live journal order that has an exchange order id, so a resting stop that executed is seen.

``assemble`` is fail-closed: every read must be ``ReadKind.OK``, and every venue record must map exactly onto a REC-02
record, or it raises (the runtime holds the account with ``evidence_invalid``).  Records it cannot represent exactly
(a partly filled open order, a closePosition order, an order type other than market / limit / stop-market, a foreign
client id REC-02 cannot validate) raise rather than being dropped.  Pages carry the binding of the journal's last
CONFIRMED ``BindingChanged``, so REC-02 also checks the evidence belongs to the confirmed account.
"""
from __future__ import annotations

from decimal import Decimal

from newcore.account_context import FillsRequest, OpenOrdersRequest, Operation, PositionsRequest, VenueWork, WorkState
from newcore.domain import Action, BindingChanged, IntentState, Purpose, Side
from newcore.domain.account import BindingState
from newcore.ports.values import check_symbol
from newcore.ports.venue import ReadKind, ReadOutcome
from newcore.ports.venue import VenueFill as PortFill
from newcore.ports.venue import VenueOrder as PortOrder
from newcore.ports.venue import VenuePosition as PortPosition
from newcore.reconciliation import (EvidencePage, SnapshotComponent, TradeEffect, VenueOrder, VenueOrderKind,
                                    VenueOrderState, VenuePosition, VenueSnapshot, VenueTrade)

MAX_SYMBOLS = 8
MAX_FILL_READS = 16
_KIND = {'MARKET': VenueOrderKind.MARKET, 'LIMIT': VenueOrderKind.LIMIT, 'STOP_MARKET': VenueOrderKind.STOP_MARKET}
_STATE = {'NEW': VenueOrderState.OPEN}
_ASKED = frozenset({IntentState.SUBMITTED, IntentState.WORKING, IntentState.UNKNOWN, IntentState.CANCELLING})


class EvidenceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def confirmed_binding(journal):
    """The binding of the journal's last BindingChanged, if that left it CONFIRMED with a confirmation; else raise."""
    binding = state = confirmation = None
    for ev in journal.read():
        if isinstance(ev, BindingChanged):
            binding, state = ev.binding, ev.to_state
            if ev.confirmation is not None:
                confirmation = ev.confirmation
    if state is not BindingState.CONFIRMED or confirmation is None or confirmation.new_key_digest != binding.key_digest:
        raise EvidenceError('binding_not_confirmed')
    return binding


class VenueEvidence:
    """A concrete RT-01 ``EvidencePort``.  The runtime hands it the account's journal at boot (``bind_journal``)."""

    def __init__(self, *, symbols: tuple[str, ...]):
        if type(symbols) is not tuple or not 1 <= len(symbols) <= MAX_SYMBOLS or len(set(symbols)) != len(symbols):
            raise EvidenceError('symbols_invalid')
        for symbol in symbols:
            check_symbol(symbol, 'symbols')
        self.symbols = symbols
        self._journal = None
        self._plans: dict[int, dict] = {}

    def bind_journal(self, journal) -> None:
        if self._journal is not None:
            raise EvidenceError('already_bound')
        self._journal = journal

    # ------------------------------------------------------------------------------------------------ EvidencePort
    def read_work(self, account_id, generation, now_mono, deadline_mono):
        if self._journal is None or self._journal.account_id != account_id:
            raise EvidenceError('journal_unbound')
        state = self._journal.state()
        orders = tuple(v for v in state.intents
                       if v.state in _ASKED and v.last_result is not None and v.last_result.exchange_order_id)
        watched = list(self.symbols)
        for symbol in [v.intent.symbol for v in state.intents] + [s for (s, _side), net in state.booked if net]:
            if symbol not in watched:
                watched.append(symbol)
        if len(watched) > MAX_SYMBOLS or len(orders) > MAX_FILL_READS:
            raise EvidenceError('evidence_too_wide')
        prefix = f'ev{generation}'

        def work(suffix, operation, payload):
            return VenueWork(work_id=f'{prefix}-{suffix}', action=Action.RECONCILE, operation=operation,
                             payload=payload, enqueued_at_ms=now_mono, deadline_ms=deadline_mono)

        rows = [work('p', Operation.POSITIONS, PositionsRequest())]
        rows += [work(f'o{i}', Operation.OPEN_ORDERS, OpenOrdersRequest(symbol=s)) for i, s in enumerate(watched)]
        rows += [work(f'f{i}', Operation.FILLS, FillsRequest(symbol=v.intent.symbol,
                                                             exchange_order_id=v.last_result.exchange_order_id))
                 for i, v in enumerate(orders)]
        self._plans[generation] = {
            'positions': rows[0].work_id,
            'orders': tuple(r.work_id for r in rows[1:1 + len(watched)]),
            'fills': tuple((r.work_id, v.intent) for r, v in zip(rows[1 + len(watched):], orders)),
        }
        for old in [g for g in self._plans if g < generation - 4]:
            del self._plans[old]
        return tuple(rows)

    def assemble(self, account_id, generation, results):
        if self._journal is None or self._journal.account_id != account_id:
            raise EvidenceError('journal_unbound')
        plan = self._plans.pop(generation, None)
        if plan is None:
            raise EvidenceError('generation_unknown')
        by_id = {}
        for r in results:
            if r.state is not WorkState.COMPLETED or type(r.value) is not ReadOutcome or r.value.kind is not ReadKind.OK:
                raise EvidenceError('read_not_ok')
            by_id[r.work_id] = r.value
        expected = {plan['positions'], *plan['orders'], *(w for w, _i in plan['fills'])}
        if set(by_id) != expected:
            raise EvidenceError('results_mismatch')
        binding = confirmed_binding(self._journal)

        def pages(component, reads, convert):
            reads = sorted(reads, key=lambda o: o.observed_at_ms)
            return tuple(EvidencePage(component, account_id, binding, generation, n, o.observed_at_ms,
                                      n == len(reads), tuple(convert(o)))
                         for n, o in enumerate(reads, 1))

        positions = by_id[plan['positions']]
        out = list(pages(SnapshotComponent.POSITIONS, (positions,), _positions))
        out += pages(SnapshotComponent.OPEN_ORDERS, [by_id[w] for w in plan['orders']], _orders)
        fills = [(by_id[w], intent) for w, intent in plan['fills']]
        if fills:
            observed = sorted(fills, key=lambda x: x[0].observed_at_ms)
            out += [EvidencePage(SnapshotComponent.USER_TRADES, account_id, binding, generation, n,
                                 read.observed_at_ms, n == len(observed), _trades(read, intent))
                    for n, (read, intent) in enumerate(observed, 1)]
        else:
            # No live order has an exchange id: there is no owned execution to report since the last journaled result.
            out.append(EvidencePage(SnapshotComponent.USER_TRADES, account_id, binding, generation, 1,
                                    positions.observed_at_ms, True, ()))
        return VenueSnapshot(tuple(out))


def _positions(read: ReadOutcome):
    out = []
    for row in read.value:
        if type(row) is not PortPosition:
            raise EvidenceError('position_record')
        if row.qty > 0:
            out.append(VenuePosition(row.symbol, Side(row.side), row.qty))
    return out


def _orders(read: ReadOutcome):
    out = []
    for row in read.value:
        if type(row) is not PortOrder or row.close_position or row.order_type not in _KIND or row.status not in _STATE:
            raise EvidenceError('order_record')
        out.append(VenueOrder(row.exchange_order_id, row.ref.client_id, row.ref.symbol, Side(row.position_side),
                              _KIND[row.order_type], _STATE[row.status], row.reduce, row.qty, Decimal('0')))
    return out


def _trades(read: ReadOutcome, intent):
    effect = TradeEffect.ADD if intent.purpose in (Purpose.ENTRY, Purpose.ADD) else TradeEffect.REDUCE
    out = []
    for row in read.value:
        if type(row) is not PortFill or (row.symbol, row.position_side) != (intent.symbol, intent.side.value):
            raise EvidenceError('fill_record')
        out.append(VenueTrade(row.trade_id, intent.client_order_id, row.symbol, Side(row.position_side), effect,
                              row.qty, row.price, row.at_ms))
    return tuple(out)


__all__ = ['EvidenceError', 'MAX_FILL_READS', 'MAX_SYMBOLS', 'VenueEvidence', 'confirmed_binding']
