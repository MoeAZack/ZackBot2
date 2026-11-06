"""TNET-02 concrete ``HarnessAdapter`` for the frozen TNET-01 twelve-scenario matrix.

All effects go through injected collaborators:

* ``context``  NC-04 ``AccountContext`` (budgets, protection lane, 429/418 rate blocks) wrapping the #291 venue;
* ``venue``    the same #291 ``VenuePort`` (used directly only for the one all-symbol open-order isolation read,
               which NC-04 refuses by design because it has no symbol);
* ``budget``   the NC-04 ``AccountBudget`` behind ``context`` (state evidence and the audited release after a
               *synthetic* 418 only);
* ``faults``   the case-scoped ``FaultingSender`` the venue sends through;
* ``account`` / ``filters`` / ``journals`` / ``clock`` / ``sleeper``  TNET-02 ports.

NC-06 ``risk.evaluate`` authorizes every risk-increasing order, NC-07 ``management.evaluate`` decides target,
partial and DCA actions, REC-02 ``plan_reconciliation`` decides every "reconciled" claim.  Nothing here reads
credentials, opens sockets or touches the filesystem except through those injected objects.  Errors raised to the
kernel are ``AdapterError`` with a closed code (the kernel never forwards text anyway).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from newcore import management as M
from newcore import risk as R
from newcore.account_context import (AccountBudget, AccountContext, AdmissionRefused, FillsRequest,
                                     OpenOrdersRequest, Operation, PositionsRequest, VenueWork, WorkState)
from newcore.domain.account import Account, Environment
from newcore.domain.decision import Action
from newcore.domain.orders import IntentState, Purpose, Side
from newcore.ports import keys as K
from newcore.ports.rate_blocks import RateBlockState
from newcore.ports.venue import (MarketOrder, OrderOutcome, OrderRef, OutcomeKind, ReadKind, StopOrder, VenuePort)
from newcore.reconciliation import (DurableState, EvidencePage, LocalOrder, LocalPosition, PlannerConfig,
                                    PlanStatus as RecStatus, ReconciliationPlan, SnapshotComponent,
                                    VenueOrder as RecOrder, VenueOrderKind, VenueOrderState,
                                    VenuePosition as RecPosition, VenueSnapshot, plan_reconciliation)
from newcore.testnet_harness import (MAX_AGGREGATE_NOTIONAL, BindingEvidence, CleanupContext, CleanupEvidence,
                                     FinalTruth, HarnessBinding, RateLimitEvidence, RateLimitKind, ScenarioContext,
                                     ScenarioEvidence, ScenarioKind, SetupEvidence)

from .faults import FaultingSender, FaultKind, client_id_of
from .ports import AccountPort, CaseJournal, Clock, FiltersPort, JournalOpener, JournalRecord, RecordKind, Sleeper


ZERO = Decimal("0")
ONE = Decimal("1")
STRATEGY_ID = "tnet02"
BOOK_ID = "harness"
_TERMINAL = frozenset({IntentState.FILLED, IntentState.CANCELLED, IntentState.REJECTED, IntentState.NOT_SENT})


class AdapterError(RuntimeError):
    """Closed failure code; never contains venue, credential or exception text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _hold(code: str) -> None:
    raise AdapterError(code) from None


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """Frozen run parameters.  Offsets are fractions of the read mark price."""

    entry_notional_fraction: Decimal = Decimal("0.3")
    protective_offset: Decimal = Decimal("0.01")
    trigger_offset: Decimal = Decimal("0.0005")
    notional_buffer: Decimal = Decimal("1.02")
    poll_ms: int = 1000
    settle_polls: int = 10
    work_timeout_ms: int = 30_000
    rate_retry_after_ms: int = 5_000
    max_snapshot_age_s: int = 30
    trigger_wait_ms: int = 8 * 60 * 1000
    # bounded_dca waits for a real adverse move, so it gets the longest wait the case deadline allows, minus a margin
    # kept for the add, the stop swap, the close and the reconciliation that follow.
    dca_wait_ms: int = 9 * 60 * 1000
    dca_finish_margin_ms: int = 60_000

    def __post_init__(self) -> None:
        for name in ("entry_notional_fraction", "protective_offset", "trigger_offset"):
            value = getattr(self, name)
            if type(value) is not Decimal or not value.is_finite() or not ZERO < value < Decimal("0.5"):
                _hold(f"config.{name}")
        if type(self.notional_buffer) is not Decimal or not ONE <= self.notional_buffer <= Decimal("1.2"):
            _hold("cfg.notional_buffer")
        if self.trigger_offset >= self.protective_offset:
            _hold("cfg.offsets")
        for name, low, high in (("poll_ms", 1, 60_000), ("settle_polls", 1, 120), ("work_timeout_ms", 1, 60_000),
                                ("rate_retry_after_ms", 1, 60_000), ("max_snapshot_age_s", 1, 300),
                                ("trigger_wait_ms", 1, 9 * 60 * 1000), ("dca_wait_ms", 1, 9 * 60 * 1000),
                                ("dca_finish_margin_ms", 1, 5 * 60 * 1000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                _hold(f"config.{name}")


@dataclass(slots=True)
class _Order:
    intent_id: str
    client_id: str
    route: str
    purpose: Purpose
    symbol: str
    side: Side
    requested_qty: Decimal
    stop_price: Decimal | None
    state: IntentState = IntentState.DURABLE
    executed_qty: Decimal = ZERO
    avg_price: Decimal | None = None
    exchange_order_id: str | None = None

    @property
    def ref(self) -> OrderRef:
        return OrderRef(symbol=self.symbol, client_id=self.client_id, route=self.route)


@dataclass(slots=True)
class _Case:
    context: ScenarioContext
    journal: CaseJournal | None
    boot_id: str
    orders: list[_Order] = field(default_factory=list)
    position: Decimal = ZERO
    orders_placed: int = 0
    peak_notional: Decimal = ZERO
    ordinals: dict[Purpose, int] = field(default_factory=dict)
    journal_sequence: int = 0
    marks: list[Decimal] = field(default_factory=list)
    generation: int = 0
    rate_latch_synthetic: bool = False
    leverage: int = 0
    trusted: bool = True

    @property
    def symbol(self) -> str:
        return self.context.case.symbol

    @property
    def side(self) -> Side:
        return self.context.case.side


@dataclass(frozen=True, slots=True)
class _CleanupMemo:
    evidence: CleanupEvidence
    flat: bool


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _ceil(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def _id(prefix: str, *parts: str) -> str:
    raw = "\x00".join(parts).encode("ascii")
    return prefix + "_" + hashlib.sha256(b"zackbot.tnet02.v1\x00" + raw).hexdigest()[:32]


class RealTestnetAdapter:
    """The concrete ``newcore.testnet_harness.HarnessAdapter`` (structural protocol)."""

    __test__ = False

    def __init__(self, *, venue: VenuePort, context: AccountContext, budget: AccountBudget, faults: FaultingSender,
                 account: AccountPort, filters: FiltersPort, journals: JournalOpener, clock: Clock,
                 monotonic: Clock, sleeper: Sleeper, binding_digest: str, config: HarnessConfig = HarnessConfig()):
        for name, value in (("venue", venue), ("account", account), ("filters", filters), ("journals", journals)):
            if value is None:
                _hold(f"adapter.{name}")
        if type(context) is not AccountContext or type(budget) is not AccountBudget:
            _hold("adapter.context")
        if type(faults) is not FaultingSender:
            _hold("adapter.faults")
        if not callable(clock) or not callable(monotonic) or not callable(sleeper):
            _hold("adapter.clock")
        if type(config) is not HarnessConfig:
            _hold("adapter.config")
        if type(binding_digest) is not str or len(binding_digest) != 16:
            _hold("adapter.binding_digest")
        self._venue, self._context, self._budget, self._faults = venue, context, budget, faults
        self._account_port, self._filters, self._journals = account, filters, journals
        self._clock, self._mono, self._sleep = clock, monotonic, sleeper
        self._digest, self._cfg = binding_digest, config
        self._account: Account | None = None
        self._symbols: tuple[str, ...] = ()
        self._case: _Case | None = None
        self._cleanups: dict[str, _CleanupMemo] = {}
        self._halted = False
        self._work_seq = 0
        self._last_observed = 1
        self._aggregate_peak = ZERO
        self._notes: list[dict] = []

    # ------------------------------------------------------------------------------------------------ plumbing
    def _now(self) -> int:
        value = self._clock()
        if type(value) is not int or value < 1:
            _hold("clock.invalid")
        return value

    def _run(self, action: Action, operation: Operation, payload: Any):
        self._work_seq += 1
        now = self._mono()
        work = VenueWork(work_id=f"tnet02-{self._work_seq}", action=action, operation=operation, payload=payload,
                         enqueued_at_ms=now, deadline_ms=now + self._cfg.work_timeout_ms)
        try:
            self._context.submit(work)
        except AdmissionRefused:
            _hold("work.admission_refused")
        result = self._context.run_next()
        if result is None or result.work_id != work.work_id:
            _hold("work.sequence")
        return result

    def _read_positions(self, symbol: str | None, action: Action = Action.RECONCILE) -> dict[tuple[str, Side], Decimal]:
        result = self._run(action, Operation.POSITIONS, PositionsRequest(symbol=symbol))
        if result.state is not WorkState.COMPLETED or result.value.kind is not ReadKind.OK:
            _hold("read.positions")
        out: dict[tuple[str, Side], Decimal] = {}
        for row in result.value.value:
            key = (row.symbol, Side(row.side))
            if key in out:
                _hold("read.positions_duplicate")
            if row.qty > 0:
                out[key] = row.qty
        self._observe(result.value.observed_at_ms)
        return out

    def _read_orders(self, symbol: str, action: Action = Action.RECONCILE):
        result = self._run(action, Operation.OPEN_ORDERS, OpenOrdersRequest(symbol=symbol))
        if result.state is not WorkState.COMPLETED or result.value.kind is not ReadKind.OK:
            _hold("read.open_orders")
        self._observe(result.value.observed_at_ms)
        return result.value.value

    def _observe(self, at_ms: int) -> None:
        if at_ms < self._last_observed:
            _hold("clock.backward")
        self._last_observed = at_ms

    def _facts(self, symbols: tuple[str, ...]):
        try:
            return self._account_port.facts(symbols)
        except Exception:
            _hold("read.account_facts")

    def _mark(self, case: _Case) -> Decimal:
        facts = self._facts((case.symbol,))
        mark = facts.symbol(case.symbol).mark_price
        case.marks.append(mark)
        self._track_notional(case, case.position * mark)
        return mark

    def _track_notional(self, case: _Case, notional: Decimal) -> None:
        case.peak_notional = max(case.peak_notional, notional)
        self._aggregate_peak = max(self._aggregate_peak, notional)

    # ------------------------------------------------------------------------------------------------ journal
    def _journal(self, case: _Case, record_kind: RecordKind, order: _Order | None = None) -> None:
        if case.journal is None:
            _hold("journal.closed")
        case.journal_sequence += 1
        if order is None:
            record = JournalRecord(RecordKind.POSITION, case.journal_sequence, case.context.case.case_id, case.boot_id,
                                   case.symbol, case.side, None, None, None, None, None, case.position, case.position,
                                   None, None)
        else:
            record = JournalRecord(RecordKind.ORDER, case.journal_sequence, case.context.case.case_id, case.boot_id,
                                   order.symbol, order.side, order.intent_id, order.client_id, order.route,
                                   order.purpose, order.state, order.requested_qty, order.executed_qty,
                                   order.exchange_order_id, order.stop_price)
        try:
            case.journal.append(record)
        except Exception:
            _hold("journal.append")

    def _set(self, case: _Case, order: _Order, state: IntentState) -> None:
        order.state = state
        self._journal(case, RecordKind.ORDER, order)

    # ------------------------------------------------------------------------------------------------ orders
    def _new_order(self, case: _Case, purpose: Purpose, route: str, qty: Decimal,
                   stop_price: Decimal | None) -> _Order:
        entry = case.context.synthetic_intent_id
        if purpose is Purpose.ENTRY and not any(o.purpose is Purpose.ENTRY for o in case.orders):
            intent = entry
        else:
            ordinal = case.ordinals.get(purpose, 0)
            case.ordinals[purpose] = ordinal + 1
            intent = K.derive_child_intent_id(case.context.account_id, entry, purpose, ordinal)
        order = _Order(intent, K.client_id_for(intent, route), route, purpose, case.symbol, case.side, qty,
                       stop_price)
        case.orders.append(order)
        self._journal(case, RecordKind.ORDER, order)
        return order

    def _guard_orders(self, case: _Case, extra: int = 1) -> None:
        if case.orders_placed + extra > case.context.case.max_orders:
            _hold("bounds.orders")

    def _guard_notional(self, case: _Case, add_qty: Decimal, mark: Decimal) -> None:
        projected = (case.position + add_qty) * mark * self._cfg.notional_buffer
        if projected > case.context.case.max_notional or projected > MAX_AGGREGATE_NOTIONAL:
            _hold("bounds.notional")

    def _dispatched(self, case: _Case, result) -> bool:
        sent = result.state in (WorkState.COMPLETED, WorkState.UNKNOWN)
        if sent:
            case.orders_placed += 1
        return sent

    def _apply_fill(self, case: _Case, order: _Order, executed: Decimal, avg: Decimal | None) -> None:
        delta = executed - order.executed_qty
        if delta < 0:
            _hold("fill.regressed")
        if delta == 0:
            return
        order.executed_qty = executed
        if avg is not None:
            order.avg_price = avg
        if order.purpose in (Purpose.ENTRY, Purpose.ADD):
            case.position += delta
            if avg is not None:
                self._track_notional(case, case.position * avg)
        else:
            if delta > case.position:
                _hold("fill.reduce_exceeds_position")
            case.position -= delta
        self._journal(case, RecordKind.POSITION)

    def _absorb(self, case: _Case, order: _Order, outcome: OrderOutcome) -> bool:
        """Apply one typed outcome; True when the order reached a terminal state."""
        if outcome.ref.client_id != order.client_id:
            _hold("outcome.echo")
        if outcome.exchange_order_id is not None and order.route == "classic":
            order.exchange_order_id = outcome.exchange_order_id
        kind = outcome.kind
        if kind is OutcomeKind.FINAL:
            if order.route == "algo" and order.exchange_order_id is None:
                order.exchange_order_id = outcome.exchange_order_id
            self._apply_fill(case, order, outcome.executed_qty, outcome.avg_price)
            if order.executed_qty == order.requested_qty:
                state = IntentState.FILLED
            elif order.executed_qty == 0 and outcome.status == "REJECTED":
                state = IntentState.REJECTED
            else:
                state = IntentState.CANCELLED
            self._set(case, order, state)
            return True
        if kind is OutcomeKind.REJECTED:
            if order.state in (IntentState.SUBMITTED, IntentState.DURABLE):
                self._set(case, order, IntentState.REJECTED)
                return True
            return False
        if kind is OutcomeKind.KNOWN:
            if order.route == "algo" and outcome.detail != "algo_triggered" and order.exchange_order_id is None:
                order.exchange_order_id = outcome.exchange_order_id
            if order.state is not IntentState.WORKING:
                self._set(case, order, IntentState.WORKING)
            return False
        if kind is OutcomeKind.ACKNOWLEDGED:
            if order.state is not IntentState.WORKING:
                self._set(case, order, IntentState.WORKING)
            return False
        if order.state is not IntentState.UNKNOWN:
            self._set(case, order, IntentState.UNKNOWN)
        return False

    def _query(self, case: _Case, order: _Order, action: Action = Action.RECONCILE) -> OrderOutcome | None:
        result = self._run(action, Operation.QUERY, order.ref)
        if result.state is not WorkState.COMPLETED:
            return None
        return result.value

    def _settle_market(self, case: _Case, order: _Order) -> None:
        for _ in range(self._cfg.settle_polls):
            if order.state in _TERMINAL:
                return
            self._sleep(self._cfg.poll_ms)
            outcome = self._query(case, order)
            if outcome is None or outcome.kind is OutcomeKind.UNKNOWN:
                continue
            if outcome.kind is OutcomeKind.NOT_FOUND:
                continue
            self._absorb(case, order, outcome)
        if order.state not in _TERMINAL:
            _hold("order.unsettled")

    def _market(self, case: _Case, purpose: Purpose, qty: Decimal, action: Action) -> _Order:
        reduce = purpose not in (Purpose.ENTRY, Purpose.ADD)
        self._guard_orders(case)
        if not reduce:
            self._guard_notional(case, qty, self._mark(case))
        elif qty > case.position:
            _hold("bounds.reduce_qty")
        order = self._new_order(case, purpose, "classic", qty, None)
        self._set(case, order, IntentState.SUBMITTED)
        payload = MarketOrder(ref=order.ref, position_side=case.side.value, qty=qty, reduce=reduce)
        result = self._run(action, Operation.SUBMIT_MARKET, payload)
        if not self._dispatched(case, result):
            self._set(case, order, IntentState.NOT_SENT)
            _hold("order.refused_before_send")
        if result.state is WorkState.COMPLETED:
            self._absorb(case, order, result.value)
        elif order.state is not IntentState.UNKNOWN:
            self._set(case, order, IntentState.UNKNOWN)
        if order.state is IntentState.REJECTED:
            _hold("order.rejected")
        self._settle_market(case, order)
        return order

    def _stop(self, case: _Case, qty: Decimal, stop_price: Decimal) -> _Order:
        self._guard_orders(case)
        order = self._new_order(case, Purpose.PROTECT, "algo", qty, stop_price)
        self._set(case, order, IntentState.SUBMITTED)
        payload = StopOrder(ref=order.ref, position_side=case.side.value, qty=qty, stop_price=stop_price)
        result = self._run(Action.PROTECT, Operation.SUBMIT_STOP, payload)
        if not self._dispatched(case, result):
            self._set(case, order, IntentState.NOT_SENT)
            _hold("stop.refused_before_send")
        if result.state is WorkState.COMPLETED:
            self._absorb(case, order, result.value)
        elif order.state is not IntentState.UNKNOWN:
            self._set(case, order, IntentState.UNKNOWN)
        for _ in range(self._cfg.settle_polls):
            if order.state is IntentState.WORKING and order.exchange_order_id is not None:
                return order
            if order.state in _TERMINAL:
                break
            self._sleep(self._cfg.poll_ms)
            outcome = self._query(case, order, Action.PROTECT)
            if outcome is not None and outcome.kind not in (OutcomeKind.UNKNOWN, OutcomeKind.NOT_FOUND):
                self._absorb(case, order, outcome)
        _hold("stop.unconfirmed")

    def _cancel(self, case: _Case, order: _Order, action: Action = Action.PROTECT) -> None:
        if order.state in _TERMINAL:
            return
        self._set(case, order, IntentState.CANCELLING)
        result = self._run(action, Operation.CANCEL, order.ref)
        if result.state is WorkState.COMPLETED and result.value.kind is OutcomeKind.FINAL:
            self._absorb(case, order, result.value)
        for _ in range(self._cfg.settle_polls):
            if order.state in _TERMINAL:
                return
            outcome = self._query(case, order, action)
            if outcome is not None and outcome.kind is OutcomeKind.FINAL:
                self._absorb(case, order, outcome)
            elif outcome is not None and outcome.kind is OutcomeKind.KNOWN and outcome.detail == "algo_triggered":
                self._absorb_trigger(case, order, outcome)
                if order.state not in _TERMINAL:
                    self._sleep(self._cfg.poll_ms)  # child order still settling: wait, never spin the poll budget
            else:
                self._sleep(self._cfg.poll_ms)
        if order.state not in _TERMINAL:
            _hold("cancel.unconfirmed")

    def _absorb_trigger(self, case: _Case, order: _Order, outcome: OrderOutcome) -> None:
        """A triggered algo stop: executed quantity comes only from the child order's exchange fills."""
        child = outcome.exchange_order_id
        result = self._run(Action.RECONCILE, Operation.FILLS, FillsRequest(symbol=case.symbol, exchange_order_id=child))
        if result.state is not WorkState.COMPLETED or result.value.kind is not ReadKind.OK:
            return
        fills = result.value.value
        if any(f.position_side != case.side.value for f in fills):
            _hold("fill.side")
        executed = sum((f.qty for f in fills), ZERO)
        if executed == 0:
            return
        notional = sum((f.qty * f.price for f in fills), ZERO)
        self._apply_fill(case, order, executed, notional / executed)
        if executed == order.requested_qty:
            self._set(case, order, IntentState.FILLED)
        elif case.position == 0:
            # the child closed everything that was open; the unexecuted remainder had nothing left to reduce
            self._set(case, order, IntentState.CANCELLED)
        # otherwise the child is still filling: the stop stays WORKING and callers poll again or HOLD

    def _working_stops(self, case: _Case) -> list[_Order]:
        return [o for o in case.orders if o.purpose is Purpose.PROTECT and o.state is IntentState.WORKING]

    # ------------------------------------------------------------------------------------------------ NC-06
    def _filters_for(self, case: _Case):
        try:
            return self._filters.filters(case.symbol)
        except Exception:
            _hold("read.filters")

    def _authorize(self, case: _Case, action: R.RiskAction, entry: Decimal, stop: Decimal,
                   notional: Decimal) -> R.RiskDecision:
        filters = self._filters_for(case)
        facts = self._facts((case.symbol,))
        if not facts.hedge_mode:
            _hold("account.hedge_mode")
        leverage = Decimal(case.context.case.leverage)
        env = R.Environment.TESTNET
        account_id = case.context.account_id
        side = R.PositionSide(case.side.value)
        distance = abs(entry - stop)
        exposures = ()
        if case.position > 0:
            exposures = (R.Exposure(symbol=case.symbol, side=side, strategy_id=STRATEGY_ID, book_id=BOOK_ID,
                                    quantity=case.position, notional=case.position * entry,
                                    risk=case.position * distance),)
        case.generation += 1
        gen = case.generation
        evaluation = R.RiskEvaluation(
            account=R.AccountRiskSnapshot(account_id=account_id, environment=env, generation=gen, confirmed=True,
                                          fresh=True, hedge_mode=facts.hedge_mode, equity=facts.equity,
                                          available_margin=facts.available_margin, reserved_capital=ZERO),
            portfolio=R.PortfolioRiskSnapshot(account_id=account_id, generation=gen, fresh=True,
                                              exposures=exposures, reservations=()),
            policy=R.RiskPolicySnapshot(
                policy_id="tnet02_policy_v1", account_id=account_id, environment=env, generation=gen, fresh=True,
                capital_cap=case.context.case.max_notional, max_trade_risk_fraction=ONE,
                max_portfolio_risk_fraction=ONE, max_symbol_side_risk_fraction=ONE, max_gross_multiple=ONE,
                max_net_multiple=ONE, max_symbol_fraction=ONE, max_symbol_side_fraction=ONE, max_leverage=leverage,
                allocations=(R.Allocation(strategy_id=STRATEGY_ID, book_id=BOOK_ID, notional_fraction=ONE,
                                          risk_fraction=ONE),),
                correlation_map=(R.CorrelationAssignment(symbol=case.symbol, bucket="tnet02"),),
                correlation_caps=(R.CorrelationCap(bucket="tnet02", exposure_fraction=ONE),)),
            filters=R.VenueRiskFilters(
                filter_id="tnet02_" + filters.source_sha256[:16], symbol=case.symbol, generation=gen, fresh=True,
                verified=True, step_size=filters.step_size, tick_size=filters.tick_size,
                min_quantity=filters.min_quantity, max_quantity=filters.max_quantity,
                min_notional=filters.min_notional, max_notional=case.context.case.max_notional,
                max_leverage=leverage),
            request=R.RiskRequest(
                request_id=_id("req", case.context.authorization_id, str(gen)), account_id=account_id,
                environment=env, account_generation=gen, portfolio_generation=gen, policy_generation=gen,
                filter_generation=gen, action=action, strategy_id=STRATEGY_ID, book_id=BOOK_ID, symbol=case.symbol,
                side=side, entry_price=entry, stop_price=stop, requested_risk=notional * distance / entry,
                requested_leverage=leverage),
            followers=())
        return R.evaluate(evaluation)

    def _prices(self, case: _Case, mark: Decimal, adverse: Decimal) -> tuple[Decimal, Decimal]:
        tick = self._filters_for(case).tick_size
        entry = _floor(mark, tick)
        if case.side is Side.LONG:
            stop = _floor(entry * (ONE - adverse), tick)
        else:
            stop = _ceil(entry * (ONE + adverse), tick)
        if stop <= 0 or stop == entry:
            _hold("prices.degenerate")
        return entry, stop

    # ------------------------------------------------------------------------------------------------ REC-02
    def _reconcile(self, case: _Case | None, symbol: str) -> ReconciliationPlan:
        before = self._last_observed
        positions = self._read_positions(symbol)
        orders = self._read_orders(symbol)
        now = self._now()
        account = self._account
        if account is None:
            _hold("bind.unconfirmed")
        rec_positions = tuple(RecPosition(s, side, qty) for (s, side), qty in sorted(positions.items(),
                                                                                       key=lambda x: (x[0][0], x[0][1].value)))
        rec_orders = []
        for row in orders:
            kind = VenueOrderKind.STOP_MARKET if "STOP" in row.order_type else (
                VenueOrderKind.LIMIT if row.order_type == "LIMIT" else VenueOrderKind.MARKET)
            if row.qty <= 0:
                _hold("rec.close_position_order")
            rec_orders.append(RecOrder(row.exchange_order_id, row.ref.client_id, row.ref.symbol,
                                       Side(row.position_side), kind, VenueOrderState.OPEN, row.reduce, row.qty, ZERO))
        local_positions, local_orders = (), ()
        if case is not None:
            if case.position > 0:
                local_positions = (LocalPosition(case.symbol, case.side, case.position),)
            local_orders = tuple(LocalOrder(o.intent_id, o.client_id, o.exchange_order_id, o.symbol, o.side,
                                            o.purpose, o.state, o.requested_qty, o.executed_qty)
                                 for o in case.orders if o.state is not IntentState.DURABLE)
        pages = []
        for component, records in ((SnapshotComponent.POSITIONS, rec_positions),
                                   (SnapshotComponent.OPEN_ORDERS, tuple(rec_orders)),
                                   (SnapshotComponent.USER_TRADES, ())):
            pages.append(EvidencePage(component, account.account_id, account.binding, 1, 1, now, True, records))
        state = DurableState(account, 1, before, local_positions, local_orders)
        plan = plan_reconciliation(state, VenueSnapshot(tuple(pages)), PlannerConfig(now, self._cfg.max_snapshot_age_s))
        self._last_observed = now
        return plan

    def _reconciled(self, case: _Case) -> tuple[bool, FinalTruth]:
        plan = self._reconcile(case, case.symbol)
        if plan.status is RecStatus.FLAT_CONFIRMED and case.position == 0:
            return True, FinalTruth.FLAT
        if plan.status is RecStatus.PROTECTED_CONFIRMED and case.position > 0:
            return True, FinalTruth.PROTECTED_RECONCILED
        return False, FinalTruth.HOLD

    # ------------------------------------------------------------------------------------------------ NC-07
    def _manage(self, case: _Case, *, entry: Decimal, stop: Decimal, stop_order: _Order,
                targets: tuple[M.TargetRule, ...], increases: tuple[M.RiskIncreaseRule, ...],
                initial_qty: Decimal) -> M.ManagementPlan:
        filters = self._filters_for(case)
        mark = self._mark(case)
        marks = case.marks
        case.generation += 1
        gen = case.generation
        side = M.PositionSide(case.side.value)
        max_add = max((r.requested_risk for r in increases), default=ZERO)
        evaluation = M.ManagementEvaluation(
            lot_generation=gen, policy_generation=gen, market_generation=gen, protection_generation=gen,
            strategy_exit_generation=gen, pending_generation=gen,
            lot=M.LotState(lot_id=K.derive_lot_id(case.context.account_id, case.context.synthetic_intent_id),
                           strategy_id=STRATEGY_ID, generation=gen, fresh=True, side=side, quantity=case.position,
                           initial_quantity=initial_qty, entry_price=entry, initial_stop=stop,
                           initial_risk_per_unit=abs(entry - stop), completed_target_levels=(),
                           completed_risk_levels=(), breakeven_applied=False, trail_applied=False,
                           completed_bars_held=0, add_count=0, cumulative_add_risk=ZERO),
            policy=M.ManagementPolicy(policy_id="tnet02_mgmt", version="v1", generation=gen, fresh=True,
                                      tick_size=filters.tick_size, quantity_step=filters.step_size,
                                      min_quantity=filters.min_quantity, targets=targets, preserve_runner=False,
                                      runner_quantity=ZERO, breakeven_trigger_r=None, breakeven_offset_ticks=ZERO,
                                      trail_trigger_r=None, trail_distance_r=None, max_holding_bars=None,
                                      max_add_count=len(increases), max_single_add_risk=max_add,
                                      max_cumulative_add_risk=max_add, risk_increases=increases),
            market=M.MarketObservation(observation_id=f"obs{gen}", generation=gen, fresh=True, completed=True,
                                       high=max(marks), low=min(marks), mark=mark, path_order=M.PathOrder.NONE,
                                       forced_exit_confirmed=False),
            protection=M.ProtectionState(protection_id=stop_order.client_id, generation=gen, fresh=True,
                                         status=M.ProtectionStatus.WORKING, confirmed_stop=stop_order.stop_price,
                                         confirmed_quantity=stop_order.requested_qty, confirmed_target_levels=()),
            strategy_exit=M.StrategyExitFact(version="v1", generation=gen, fresh=True, exit_requested=False),
            pending=M.PendingSummary(generation=gen, fresh=True, kind=M.PendingKind.NONE))
        return M.evaluate(evaluation)

    def _wait_for_plan(self, case: _Case, deadline_ms: int, **kwargs) -> M.ManagementPlan | None:
        while self._now() < deadline_ms:
            plan = self._manage(case, **kwargs)
            if plan.status is M.PlanStatus.ACTIONS:
                return plan
            if plan.status is not M.PlanStatus.HOLD or plan.reason is not M.PlanReason.NO_CHANGE:
                return None
            self._sleep(self._cfg.poll_ms)
        return None

    # ------------------------------------------------------------------------------------------------ binding
    def confirm_binding(self, expected: HarnessBinding) -> BindingEvidence:
        if type(expected) is not HarnessBinding:
            _hold("bind.expected")
        account = expected.account
        if account.binding.environment is not Environment.TESTNET:
            _hold("bind.environment")
        if self._digest != account.binding.key_digest:
            _hold("bind.key_digest")
        self._account, self._symbols = account, tuple(expected.symbols)
        positions = self._read_positions(None)
        classic_algo = self._venue.open_orders(None)
        if classic_algo.kind is not ReadKind.OK:
            _hold("bind.open_orders")
        facts = self._facts(self._symbols)
        if facts.hedge_mode != account.hedge_mode or facts.settlement_asset != account.binding.settlement_asset:
            _hold("bind.account_mode")
        isolated = not positions and not classic_algo.value
        return BindingEvidence(account.account_id, self._digest, None, account.binding.venue, Environment.TESTNET,
                               facts.settlement_asset, isolated, self._now())

    # ------------------------------------------------------------------------------------------------ setup
    def setup(self, context: ScenarioContext) -> SetupEvidence:
        if type(context) is not ScenarioContext:
            _hold("setup.context")
        if self._halted or self._account is None:
            _hold("setup.halted")
        if self._faults.armed:
            _hold("setup.fault_armed")
        case = context.case
        plan = self._reconcile(None, case.symbol)
        facts = self._facts((case.symbol,))
        leverage = facts.symbol(case.symbol).leverage
        if leverage > case.leverage:
            _hold("setup.leverage")
        try:
            journal = self._journals.open(case.case_id, context.boot_id, reopen=False)
        except Exception:
            _hold("setup.journal")
        self._case = _Case(context, journal, context.boot_id, leverage=leverage)
        flat = plan.status is RecStatus.FLAT_CONFIRMED
        return SetupEvidence(case.case_id, self._now(), self._digest, flat,
                             FinalTruth.FLAT if flat else FinalTruth.HOLD)

    # ------------------------------------------------------------------------------------------------ execute
    def execute(self, context: ScenarioContext) -> ScenarioEvidence:
        case = self._case
        if type(context) is not ScenarioContext or case is None or case.context != context:
            _hold("execute.context")
        started = self._now()
        flags: dict[str, Any] = dict(nc06=False, entry=False, protection=False, target=False, stop=False,
                                     partial=False, dca=0, cancel_replace=False, minimum=False, accepted=None,
                                     lost=None, reconciled=False, reopened=False, restart=None, rate=None,
                                     truth=FinalTruth.HOLD)
        kind = context.case.kind
        handler = {
            ScenarioKind.LONG_TARGET: self._run_target, ScenarioKind.SHORT_TARGET: self._run_target,
            ScenarioKind.LONG_STOP: self._run_stop, ScenarioKind.SHORT_STOP: self._run_stop,
            ScenarioKind.PARTIAL_CLOSE: self._run_partial, ScenarioKind.BOUNDED_DCA: self._run_dca,
            ScenarioKind.CANCEL_REPLACE_RACE: self._run_cancel_replace,
            ScenarioKind.LOST_ANSWER_RECONCILE: self._run_lost_answer,
            ScenarioKind.MINIMUM_REFUSAL: self._run_minimum, ScenarioKind.RESTART_OPEN_POSITION: self._run_restart,
            ScenarioKind.RATE_LIMIT_429: self._run_429, ScenarioKind.IP_BAN_418: self._run_418,
        }[kind]
        try:
            handler(case, flags)
        finally:
            if self._faults.disarm():
                flags["rate"] = None
                flags["truth"] = FinalTruth.HOLD
        return ScenarioEvidence(
            context.case.case_id, kind, started, self._now(), context.synthetic_intent_id, context.authorization_id,
            flags["nc06"], case.orders_placed, case.peak_notional, self._aggregate_peak,
            case.leverage, flags["entry"], flags["protection"], flags["target"], flags["stop"],
            flags["partial"], flags["dca"], flags["cancel_replace"], flags["minimum"], flags["accepted"],
            flags["lost"], flags["reconciled"], flags["reopened"], flags["restart"], flags["rate"], flags["truth"])

    def _open_protected(self, case: _Case, flags: dict, *, adverse: Decimal) -> tuple[Decimal, Decimal, _Order]:
        """NC-06-authorized market entry followed by a confirmed reduce-only protective stop."""
        mark = self._mark(case)
        entry, stop = self._prices(case, mark, adverse)
        notional = case.context.case.max_notional * self._cfg.entry_notional_fraction
        decision = self._authorize(case, R.RiskAction.ENTRY, entry, stop, notional)
        if type(decision) is not R.ApprovedDecision:
            _hold("nc06.refused")
        flags["nc06"] = True
        order = self._market(case, Purpose.ENTRY, decision.quantity, Action.ENTER)
        flags["entry"] = order.state is IntentState.FILLED and case.position == decision.quantity
        if not flags["entry"]:
            _hold("open.unconfirmed")
        stop_order = self._stop(case, case.position, stop)
        flags["protection"] = True
        return order.avg_price, stop, stop_order

    def _target_price(self, case: _Case, entry: Decimal) -> Decimal:
        tick = self._filters_for(case).tick_size
        if case.side is Side.LONG:
            return _ceil(entry * (ONE + self._cfg.trigger_offset), tick)
        return _floor(entry * (ONE - self._cfg.trigger_offset), tick)

    def _refresh_stops(self, case: _Case) -> None:
        """Fold any exchange-side stop trigger into local truth before a reconciliation claim."""
        for order in self._working_stops(case):
            outcome = self._query(case, order, Action.RECONCILE)
            if outcome is None or outcome.kind in (OutcomeKind.UNKNOWN, OutcomeKind.NOT_FOUND):
                _hold("refresh.stop_unknown")
            if outcome.kind is OutcomeKind.KNOWN and outcome.detail == "algo_triggered":
                self._absorb_trigger(case, order, outcome)
                if order.state is IntentState.WORKING:
                    _hold("refresh.trigger_unsettled")
            elif outcome.kind is OutcomeKind.FINAL:
                self._absorb(case, order, outcome)
                if order.state is not IntentState.FILLED:
                    # this harness never cancels a stop without settling it in _cancel; a stop the exchange ended any
                    # other way means protection was lost outside our control
                    _hold("refresh.stop_lost")

    def _finish_truth(self, case: _Case, flags: dict) -> None:
        self._refresh_stops(case)
        flags["reconciled"], flags["truth"] = self._reconciled(case)

    def _run_target(self, case: _Case, flags: dict, fraction: Decimal = ONE) -> None:
        entry, stop, stop_order = self._open_protected(case, flags, adverse=self._cfg.protective_offset)
        initial = case.position
        target = self._target_price(case, entry)
        rule = M.TargetRule(level_id="t1", trigger_price=target, reduce_fraction=fraction)
        plan = self._wait_for_plan(case, self._now() + self._cfg.trigger_wait_ms, entry=entry, stop=stop,
                                   stop_order=stop_order, targets=(rule,), increases=(), initial_qty=initial)
        action = None if plan is None else next((a for a in plan.actions if isinstance(a, M.ReduceAction)), None)
        if action is None:
            self._finish_truth(case, flags)
            return
        reduce = self._market(case, Purpose.CLOSE if action.remaining_quantity == 0 else Purpose.REDUCE,
                              action.quantity, Action.CLOSE if action.remaining_quantity == 0 else Action.REDUCE)
        done = reduce.state is IntentState.FILLED and case.position == action.remaining_quantity
        if fraction == ONE:
            flags["target"] = done and case.position == 0
            if case.position == 0:
                self._cancel(case, stop_order)
        else:
            if done and case.position > 0:
                replacement = self._stop(case, case.position, stop)
                self._cancel(case, stop_order)
                flags["partial"] = replacement.state is IntentState.WORKING and stop_order.state in _TERMINAL
        self._finish_truth(case, flags)

    def _run_partial(self, case: _Case, flags: dict) -> None:
        self._run_target(case, flags, Decimal("0.5"))

    def _run_stop(self, case: _Case, flags: dict) -> None:
        entry, stop, stop_order = self._open_protected(case, flags, adverse=self._cfg.trigger_offset)
        deadline = self._now() + self._cfg.trigger_wait_ms
        while self._now() < deadline and stop_order.state is IntentState.WORKING:
            self._sleep(self._cfg.poll_ms)
            self._mark(case)
            outcome = self._query(case, stop_order, Action.PROTECT)
            if outcome is not None and outcome.kind is OutcomeKind.KNOWN and outcome.detail == "algo_triggered":
                self._absorb_trigger(case, stop_order, outcome)
            elif outcome is not None and outcome.kind is OutcomeKind.FINAL:
                self._absorb(case, stop_order, outcome)
        flags["stop"] = stop_order.state is IntentState.FILLED and case.position == 0
        self._finish_truth(case, flags)

    def _run_dca(self, case: _Case, flags: dict) -> None:
        entry, stop, stop_order = self._open_protected(case, flags, adverse=self._cfg.protective_offset)
        initial = case.position
        tick = self._filters_for(case).tick_size
        self._mark(case)
        trigger = self._dca_trigger(case, entry, tick)
        add_notional = case.context.case.max_notional * self._cfg.entry_notional_fraction
        add_risk = add_notional * abs(entry - stop) / entry
        rule = M.RiskIncreaseRule(level_id="d1", kind=M.IncreaseKind.DCA, trigger_price=trigger,
                                  requested_risk=add_risk)
        deadline = min(self._now() + self._cfg.dca_wait_ms,
                       case.context.case_deadline_ms - self._cfg.dca_finish_margin_ms)
        plan = self._wait_for_plan(case, deadline, entry=entry, stop=stop,
                                   stop_order=stop_order, targets=(), increases=(rule,), initial_qty=initial)
        proposal = None if plan is None else next(
            (a for a in plan.actions if isinstance(a, M.RiskIncreaseProposal)), None)
        if proposal is None or not proposal.requires_nc06_authorization:
            self._note(case, "dca.no_trigger" if plan is None else "dca.no_proposal", trigger=trigger)
            self._finish_truth(case, flags)
            return
        mark = self._mark(case)
        add_entry = _floor(mark, tick)
        if not ((case.side is Side.LONG and stop < add_entry) or (case.side is Side.SHORT and stop > add_entry)):
            self._note(case, "dca.stop_crossed", trigger=trigger)
            self._finish_truth(case, flags)
            return
        decision = self._authorize(case, R.RiskAction.ADD, add_entry, stop, add_notional)
        if type(decision) is not R.ApprovedDecision:
            self._note(case, "dca.nc06_refused", trigger=trigger)
            self._finish_truth(case, flags)
            return
        add = self._market(case, Purpose.ADD, decision.quantity, Action.ADD)
        if add.state is IntentState.FILLED:
            replacement = self._stop(case, case.position, stop)
            self._cancel(case, stop_order)
            if replacement.state is IntentState.WORKING and stop_order.state in _TERMINAL:
                flags["dca"] = 1
        self._note(case, "dca.added" if flags["dca"] == 1 else "dca.add_unconfirmed", trigger=trigger)
        self._finish_truth(case, flags)

    def _dca_trigger(self, case: _Case, entry: Decimal, tick: Decimal) -> Decimal:
        """The DCA level is compared with polled MARK prices (the market low/high is taken over every mark this case
        has read), so it is set ``trigger_offset`` beyond the most adverse mark already seen, which includes the mark
        read just after entry.  No mark seen so far can satisfy it, so only a fresh adverse move fires it, and it must
        also be strictly adverse to the actual fill by at least one tick."""
        if case.side is Side.LONG:
            return min(_floor(min(case.marks) * (ONE - self._cfg.trigger_offset), tick), _floor(entry, tick) - tick)
        return max(_ceil(max(case.marks) * (ONE + self._cfg.trigger_offset), tick), _ceil(entry, tick) + tick)

    def _note(self, case: _Case, reason: str, **prices: Decimal) -> None:
        """Run diagnostics: why a scenario took the path it did.  Prices only, never ids or account data."""
        marks = case.marks
        self._notes.append({"kind": case.context.case.kind.value, "reason": reason, "side": case.side.value,
                            "symbol": case.symbol, "polls": len(marks),
                            "mark_low": str(min(marks)) if marks else None,
                            "mark_high": str(max(marks)) if marks else None,
                            **{name: str(value) for name, value in prices.items()}})

    @property
    def notes(self) -> tuple[dict, ...]:
        return tuple(dict(n) for n in self._notes)

    def _run_cancel_replace(self, case: _Case, flags: dict) -> None:
        _entry, stop, first = self._open_protected(case, flags, adverse=self._cfg.protective_offset)
        replacement = self._stop(case, case.position, stop)
        self._cancel(case, first)
        racing = self._run(Action.PROTECT, Operation.CANCEL, first.ref)
        late_cancel_safe = (racing.state is WorkState.COMPLETED
                            and racing.value.kind in (OutcomeKind.NOT_FOUND, OutcomeKind.FINAL, OutcomeKind.REJECTED))
        working = [row for row in self._read_orders(case.symbol, Action.PROTECT)
                   if row.ref.client_id in {o.client_id for o in case.orders}]
        flags["cancel_replace"] = (late_cancel_safe and first.state is IntentState.CANCELLED
                                   and replacement.state is IntentState.WORKING and len(working) == 1
                                   and working[0].ref.client_id == replacement.client_id
                                   and working[0].qty == case.position)
        self._finish_truth(case, flags)

    def _run_lost_answer(self, case: _Case, flags: dict) -> None:
        mark = self._mark(case)
        entry, stop = self._prices(case, mark, self._cfg.protective_offset)
        notional = case.context.case.max_notional * self._cfg.entry_notional_fraction
        decision = self._authorize(case, R.RiskAction.ENTRY, entry, stop, notional)
        if type(decision) is not R.ApprovedDecision:
            _hold("nc06.refused")
        flags["nc06"] = True
        expected_client = K.client_id_for(case.context.synthetic_intent_id, "classic")
        self._faults.arm(FaultKind.DROP_AFTER_ACCEPT,
                         lambda request: request.method == "POST" and client_id_of(request) == expected_client)
        order = self._market(case, Purpose.ENTRY, decision.quantity, Action.ENTER)
        fired = [f for f in self._faults.fired if f.kind is FaultKind.DROP_AFTER_ACCEPT]
        if len(fired) == 1 and fired[0].upstream_status is not None and 200 <= fired[0].upstream_status < 300:
            flags["accepted"] = flags["lost"] = fired[0].fired_at_ms
        submits = [o for o in case.orders if o.purpose is Purpose.ENTRY]
        flags["entry"] = (order.state is IntentState.FILLED and len(submits) == 1
                          and case.orders_placed == 1 and case.position == decision.quantity)
        if not flags["entry"]:
            _hold("open.unconfirmed")
        self._stop(case, case.position, stop)
        flags["protection"] = True
        self._finish_truth(case, flags)

    def _run_minimum(self, case: _Case, flags: dict) -> None:
        mark = self._mark(case)
        entry, stop = self._prices(case, mark, self._cfg.protective_offset)
        filters = self._filters_for(case)
        tiny = min(filters.min_notional, filters.min_quantity * entry) / 2
        decision = self._authorize(case, R.RiskAction.ENTRY, entry, stop, tiny)
        flags["nc06"] = True
        flags["minimum"] = (type(decision) is R.RefusedDecision
                            and bool({R.RefusalCode.BELOW_MIN_QUANTITY, R.RefusalCode.BELOW_MIN_NOTIONAL}
                                     & set(decision.reasons))
                            and case.orders_placed == 0)
        self._finish_truth(case, flags)

    def _run_restart(self, case: _Case, flags: dict) -> None:
        self._open_protected(case, flags, adverse=self._cfg.protective_offset)
        before = [(o.client_id, o.state, o.executed_qty) for o in case.orders]
        position = case.position
        if case.journal is not None:
            case.journal.close()
        case.journal = None
        restart_boot = case.context.restart_boot_id
        if restart_boot == case.context.boot_id:
            _hold("restart.boot_reuse")
        # Until the reopened journal is proven to match, local truth is untrusted: cleanup must HOLD and send nothing.
        case.trusted = False
        try:
            reopened = self._journals.open(case.context.case.case_id, restart_boot, reopen=True)
        except Exception:
            _hold("restart.journal")
        records = reopened.records()
        rebuilt_orders, rebuilt_position = self._rebuild(case, records)
        if ([(o.client_id, o.state, o.executed_qty) for o in rebuilt_orders] != before
                or rebuilt_position != position):
            reopened.close()
            _hold("restart.mismatch")
        case.journal, case.boot_id = reopened, restart_boot
        case.orders, case.position, case.journal_sequence = rebuilt_orders, rebuilt_position, len(records)
        case.trusted = True
        flags["reopened"] = True
        flags["restart"] = restart_boot
        self._finish_truth(case, flags)

    def _rebuild(self, case: _Case, records: tuple[JournalRecord, ...]) -> tuple[list[_Order], Decimal]:
        orders: dict[str, _Order] = {}
        position = ZERO
        for record in records:
            if record.case_id != case.context.case.case_id or record.symbol != case.symbol or record.side != case.side:
                _hold("restart.journal_identity")
            if record.kind is RecordKind.POSITION:
                position = record.executed_qty
                continue
            order = orders.get(record.client_id)
            if order is None:
                order = _Order(record.intent_id, record.client_id, record.route, record.purpose, record.symbol,
                               record.side, record.requested_qty, record.stop_price)
                orders[record.client_id] = order
            elif (order.intent_id, order.route, order.purpose, order.requested_qty) != (
                    record.intent_id, record.route, record.purpose, record.requested_qty):
                _hold("restart.journal_order")
            order.state, order.executed_qty = record.state, record.executed_qty
            order.exchange_order_id = record.exchange_order_id
        return list(orders.values()), position

    def _run_429(self, case: _Case, flags: dict) -> None:
        _entry, stop, first = self._open_protected(case, flags, adverse=self._cfg.protective_offset)
        self._faults.arm(FaultKind.HTTP_429, lambda request: request.url.endswith("/fapi/v2/positionRisk"),
                         retry_after_ms=self._cfg.rate_retry_after_ms)
        trigger = self._run(Action.ENTER, Operation.POSITIONS, PositionsRequest(symbol=case.symbol))
        fired = [f for f in self._faults.fired if f.kind is FaultKind.HTTP_429]
        snap = self._budget.snapshot()
        if len(fired) != 1 or trigger.state is not WorkState.COMPLETED or snap.state is not RateBlockState.BLOCKED_UNTIL:
            self._finish_truth(case, flags)
            return
        observed = fired[0].fired_at_ms
        retry_at = observed + self._cfg.rate_retry_after_ms
        blocked = self._run(Action.ENTER, Operation.POSITIONS, PositionsRequest(symbol=case.symbol))
        non_protection_blocked = blocked.state is WorkState.REFUSED and blocked.detail == "blocked_until"
        replacement = self._stop(case, case.position, stop)
        self._cancel(case, first)
        preserved = replacement.state is IntentState.WORKING and first.state in _TERMINAL
        resumed = None
        while resumed is None and self._now() < case.context.case_deadline_ms:
            self._sleep(self._cfg.poll_ms)
            if self._now() < retry_at:
                continue
            probe = self._run(Action.ENTER, Operation.POSITIONS, PositionsRequest(symbol=case.symbol))
            if probe.state is WorkState.COMPLETED and probe.value.kind is ReadKind.OK:
                resumed = self._now()
        if resumed is not None:
            flags["rate"] = RateLimitEvidence(RateLimitKind.HTTP_429, observed, retry_at, non_protection_blocked,
                                              preserved, False, resumed)
        self._finish_truth(case, flags)

    def _run_418(self, case: _Case, flags: dict) -> None:
        self._open_protected(case, flags, adverse=self._cfg.protective_offset)
        reconciled, truth = self._reconciled(case)
        stops_before = self._working_stops(case)
        if not reconciled or truth is not FinalTruth.PROTECTED_RECONCILED or len(stops_before) != 1:
            flags["reconciled"], flags["truth"] = reconciled, truth
            return
        self._faults.arm(FaultKind.HTTP_418, lambda request: request.url.endswith("/fapi/v2/positionRisk"))
        trigger = self._run(Action.ENTER, Operation.POSITIONS, PositionsRequest(symbol=case.symbol))
        fired = [f for f in self._faults.fired if f.kind is FaultKind.HTTP_418]
        snap = self._budget.snapshot()
        latched = (len(fired) == 1 and trigger.state is WorkState.COMPLETED
                   and snap.state is RateBlockState.BLOCKED_UNKNOWN_DURATION and snap.cause == "ban_418")
        if not latched:
            flags["truth"] = FinalTruth.HOLD
            return
        case.rate_latch_synthetic = True
        observed = fired[0].fired_at_ms
        blocked = self._run(Action.ENTER, Operation.POSITIONS, PositionsRequest(symbol=case.symbol))
        non_protection_blocked = blocked.state is WorkState.REFUSED
        # Under a latched ban nothing may be sent; the protection lane is preserved when the confirmed stop stays
        # resting untouched (no cancel/replace attempted while latched).
        preserved = self._working_stops(case) == stops_before
        flags["rate"] = RateLimitEvidence(RateLimitKind.HTTP_418, observed, observed + 1, non_protection_blocked,
                                          preserved, True, None)
        flags["reconciled"], flags["truth"] = True, FinalTruth.PROTECTED_RECONCILED

    # ------------------------------------------------------------------------------------------------ cleanup
    def cleanup(self, context: CleanupContext) -> CleanupEvidence:
        if type(context) is not CleanupContext:
            _hold("cleanup.context")
        memo = self._cleanups.get(context.token)
        if memo is not None:
            return self._recheck(context, memo)
        case = self._case
        if case is None or case.context.case.case_id != context.case_id:
            evidence = CleanupEvidence(context.token, context.started_ms, None, None, self._now(), FinalTruth.HOLD,
                                       False, 0)
            self._halted = True
            self._cleanups[context.token] = _CleanupMemo(evidence, False)
            return evidence
        self._faults.disarm()
        before = case.orders_placed
        try:
            if not case.trusted:
                _hold("cleanup.untrusted_state")
            if self._budget.snapshot().state is not RateBlockState.OPEN:
                if not case.rate_latch_synthetic:
                    _hold("cleanup.real_rate_block")
                self._budget.release_after_reconciliation()
            for order in case.orders:
                if order.state in (IntentState.UNKNOWN, IntentState.SUBMITTED, IntentState.CANCELLING):
                    _hold("cleanup.unresolved_order")
                if order.purpose is not Purpose.PROTECT and order.state not in _TERMINAL:
                    # an acknowledged entry/close that never settled: sending another order would be a guess
                    _hold("cleanup.unsettled_order")
            self._refresh_stops(case)
            positions = self._read_positions(case.symbol)
            venue_qty = positions.get((case.symbol, case.side), ZERO)
            if any(key != (case.symbol, case.side) for key in positions) or venue_qty != case.position:
                _hold("cleanup.position_mismatch")
            if case.position > 0:
                # flatten first: the resting reduce-only stop keeps protecting until the position is gone
                self._market(case, Purpose.CLOSE, case.position, Action.FLATTEN)
            for order in list(case.orders):
                if order.purpose is Purpose.PROTECT and order.state not in _TERMINAL:
                    self._cancel(case, order, Action.RECONCILE)
            plan = self._reconcile(case, case.symbol)
            if plan.status is not RecStatus.FLAT_CONFIRMED or case.position != 0:
                _hold("cleanup.not_flat")
            at = self._now()
            evidence = CleanupEvidence(context.token, context.started_ms, None, max(at, context.started_ms), None,
                                       FinalTruth.FLAT, True, case.orders_placed - before)
            flat = True
        except Exception:  # any failure, coded or not, ends the cleanup in HOLD with the memo recorded
            self._halted = True
            at = self._now()
            evidence = CleanupEvidence(context.token, context.started_ms, None, None, max(at, context.started_ms),
                                       FinalTruth.HOLD, False, case.orders_placed - before)
            flat = False
        if case.journal is not None:
            case.journal.close()
            case.journal = None
        self._case = None
        self._cleanups[context.token] = _CleanupMemo(evidence, flat)
        return evidence

    def _recheck(self, context: CleanupContext, memo: _CleanupMemo) -> CleanupEvidence:
        """Second call with the same token: re-read only, send nothing, and return the identical record."""
        if not memo.flat:
            return memo.evidence
        symbol = next((c.symbol for c in (self._case,) if c is not None), None)
        try:
            symbols = (symbol,) if symbol is not None else self._symbols
            for name in symbols:
                if self._read_positions(name) or any(
                        True for _ in self._read_orders(name)):
                    self._halted = True
                    return CleanupEvidence(context.token, context.started_ms, None, None,
                                           max(self._now(), context.started_ms), FinalTruth.HOLD, False,
                                           memo.evidence.cleanup_orders_total)
        except AdapterError:
            self._halted = True
            return CleanupEvidence(context.token, context.started_ms, None, None, max(self._now(), context.started_ms),
                                   FinalTruth.HOLD, False, memo.evidence.cleanup_orders_total)
        return memo.evidence


__all__ = ["AdapterError", "HarnessConfig", "RealTestnetAdapter"]
