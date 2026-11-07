"""Synthetic providers/receipts; no production acceptance, market reads or venue."""
import dataclasses
from dataclasses import replace
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'newcore_risk'))
from test_c259_paper_adapter import fixture as helper_fixture,observed_inputs
from test_nc06_risk import account, portfolio, policy, filters, allocation
from test_rt02_journaling import A, Harness
from test_rt01_runtime import _evaluation
from tools.research import c259_paper_adapter as helper
from newcore.runtime import c259_source as S
from newcore import management as M, risk as R
from newcore.ports import keys as K
from newcore.ports.venue import OrderOutcome, OutcomeKind

H = S.HOUR_MS


class SyntheticVerifier:
    """Explicit test double for a verifier that is not installed in production."""
    def __init__(self): self.transform = lambda value: value
    def authorize(self, scope, snapshot, config, dispatch_ms, dispatch_mono):
        value = S.ReceiptAuthorization(scope, config.account_id, S.digest(snapshot), S.digest(config),
            config.implementation_sha256, helper.digest('synthetic protected receipt'),
            helper.digest('synthetic economic receipt'), helper.digest('synthetic collector receipt'),
            snapshot.generation, snapshot.journal_sequence, snapshot.observed_ms,
            config.accepted_execution_head, config.evaluator_closure_sha256, config.helper_closure_sha256,
            dispatch_ms,dispatch_mono,config.protected_runtime_config_sha256)
        return self.transform(value)


class View:
    account_id = A
    def __init__(self, sequence=11, events=()): self.sequence, self.events = sequence, events
    def last_sequence(self): return self.sequence
    def read(self): return self.events


def inputs(raw=None):
    raw = helper_fixture() if raw is None else raw
    proposal = helper.build_intents(**raw).intents[0]
    config = S.SourceConfig(A, proposal.account_fingerprint, enabled=True,
        implementation_sha256=helper.digest('synthetic source implementation'),
        protected_config_sha256=helper.digest('synthetic protected config'),
        accepted_execution_head='e' * 40, evaluator_closure_sha256=helper.digest('actual accepted evaluator closure'),
        helper_closure_sha256=helper.digest('separately bound helper closure'),
        runtime_config=raw['config'].runtime_config,
        protected_runtime_config_sha256=raw['config'].protected_runtime_config_sha256)
    key = K.decision_key(proposal.strategy_name, proposal.strategy_version, '1h', proposal.symbol,
                         'LONG', proposal.decision_ms, 'entry')
    symbol = next(row for row in raw['cohort'].symbols if row.symbol == proposal.symbol)
    day = datetime.fromtimestamp(proposal.entry_due_ms / 1000, ZoneInfo('Africa/Cairo')).strftime('%Y-%m-%d')
    book = portfolio(account_id=A)
    book = replace(book, loss=replace(book.loss, trading_day=day))
    context = S.NativeRiskContext(account(account_id=A), book,
        policy(account_id=A, capital_cap=proposal.side_book_equity,
            allocations=(allocation(strategy_id=key.strategy, book_id=proposal.book_id,
                                    notional_fraction=D(1), risk_fraction=D('.1')),),
            correlation_map=(R.CorrelationAssignment(symbol=proposal.symbol, bucket='crypto-major'),)),
        filters(symbol=proposal.symbol, tick_size=symbol.filters.tick, step_size=symbol.filters.step,
                min_quantity=symbol.filters.min_qty, max_quantity=symbol.filters.max_qty,
                min_notional=symbol.filters.min_notional), symbol.filters_sha256,
                proposal.dispatch_entry_reference,proposal.collector_observed_ms,proposal.collector_observed_mono,
                proposal.dispatch_quote_source_sha256)
    view = View()
    snapshot = S.EntrySnapshot(proposal.collector_observed_ms, proposal.collector_observed_mono, 3, 11, S.digest(()), proposal, context,
        helper.digest(raw['book']), proposal.side_book_equity, 0, D(0), D(5000), D(5000),
        helper.digest('complete all-C259 open+pending reservation receipt'))
    verifier = SyntheticVerifier()
    source = S.C259EntrySource(config=config, provider=lambda *_: snapshot, verifier=verifier,
                               journal=lambda: view, wall_clock=lambda: proposal.prepared_ms)
    return source, snapshot, view, verifier


