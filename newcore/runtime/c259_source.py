"""Default-OFF native C259 sources over verified, immutable prepared inputs.

The research helper runs OUTSIDE runtime. These sources never compute signals,
ATR, ranks, sizing or market exits, read files, fetch data, verify an economic
result themselves, or submit venue work. The injected protected-receipt verifier
must authenticate the complete snapshot, helper/code/config closure, acceptance
of the selected side, account binding and collector provenance. No production
verifier, collector or launcher composition is installed by this module.

Entry quantity is bounded through NC-06's existing requested-risk ceiling. The
actual account/loss snapshots are retained; a separately verified isolated-book
policy cap must already be supplied. Management validates an already derived
frozen exit event and asks NC-07 for a FULL strategy exit, never a partial target.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from decimal import Decimal, DecimalException, localcontext
from enum import Enum
import hashlib
import json
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from newcore import management as M, risk as R
from newcore.domain import DecisionRecorded, IntentRecorded, Purpose, ResultObserved, ResultPhase, Side
from newcore.ports import keys as K
from newcore.runtime.ports import EntryCandidate

VERSION = 'c259-native-source/1'
HELPER_VERSION = 'c259-paper-intent/1'
# Historical helper component label only. Production acceptance authority is
# the verifier-bound execution head and exact closures below, not this label.
EVALUATOR_HEAD = '5c332bd2faf2f06591a6d621965770ba3cdaecee'
CONTRACT_SHA256 = '1aad55f092c0c308cb901701e2ee8bd3062d1eee9a07e13729673efbf5b17719'
ORIGINS = ('https://testnet.binancefuture.com', 'https://demo-fapi.binance.com')
HOUR_MS = 3_600_000
D = Decimal


class SourceRefusal(ValueError):
    """Stable refusal code only: no untrusted input or credentials in errors."""


class CausalProposal(Protocol):
    """The frozen PaperIntent value prepared by the separately bound helper."""
    intent_id: str
    candidate_id: int
    adapter_version: str
    evaluator_source_head: str
    contract_sha256: str
    strategy_name: str
    strategy_version: str
    book_id: str
    account_fingerprint: str
    side: int
    symbol: str
    decision_ms: int
    decision_candle_close_ms: int
    entry_due_ms: int
    scheduled_exit_ms: int
    quantity: Decimal
    entry_reference: Decimal
    stop: Decimal
    target: Decimal
    side_book_equity: Decimal
    modeled_risk_cash: Decimal
    requested_risk_cash_ceiling: Decimal
    gross_notional: Decimal
    collateral_cash_ceiling: Decimal
    estimated_entry_drag_reserve: Decimal
    inputs_sha256: str
    config_sha256: str
    signal_window_sha256: str
    source_digests: tuple[tuple[str, str], ...]
    execution_authorized: bool


def _value(value):
    if isinstance(value, Decimal):
        if not value.is_finite(): raise SourceRefusal('NONFINITE_INPUT')
        return str(value)
    if isinstance(value, Enum): return value.value
    if is_dataclass(value): return {f.name: _value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple): return [_value(v) for v in value]
    if value is None or type(value) in (str, int, bool): return value
    raise SourceRefusal('NONIMMUTABLE_SNAPSHOT')


def digest(value) -> str:
    return hashlib.sha256(json.dumps(_value(value), sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _hash(value):
    return type(value) is str and len(value) == 64 and set(value) <= set('0123456789abcdef') and value != '0' * 64


def _positive(*values):
    return all(type(v) is D and v.is_finite() and v > 0 for v in values)


def _require(condition, code):
    if not condition: raise SourceRefusal(code)


@dataclass(frozen=True)
class SourceConfig:
    account_id: str
    account_fingerprint: str
    enabled: bool = False
    origin: str = ORIGINS[0]
    environment: R.Environment = R.Environment.TESTNET
    implementation_sha256: str = ''
    protected_config_sha256: str = ''
    version: str = VERSION
    accepted_execution_head: str = ''
    evaluator_closure_sha256: str = ''
    helper_closure_sha256: str = ''


@dataclass(frozen=True)
class ReceiptAuthorization:
    """Returned ONLY by the injected trusted verifier, never synthesized here.

    This is a typed port capability, not a public PASS flag or an on-disk receipt
    verifier. Packaging must bind that verifier before enabling this source.
    """
    scope: str
    account_id: str
    snapshot_sha256: str
    source_config_sha256: str
    implementation_sha256: str
    protected_receipt_sha256: str
    economic_receipt_sha256: str
    collector_receipt_sha256: str
    generation: int
    journal_sequence: int
    observed_ms: int
    accepted_execution_head: str
    evaluator_closure_sha256: str
    helper_closure_sha256: str


class ReceiptVerifier(Protocol):
    def authorize(self, scope: str, snapshot, config: SourceConfig) -> ReceiptAuthorization | None: ...


@dataclass(frozen=True)
class NativeRiskContext:
    """Actual NC-06 snapshots, supplied by the verified collector/ledger owner."""
    account: R.AccountRiskSnapshot
    portfolio: R.PortfolioRiskSnapshot
    policy: R.RiskPolicySnapshot
    filters: R.VenueRiskFilters
    filters_sha256: str


@dataclass(frozen=True)
class EntrySnapshot:
    observed_ms: int
    observed_mono: int
    generation: int
    journal_sequence: int
    journal_sha256: str
    proposal: CausalProposal
    risk: NativeRiskContext
    isolated_book_sha256: str
    isolated_equity: Decimal
    all_c259_positions_and_reservations: int
    all_c259_open_and_reserved_risk_r: Decimal
    available_shared_collateral: Decimal
    available_shared_gross: Decimal
    reservations_receipt_sha256: str


@dataclass(frozen=True)
class FrozenExitEvent:
    """Already derived outside runtime; OPEN carries no later high/low values.

    RANGE becomes available at bar_open+1h and excludes same-boundary funding;
    OPEN includes funding due at its boundary before gap/scheduled exits.
    """
    phase: str
    bar_open_ms: int
    observed_ms: int
    open: Decimal
    high: Decimal | None
    low: Decimal | None
    mark: Decimal
    exit_kind: str
    source_sha256: str
    prior_events_receipt_sha256: str
    funding_applied_through_ms: int
    funding_receipt_sha256: str


@dataclass(frozen=True)
class ManagedLot:
    proposal: CausalProposal
    entry_intent_id: str
    evaluation: M.ManagementEvaluation
    event: FrozenExitEvent


@dataclass(frozen=True)
class ManagementSnapshot:
    observed_ms: int
    observed_mono: int
    generation: int
    journal_sequence: int
    journal_sha256: str
    lots: tuple[ManagedLot, ...]


def _proposal(p, config):
    _require(type(p).__module__ == 'tools.research.c259_paper_adapter' and type(p).__name__ == 'PaperIntent'
             and is_dataclass(p) and p.__dataclass_params__.frozen, 'UNBOUND_PREPARED_PROPOSAL')
    _require(p.candidate_id == 259 and p.adapter_version == HELPER_VERSION
             and p.evaluator_source_head == EVALUATOR_HEAD and p.contract_sha256 == CONTRACT_SHA256
             and p.execution_authorized is False and p.account_fingerprint == config.account_fingerprint,
             'PROPOSAL_PROVENANCE_MISMATCH')
    _require(p.strategy_name == 'c259_btc_leadlag' and p.strategy_version == 'v1'
             and type(p.side) is int and p.side in (-1, 1), 'CANDIDATE_IDENTITY_MISMATCH')
    expected_book = 'c259:' + ('long_after_positive_btc_impulse' if p.side == 1 else 'short_after_negative_btc_impulse')
    _require(p.book_id == expected_book and p.decision_ms % HOUR_MS == 0
             and p.decision_candle_close_ms == p.decision_ms - 1
             and p.entry_due_ms == p.decision_ms + HOUR_MS
             and p.scheduled_exit_ms == p.entry_due_ms + 12 * HOUR_MS, 'PROPOSAL_CLOCK_OR_BOOK_MISMATCH')
    _require(_positive(p.quantity, p.entry_reference, p.stop, p.target, p.side_book_equity,
                       p.modeled_risk_cash, p.requested_risk_cash_ceiling, p.gross_notional)
             and p.modeled_risk_cash <= p.requested_risk_cash_ceiling
             and p.requested_risk_cash_ceiling <= p.side_book_equity * D('.0025')
             and p.gross_notional == p.quantity * p.entry_reference
             and p.collateral_cash_ceiling >= p.gross_notional
             and (p.stop < p.entry_reference < p.target if p.side == 1 else p.target < p.entry_reference < p.stop),
             'INVALID_PROPOSAL_PROTECTION_OR_SIZE')
    _require(type(p.source_digests) is tuple and len(p.source_digests) == 6
             and {name for name, _ in p.source_digests} == {
                 'market', 'pit', 'filters', 'BTC', 'funding', 'economic_acceptance'}
             and all(_hash(value) for value in (p.intent_id, p.inputs_sha256, p.config_sha256,
                 p.signal_window_sha256, *[v for _, v in p.source_digests])),
             'MISSING_PROPOSAL_DIGEST')
    return K.decision_key(p.strategy_name, p.strategy_version, '1h', p.symbol,
                          Side.LONG if p.side == 1 else Side.SHORT, p.decision_ms, Purpose.ENTRY)


class _Source:
    def __init__(self, *, config: SourceConfig, provider: Callable, verifier: ReceiptVerifier,
                 journal: Callable, wall_clock: Callable[[], int]):
        self.config, self._provider, self._verifier = config, provider, verifier
        self._journal, self._wall = journal, wall_clock
        self.reasons = Counter()

    def _snapshot(self, account_id, now_mono, scope, expected):
        c = self.config
        _require(c.enabled is True, 'DEFAULT_OFF')                    # before any provider/journal/verifier access
        _require(c.version == VERSION and c.account_id == account_id and c.origin in ORIGINS
                 and c.environment in (R.Environment.PAPER, R.Environment.TESTNET)
                 and all(_hash(v) for v in (c.account_fingerprint, c.implementation_sha256,
                    c.protected_config_sha256, c.evaluator_closure_sha256, c.helper_closure_sha256))
                 and type(c.accepted_execution_head) is str and len(c.accepted_execution_head) == 40
                 and set(c.accepted_execution_head) <= set('0123456789abcdef')
                 and c.accepted_execution_head != '0' * 40, 'UNBOUND_SOURCE_CONFIG')
        now = self._wall()
        snapshot = self._provider(account_id, now_mono)
        _require(type(now_mono) is int and now_mono >= 0 and type(snapshot) is expected
                 and type(snapshot.observed_ms) is int and snapshot.observed_ms == now
                 and snapshot.observed_mono == now_mono and type(snapshot.generation) is int
                 and snapshot.generation > 0, 'MISSING_OR_STALE_COLLECTOR_SNAPSHOT')
        view = self._journal()
        _require(view is not None and view.account_id == account_id
                 and snapshot.journal_sequence == view.last_sequence()
                 and snapshot.journal_sha256 == digest(tuple(view.read())), 'JOURNAL_SNAPSHOT_MISMATCH')
        authorization = self._verifier.authorize(scope, snapshot, c)
        _require(type(authorization) is ReceiptAuthorization
                 and authorization.scope == scope and authorization.account_id == account_id
                 and authorization.snapshot_sha256 == digest(snapshot)
                 and authorization.source_config_sha256 == digest(c)
                 and authorization.implementation_sha256 == c.implementation_sha256
                 and authorization.accepted_execution_head == c.accepted_execution_head
                 and authorization.evaluator_closure_sha256 == c.evaluator_closure_sha256
                 and authorization.helper_closure_sha256 == c.helper_closure_sha256
                 and authorization.generation == snapshot.generation
                 and authorization.journal_sequence == snapshot.journal_sequence
                 and authorization.observed_ms == now
                 and all(_hash(v) for v in (authorization.protected_receipt_sha256,
                     authorization.economic_receipt_sha256, authorization.collector_receipt_sha256)),
                 'PROTECTED_RECEIPT_AUTHORIZATION_REQUIRED')
        _require(view.last_sequence() == snapshot.journal_sequence, 'JOURNAL_CHANGED_DURING_AUTHORIZATION')
        return snapshot, view


class C259EntrySource(_Source):
    """At most one proposal per fresh collector generation; sink owns durability."""
    def candidates(self, account_id, now_mono):
        try:
            snapshot, view = self._snapshot(account_id, now_mono, 'ENTRY', EntrySnapshot)
            p, context = snapshot.proposal, snapshot.risk
            key = _proposal(p, self.config)
            _require(snapshot.observed_ms == p.entry_due_ms, 'WRONG_CANONICAL_ENTRY_EVENT')
            _require(not any(isinstance(ev, DecisionRecorded) and ev.decision.key == key for ev in view.read()),
                     'SIGNAL_ALREADY_CONSUMED')
            _require(type(context) is NativeRiskContext and snapshot.isolated_equity == p.side_book_equity
                     and _hash(snapshot.isolated_book_sha256) and _hash(snapshot.reservations_receipt_sha256),
                     'ISOLATED_BOOK_MISMATCH')
            count, used = snapshot.all_c259_positions_and_reservations, snapshot.all_c259_open_and_reserved_risk_r
            _require(type(count) is int and 0 <= count < 2 and type(used) is D and used.is_finite() and used >= 0
                     and (count == 0) == (used == 0)
                     and p.estimated_entry_drag_reserve >= 0
                     and p.side_book_equity > p.estimated_entry_drag_reserve
                     and used + p.modeled_risk_cash / ((p.side_book_equity - p.estimated_entry_drag_reserve) * D('.01')) <= D('.50')
                     and p.collateral_cash_ceiling <= snapshot.available_shared_collateral
                     and p.gross_notional <= snapshot.available_shared_gross, 'ALL_C259_RESERVATION_CAP')
            a, portfolio, policy, f = context.account, context.portfolio, context.policy, context.filters
            _require(a.account_id == portfolio.account_id == policy.account_id == account_id
                     and a.environment is policy.environment is self.config.environment
                     and portfolio.generation == snapshot.journal_sequence
                     and policy.capital_cap == snapshot.isolated_equity
                     and context.filters_sha256 == dict(p.source_digests).get('filters')
                     and f.symbol == p.symbol, 'NATIVE_RISK_OR_BOOK_BINDING_MISMATCH')
            _require(type(portfolio.loss) is R.LossState and type(policy.loss_limits) is R.LossLimits
                     and portfolio.loss.trading_day == datetime.fromtimestamp(snapshot.observed_ms / 1000,
                                                         ZoneInfo('Africa/Cairo')).strftime('%Y-%m-%d'),
                     'MISSING_OR_STALE_ACTUAL_LOSS_STATE')
            with localcontext() as arithmetic:
                arithmetic.prec = 64
                request = R.RiskRequest(request_id='req_' + p.intent_id[:32], account_id=account_id,
                    environment=a.environment, account_generation=a.generation,
                    portfolio_generation=portfolio.generation, policy_generation=policy.generation,
                    filter_generation=f.generation, action=R.RiskAction.ENTRY, strategy_id=key.strategy,
                    book_id=p.book_id, symbol=p.symbol,
                    side=R.PositionSide.LONG if p.side == 1 else R.PositionSide.SHORT,
                    entry_price=p.entry_reference, stop_price=p.stop,
                    requested_risk=p.quantity * abs(p.entry_reference - p.stop), requested_leverage=D(1))
                evaluation = R.RiskEvaluation(account=a, portfolio=portfolio, policy=policy,
                                              filters=f, request=request, followers=())
                decision = R.evaluate(evaluation)
            _require(type(decision) is R.ApprovedDecision and decision.quantity <= p.quantity,
                     'NATIVE_RISK_REFUSED_OR_UPSIZED')
            _require(view.last_sequence() == snapshot.journal_sequence, 'JOURNAL_CHANGED_DURING_ADMISSION')
            return (EntryCandidate(key=key, evaluation=evaluation),)
        except (SourceRefusal, ValueError, TypeError, AttributeError, DecimalException) as error:
            self.reasons[str(error) if type(error) is SourceRefusal else 'SOURCE_INPUT_INVALID'] += 1
            return ()


def _exit_event(p, event, now):
    _require(type(event) is FrozenExitEvent and event.observed_ms == now and event.bar_open_ms % HOUR_MS == 0
             and event.bar_open_ms >= p.entry_due_ms
             and all(_hash(v) for v in (event.source_sha256, event.prior_events_receipt_sha256,
                                       event.funding_receipt_sha256))
             and _positive(event.open, event.mark), 'INVALID_EXIT_EVENT_PROVENANCE')
    stop_gap = event.open <= p.stop if p.side == 1 else event.open >= p.stop
    target_gap = event.open >= p.target if p.side == 1 else event.open <= p.target
    if event.phase == 'OPEN':
        _require(now == event.bar_open_ms and event.high is None and event.low is None and event.mark == event.open
                 and now <= p.scheduled_exit_ms and event.funding_applied_through_ms == now,
                 'STALE_OR_LOOKAHEAD_OPEN_EXIT')
        expected = 'STOP_GAP' if stop_gap else 'TARGET_GAP' if target_gap else (
            'SCHEDULED' if now == p.scheduled_exit_ms else 'HOLD')
        high = low = event.open
    else:
        _require(event.phase == 'RANGE' and now == event.bar_open_ms + HOUR_MS
                 and event.bar_open_ms < p.scheduled_exit_ms and _positive(event.high, event.low)
                 and event.low <= min(event.open, event.mark) <= max(event.open, event.mark) <= event.high
                 and not stop_gap and not target_gap and event.funding_applied_through_ms == now - 1,
                 'STALE_OR_LOOKAHEAD_RANGE_EXIT')
        stop_touch = event.low <= p.stop if p.side == 1 else event.high >= p.stop
        target_touch = event.high >= p.target if p.side == 1 else event.low <= p.target
        expected = 'STOP_RANGE' if stop_touch else 'TARGET_RANGE' if target_touch else 'HOLD'
        high, low = event.high, event.low
    _require(event.exit_kind == expected, 'FROZEN_EXIT_ORDER_MISMATCH')
    return expected, high, low


class C259ManagementSource(_Source):
    def evaluations(self, account_id, now_mono):
        if self.config.enabled is not True: return ()
        try:
            snapshot, view = self._snapshot(account_id, now_mono, 'MANAGEMENT', ManagementSnapshot)
            _require(type(snapshot.lots) is tuple, 'NONIMMUTABLE_MANAGED_LOTS')
            events = tuple(view.read())
            intents = {ev.intent.intent_id: ev.intent for ev in events if isinstance(ev, IntentRecorded)}
            finals = [ev.result for ev in events if isinstance(ev, ResultObserved)
                      and ev.result.phase is ResultPhase.FINAL]
            entry_results = {result.intent_id: result for result in finals
                             if result.intent_id in intents and intents[result.intent_id].purpose is Purpose.ENTRY}
            native_strategy = K.strategy_instance('c259_btc_leadlag', '1h')
            native_entries = {entry for entry in entry_results if intents[entry].slot_id == native_strategy}
            native_lots = {K.derive_lot_id(account_id, entry) for entry in native_entries}
            _require(not any(result.supersedes_result_id is not None for result in finals
                if result.intent_id in native_entries or result.intent_id in intents
                and intents[result.intent_id].owner_id in native_lots),
                'LATE_FILL_REQUIRES_LEDGER_RECONCILIATION')
            quantities = {}
            for entry, result in entry_results.items():
                if intents[entry].slot_id != native_strategy: continue
                lot_id = K.derive_lot_id(account_id, entry)
                closed = sum((result.executed_qty or D(0) for result in finals
                    if result.intent_id in intents and intents[result.intent_id].owner_id == lot_id
                    and intents[result.intent_id].purpose in (Purpose.CLOSE, Purpose.REDUCE, Purpose.PROTECT)), D(0))
                quantities[lot_id] = (result.executed_qty or D(0)) - closed
            supplied = tuple(row.evaluation.lot.lot_id for row in snapshot.lots)
            _require(all(quantity >= 0 for quantity in quantities.values())
                     and len(supplied) == len(set(supplied))
                     and set(supplied) == {lot for lot, quantity in quantities.items() if quantity > 0},
                     'INCOMPLETE_C259_JOURNAL_LOT_COVERAGE')
            output = []
            for managed in snapshot.lots:
                p, evaluation, event = managed.proposal, managed.evaluation, managed.event
                key = _proposal(p, self.config)
                entry = K.derive_intent_id(account_id, key)
                lot = evaluation.lot
                _require(managed.entry_intent_id == entry and lot.lot_id == K.derive_lot_id(account_id, entry)
                         and lot.strategy_id == key.strategy and lot.side.value == key.side.value
                         and 0 < lot.quantity <= p.quantity and lot.initial_stop == p.stop
                         and evaluation.protection.confirmed_stop == p.stop
                         and lot.quantity == quantities[lot.lot_id]
                         and lot.initial_quantity == entry_results[entry].executed_qty
                         and lot.entry_price == entry_results[entry].avg_price,
                         'MANAGED_LOT_OR_BOOK_MISMATCH')
                owned = [ev.intent for ev in view.read() if isinstance(ev, IntentRecorded) and ev.intent.intent_id == entry]
                _require(len(owned) == 1 and owned[0].slot_id == key.strategy and owned[0].symbol == p.symbol
                         and owned[0].side is key.side, 'JOURNAL_OWNERSHIP_MISMATCH')
                kind, high, low = _exit_event(p, event, snapshot.observed_ms)
                policy = replace(evaluation.policy, targets=(), preserve_runner=False, runner_quantity=D(0),
                    breakeven_trigger_r=None, trail_trigger_r=None, trail_distance_r=None, max_holding_bars=None,
                    max_add_count=0, max_single_add_risk=D(0), max_cumulative_add_risk=D(0), risk_increases=())
                market = replace(evaluation.market, high=high, low=low, mark=event.mark,
                                 completed=event.phase == 'RANGE', path_order=M.PathOrder.NONE,
                                 forced_exit_confirmed=False)
                strategy = replace(evaluation.strategy_exit, version=VERSION,
                    exit_requested=kind in ('TARGET_GAP', 'TARGET_RANGE', 'SCHEDULED')
                    and evaluation.pending.kind is M.PendingKind.NONE)
                actual = replace(evaluation, policy=policy, market=market, strategy_exit=strategy)
                plan = M.evaluate(actual)
                _require(plan.reason not in (M.PlanReason.INPUT_INVALID, M.PlanReason.GENERATION_MISMATCH,
                                             M.PlanReason.STALE_INPUT), 'INVALID_ACTUAL_MANAGEMENT_STATE')
                if strategy.exit_requested:
                    _require(len(plan.actions) == 1 and type(plan.actions[0]) is M.CloseAction
                             and plan.actions[0].quantity == lot.quantity, 'FULL_EXIT_PARITY_UNAVAILABLE')
                output.append(actual)
            _require(view.last_sequence() == snapshot.journal_sequence, 'JOURNAL_CHANGED_DURING_MANAGEMENT')
            return tuple(output)
        except (ValueError, TypeError, AttributeError, DecimalException) as error:
            code = str(error) if type(error) is SourceRefusal else 'MANAGEMENT_INPUT_INVALID'
            self.reasons[code] += 1
            raise SourceRefusal(code) from None                   # runtime HOLD; never silently omit an active lot