def test_source_off_precedes_provider_journal_and_receipt_access():
    source, snapshot, view, verifier = inputs()
    source.config = replace(source.config, enabled=False)
    source._provider = source._journal = lambda *args: pytest.fail('disabled source accessed provider')
    assert source.candidates(A, 123) == () and source.reasons['DEFAULT_OFF'] == 1
    managed = S.C259ManagementSource(config=source.config, provider=source._provider, verifier=verifier,
                                    journal=source._journal, wall_clock=source._wall)
    assert managed.evaluations(A, 123) == ()


def test_candidate_preserves_real_wallet_loss_and_hard_proposal_quantity_ceiling():
    source, snapshot, view, verifier = inputs()
    candidate, = source.candidates(A, 123)
    native = candidate.evaluation
    decision = R.evaluate(native)
    assert type(decision) is R.ApprovedDecision and decision.quantity <= snapshot.proposal.quantity
    assert native.account is snapshot.risk.account and native.portfolio.loss is snapshot.risk.portfolio.loss
    assert native.account.equity == D(10000) and native.policy.capital_cap == snapshot.isolated_equity
    assert native.request.requested_risk == snapshot.proposal.quantity * abs(snapshot.proposal.entry_reference - snapshot.proposal.stop)
    assert candidate.key.candle_close_ms == snapshot.proposal.decision_ms
    assert candidate.key.candle_close_ms != snapshot.proposal.decision_candle_close_ms
    richer = replace(snapshot, risk=replace(snapshot.risk, account=replace(native.account, equity=D(1000000))))
    source._provider = lambda *_: richer
    other, = source.candidates(A, 123)
    assert R.evaluate(other.evaluation).quantity <= snapshot.proposal.quantity


@pytest.mark.parametrize('transform', [lambda value: None, lambda value: True,
    lambda value: replace(value, snapshot_sha256='f' * 64),
    lambda value: replace(value, generation=value.generation - 1),
    lambda value: replace(value, accepted_execution_head='a' * 40),
    lambda value: replace(value, evaluator_closure_sha256='b' * 64),
    lambda value: replace(value, helper_closure_sha256='c' * 64)])
def test_absent_forged_boolean_or_mismatched_receipt_cannot_authorize(transform):
    source, snapshot, view, verifier = inputs(); verifier.transform = transform
    assert source.candidates(A, 123) == ()
    assert source.reasons['PROTECTED_RECEIPT_AUTHORIZATION_REQUIRED'] == 1


@pytest.mark.parametrize('change', ['stale', 'journal', 'wallet_cap', 'loss_missing', 'loss_stale', 'loss_halted',
                                  'candidate_cap', 'reservation_risk', 'old_component_as_authority'])
def test_hostile_collector_capital_loss_and_generation_states_refuse(change):
    source, snapshot, view, verifier = inputs()
    if change == 'stale': snapshot = replace(snapshot, observed_mono=124)
    elif change == 'journal': view.sequence += 1
    elif change == 'wallet_cap': snapshot = replace(snapshot, risk=replace(snapshot.risk,
        policy=replace(snapshot.risk.policy, capital_cap=snapshot.risk.account.equity)))
    elif change == 'loss_missing': snapshot = replace(snapshot, risk=replace(snapshot.risk,
        portfolio=replace(snapshot.risk.portfolio, loss=None)))
    elif change == 'loss_stale': snapshot = replace(snapshot, risk=replace(snapshot.risk,
        portfolio=replace(snapshot.risk.portfolio, loss=replace(snapshot.risk.portfolio.loss, trading_day='2020-01-01'))))
    elif change == 'loss_halted': snapshot = replace(snapshot, risk=replace(snapshot.risk,
        portfolio=replace(snapshot.risk.portfolio, loss=replace(snapshot.risk.portfolio.loss, halt_latched=True))))
    elif change == 'candidate_cap': snapshot = replace(snapshot, all_c259_positions_and_reservations=2)
    elif change == 'reservation_risk': snapshot = replace(snapshot, all_c259_positions_and_reservations=1,
                                                         all_c259_open_and_reserved_risk_r=D('.5'))
    else: source.config = replace(source.config, accepted_execution_head='', evaluator_closure_sha256='')
    source._provider = lambda *_: snapshot
    assert source.candidates(A, 123) == ()


def managed_inputs(tmp_path):
    source, snapshot, view, verifier = inputs()
    candidate, = source.candidates(A, 123)
    h = Harness(tmp_path)
    p = snapshot.proposal
    h.wall.value = h.venue.now.value = p.entry_due_ms
    submit = h.venue.submit_market
    # A scripted ENTRY fill matches its executable quote. Closing fills retain
    # the venue's independently scripted actual price, never a fake TP price.
    def market(order):
        value=submit(order)
        return replace(value,avg_price=p.dispatch_entry_reference) if not order.reduce and value.executed_qty else value
    h.venue.submit_market=market
    decision = R.evaluate(candidate.evaluation)
    assert h.sink.accept_entry(A, decision, candidate.evaluation, candidate.key)
    assert h.drive() == ()
    entry = K.derive_intent_id(A, candidate.key); lot_id = K.derive_lot_id(A, entry)
    evaluation = _evaluation()
    evaluation = replace(evaluation,
        lot=replace(evaluation.lot, lot_id=lot_id, strategy_id=candidate.key.strategy,
            quantity=decision.quantity, initial_quantity=decision.quantity, entry_price=p.dispatch_entry_reference,
            initial_stop=p.stop, initial_risk_per_unit=abs(p.entry_reference - p.stop)),
        policy=replace(evaluation.policy, tick_size=snapshot.risk.filters.tick_size,
                       quantity_step=snapshot.risk.filters.step_size, min_quantity=snapshot.risk.filters.min_quantity),
        protection=replace(evaluation.protection, confirmed_stop=p.stop, confirmed_quantity=decision.quantity))
    now = p.entry_due_ms + H
    event = S.FrozenExitEvent('OPEN', now, now, p.entry_reference, None, None, p.entry_reference, 'HOLD',
        helper.digest('event'), helper.digest('complete prior causal events'), now, helper.digest('funding ledger'),now,123)
    managed = S.ManagementSnapshot(now, 123, 3, h.journal.last_sequence(), S.digest(tuple(h.journal.read())),
        (S.ManagedLot(p, entry, evaluation, event),))
    owner = S.C259ManagementSource(config=source.config, provider=lambda *_: managed, verifier=verifier,
                                   journal=lambda: h.journal, wall_clock=lambda: managed.observed_ms)
    return h, owner, managed


def at_event(source, snapshot, *, kind, phase='RANGE', due=False, stop_touch=False, target_touch=False):
    row = snapshot.lots[0]; p = row.proposal
    now = p.scheduled_exit_ms if due else p.entry_due_ms + H
    high = (p.target + D(1) if target_touch else p.entry_reference)
    low = (p.stop - D(1) if stop_touch else p.entry_reference)
    event = replace(row.event, phase=phase, observed_ms=now, canonical_event_ms=now,bar_open_ms=now if phase == 'OPEN' else now - H,
                    high=None if phase == 'OPEN' else high, low=None if phase == 'OPEN' else low,
                    exit_kind=kind, funding_applied_through_ms=now if phase == 'OPEN' else now - 1)
    value = replace(snapshot, observed_ms=now, lots=(replace(row, event=event),))
    source._provider = lambda *_: value
    source._wall = lambda: now
    return value


@pytest.mark.parametrize('kind,phase,due,stop,target', [
    ('SCHEDULED', 'OPEN', True, False, False), ('STOP_RANGE', 'RANGE', False, True, True)])
def test_management_full_target_time_and_stop_first_collision(tmp_path, kind, phase, due, stop, target):
    h, source, snapshot = managed_inputs(tmp_path)
    try:
        at_event(source, snapshot, kind=kind, phase=phase, due=due, stop_touch=stop, target_touch=target)
        evaluation, = source.evaluations(A, 123)
        plan = M.evaluate(evaluation)
        assert evaluation.policy.targets == () and evaluation.policy.max_holding_bars is None
        if stop:
            assert not evaluation.strategy_exit.exit_requested
            assert all(type(action) is not M.CloseAction for action in plan.actions)
            assert plan.reason is M.PlanReason.STOP_CONFIRMATION_REQUIRED
        else:
            assert len(plan.actions) == 1 and type(plan.actions[0]) is M.CloseAction
            assert plan.actions[0].quantity == evaluation.lot.quantity
    finally: h.close()


@pytest.mark.parametrize('change', ['target_stop_collision', 'future_range_on_due_open', 'late_due',
    'missing_funding_boundary', 'wrong_lot', 'missing_active_lot', 'stale_generation'])
def test_management_hostile_parity_or_missing_state_holds_instead_of_silent_skip(tmp_path, change):
    h, source, snapshot = managed_inputs(tmp_path)
    try:
        value = at_event(source, snapshot, kind='SCHEDULED', phase='OPEN', due=True)
        row = value.lots[0]
        if change == 'target_stop_collision': value = at_event(source, snapshot, kind='TARGET_RANGE',
                                                              stop_touch=True, target_touch=True)
        elif change == 'future_range_on_due_open': value = replace(value, lots=(replace(row,
            event=replace(row.event, high=row.proposal.target, low=row.proposal.stop)),))
        elif change == 'late_due': value = replace(value, observed_ms=value.observed_ms + H,
            lots=(replace(row, event=replace(row.event, observed_ms=value.observed_ms + H,
                                           bar_open_ms=value.observed_ms + H)),))
        elif change == 'missing_funding_boundary': value = replace(value, lots=(replace(row,
            event=replace(row.event, funding_applied_through_ms=value.observed_ms - 1)),))
        elif change == 'wrong_lot': value = replace(value, lots=(replace(row, evaluation=replace(row.evaluation,
            lot=replace(row.evaluation.lot, quantity=row.evaluation.lot.quantity / 2))),))
        elif change == 'missing_active_lot': value = replace(value, lots=())
        else: value = replace(value, observed_mono=124)
        source._provider = lambda *_: value; source._wall = lambda: value.observed_ms
        with pytest.raises(S.SourceRefusal): source.evaluations(A, 123)
    finally: h.close()


def test_pending_close_does_not_generate_a_duplicate_scheduled_order(tmp_path):
    h, source, snapshot = managed_inputs(tmp_path)
    try:
        value = at_event(source, snapshot, kind='SCHEDULED', phase='OPEN', due=True)
        row = value.lots[0]
        value = replace(value, lots=(replace(row, evaluation=replace(row.evaluation,
            pending=replace(row.evaluation.pending, kind=M.PendingKind.CLOSE))),))
        source._provider = lambda *_: value
        evaluation, = source.evaluations(A, 123)
        assert M.evaluate(evaluation).actions == ()
    finally: h.close()


@pytest.mark.parametrize('change', ['missing_digests', 'raw_close_key', 'late_entry', 'wrong_side_book'])
def test_prepared_proposal_identity_clock_and_provenance_are_not_optional(change):
    source, snapshot, view, verifier = inputs()
    p = snapshot.proposal
    if change == 'missing_digests': p = replace(p, source_digests=())
    elif change == 'raw_close_key': p = replace(p, decision_ms=p.decision_candle_close_ms)
    elif change == 'late_entry': p = replace(p, entry_due_ms=p.entry_due_ms + 1)
    else: p = replace(p, book_id='wallet')
    source._provider = lambda *_: replace(snapshot, proposal=p)
    assert source.candidates(A, 123) == ()


def test_scheduled_exit_routes_flat_with_actual_loss_without_claiming_tp_parity(tmp_path):
    h, source, snapshot = managed_inputs(tmp_path)
    try:
        value = at_event(source, snapshot, kind='SCHEDULED',phase='OPEN',due=True)
        h.wall.value = h.venue.now.value = value.observed_ms
        h.venue.cancel = lambda ref: OrderOutcome(kind=OutcomeKind.FINAL, ref=ref,
            observed_at_ms=h.wall(), exchange_order_id='9003', status='CANCELED', executed_qty=D(0))
        evaluation, = source.evaluations(A, 123)
        assert h.sink.accept_management(A, M.evaluate(evaluation))
        assert h.drive() == ()
        assert not h.journal.state().intents
        assert h.sink.snapshot().lots[0][1] == '0'
        assert not h.sink._watched
        closing=[event.result for event in h.journal.read() if hasattr(event,'result') and
                 event.result.executed_qty and event.result.avg_price==D(100)]
        assert closing and D(100)<snapshot.lots[0].proposal.entry_reference
        assert S.activation_blockers()                         # flatness is not TP parity or activation
    finally: h.close()


def test_native_source_import_does_not_pull_in_research_or_numpy():
    root = str(Path(__file__).resolve().parents[2])
    result = subprocess.run([sys.executable, '-I', '-c',
        'import sys;sys.path.insert(0,sys.argv[1]);import newcore.runtime.c259_source;'
        'assert "numpy" not in sys.modules;assert not any(n.startswith("tools.research") for n in sys.modules)',
        root], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_native_actual_plus_one_ms_observation_and_later_dispatch_do_not_backdate():
    source,snapshot,view,verifier=inputs(observed_inputs(quote_shift=D('0.00000001')))
    p=snapshot.proposal
    source._wall=lambda:p.prepared_ms+1
    candidate,=source.candidates(A,p.prepared_mono+1)
    assert snapshot.observed_ms==p.entry_due_ms+1
    assert candidate.key.candle_close_ms==p.decision_ms
    assert candidate.evaluation.request.entry_price==p.dispatch_entry_reference
    assert candidate.evaluation.request.entry_price!=p.entry_reference
    assert R.evaluate(candidate.evaluation).quantity<=p.quantity
    source._wall=lambda:p.collector_observed_ms+source.config.runtime_config.max_snapshot_age_s*1000+1
    assert source.candidates(A,p.collector_observed_mono+source.config.runtime_config.max_snapshot_age_s*1000+1)==()


def test_native_dispatch_deadline_bound_plus_one_refuses_even_fresh_observation():
    runtime=helper_fixture()['config'].runtime_config
    source,snapshot,view,verifier=inputs(observed_inputs(runtime.evidence_deadline_ms))
    p=snapshot.proposal
    assert source.candidates(A,p.prepared_mono)
    source._wall=lambda:p.prepared_ms+1
    assert not source.candidates(A,p.prepared_mono+1)


def test_runtime_environment_cannot_be_relabelled_by_source_configuration():
    source,snapshot,view,verifier=inputs()
    source.config=replace(source.config,environment=R.Environment.PAPER)
    assert source.candidates(A,123)==()
    assert source.reasons['UNBOUND_RUNTIME_TIMING_POLICY']==1


@pytest.mark.parametrize('retreated',[True,False])
def test_target_range_never_becomes_a_retroactive_market_exit(tmp_path,retreated):
    h,source,snapshot=managed_inputs(tmp_path)
    try:
        value=at_event(source,snapshot,kind='TARGET_RANGE',target_touch=True)
        if not retreated:
            row=value.lots[0]
            value=replace(value,lots=(replace(row,event=replace(row.event,mark=row.proposal.target)),))
            source._provider=lambda *_:value
        before=h.journal.last_sequence()
        with pytest.raises(S.SourceRefusal,match='UNSUPPORTED_RETROACTIVE_TARGET_RANGE'):
            source.evaluations(A,123)
        assert h.journal.last_sequence()==before
        assert h.sink.snapshot().lots[0][1]!='0' and h.sink._watched
        assert 'FIRST_TOUCH' in S.activation_blockers()[0]
    finally:h.close()


def test_scheduled_canonical_open_and_real_plus_one_observation_are_separate(tmp_path):
    h,source,snapshot=managed_inputs(tmp_path)
    try:
        value=at_event(source,snapshot,kind='SCHEDULED',phase='OPEN',due=True)
        row=value.lots[0];event=row.event.canonical_event_ms
        value=replace(value,observed_ms=event+1,observed_mono=124,
            lots=(replace(row,event=replace(row.event,observed_ms=event+1,observed_mono=124)),))
        source._provider=lambda *_:value;source._wall=lambda:event+2
        evaluation,=source.evaluations(A,125)
        assert type(M.evaluate(evaluation).actions[0]) is M.CloseAction
        assert value.lots[0].event.canonical_event_ms==row.proposal.scheduled_exit_ms
    finally:h.close()
