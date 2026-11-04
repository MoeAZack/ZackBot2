from __future__ import annotations

import ast
import copy
import dataclasses
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from newcore.management import (
    Binding,
    CleanupAction,
    CloseAction,
    FlattenRequired,
    IncreaseKind,
    LotState,
    ManagementEvaluation,
    ManagementPlan,
    ManagementPolicy,
    MarketObservation,
    PathOrder,
    PendingKind,
    PendingSummary,
    PlanReason,
    PlanStatus,
    PositionSide,
    ProtectionRepair,
    ProtectionState,
    ProtectionStatus,
    ReduceAction,
    RiskIncreaseProposal,
    RiskIncreaseRule,
    StopChange,
    StrategyExitFact,
    TargetRule,
    evaluate,
)


D = Decimal


def target(level_id='tp1', trigger='110', fraction='.4'):
    return TargetRule(level_id=level_id, trigger_price=D(trigger),
                      reduce_fraction=D(fraction))


def increase(level_id='dca1', kind=IncreaseKind.DCA, trigger='97', risk='50'):
    return RiskIncreaseRule(level_id=level_id, kind=kind,
                            trigger_price=D(trigger), requested_risk=D(risk))


def policy(**changes):
    row = ManagementPolicy(
        policy_id='management-v1', version='v1', generation=7, fresh=True,
        tick_size=D('.1'), quantity_step=D('.1'), min_quantity=D('.1'),
        targets=(target(), target('tp2', '120', '.4')),
        preserve_runner=True, runner_quantity=D('2'),
        breakeven_trigger_r=D('1'), breakeven_offset_ticks=D('1'),
        trail_trigger_r=D('2'), trail_distance_r=D('1'),
        max_holding_bars=100, max_add_count=3,
        max_single_add_risk=D('50'), max_cumulative_add_risk=D('100'),
        risk_increases=(increase(trigger='96.5'),
                        increase('dca2', IncreaseKind.DCA, '96', '40'),
                        increase('pyramid1', IncreaseKind.PYRAMID, '108', '30')))
    return dataclasses.replace(row, **changes)


def lot(**changes):
    row = LotState(
        lot_id='lot_' + 'a' * 32, strategy_id='strategy-a', generation=5, fresh=True,
        side=PositionSide.LONG, quantity=D('10'), initial_quantity=D('10'),
        entry_price=D('100'), initial_stop=D('95'), initial_risk_per_unit=D('5'),
        completed_target_levels=(), completed_risk_levels=(),
        breakeven_applied=False, trail_applied=False, completed_bars_held=10,
        add_count=0, cumulative_add_risk=D('0'))
    return dataclasses.replace(row, **changes)


def market(**changes):
    row = MarketObservation(
        observation_id='bar_' + 'b' * 32, generation=11, fresh=True,
        completed=True, high=D('103'), low=D('97'), mark=D('100'),
        path_order=PathOrder.NONE, forced_exit_confirmed=False)
    return dataclasses.replace(row, **changes)


def protection(**changes):
    row = ProtectionState(
        protection_id='protect_' + 'c' * 32, generation=13, fresh=True,
        status=ProtectionStatus.WORKING, confirmed_stop=D('95'),
        confirmed_quantity=D('10'), confirmed_target_levels=())
    return dataclasses.replace(row, **changes)


def strategy(**changes):
    row = StrategyExitFact(version='signal-v1', generation=17, fresh=True,
                           exit_requested=False)
    return dataclasses.replace(row, **changes)


def pending(**changes):
    row = PendingSummary(generation=19, fresh=True, kind=PendingKind.NONE)
    return dataclasses.replace(row, **changes)


def evaluation(*, l=None, p=None, m=None, protect=None, s=None, pend=None, **changes):
    row = ManagementEvaluation(
        lot_generation=5, policy_generation=7, market_generation=11,
        protection_generation=13, strategy_exit_generation=17,
        pending_generation=19, lot=l or lot(), policy=p or policy(),
        market=m or market(), protection=protect or protection(),
        strategy_exit=s or strategy(), pending=pend or pending())
    return dataclasses.replace(row, **changes)


def plan(ev=None):
    result = evaluate(ev or evaluation())
    assert type(result) is ManagementPlan
    return result


def assert_reason(ev, status, reason):
    result = plan(ev)
    assert result.status is status
    assert result.reason is reason
    return result


def test_idle_plan_is_deterministic_generation_bound_and_closed():
    ev = evaluation()
    first = plan(ev)
    second = plan(ev)
    assert first == second
    assert first.plan_id == second.plan_id
    assert first.plan_id.startswith('mgt_') and len(first.plan_id) == 36
    assert first.status is PlanStatus.HOLD
    assert first.reason is PlanReason.NO_CHANGE
    assert first.actions == ()
    assert (first.lot_generation, first.policy_generation, first.market_generation,
            first.protection_generation, first.strategy_exit_generation,
            first.pending_generation) == (5, 7, 11, 13, 17, 19)
    assert (first.lot_id, first.policy_id, first.policy_version) == (
        ev.lot.lot_id, 'management-v1', 'v1')
    with pytest.raises(TypeError):
        ManagementPlan()
    with pytest.raises(TypeError):
        dataclasses.replace(first, reason=PlanReason.TIME_EXIT)
    assert copy.copy(first) == first


def test_decimal_spelling_and_ambient_context_do_not_change_plan():
    ev = evaluation()
    equivalent = dataclasses.replace(
        ev, lot=dataclasses.replace(ev.lot, entry_price=D('100.00'),
                                    initial_stop=D('95.000'),
                                    initial_risk_per_unit=D('5.0')))
    with localcontext() as context:
        context.prec = 7
        assert plan(ev) == plan(equivalent)


@pytest.mark.parametrize(('field', 'value'), [
    ('lot_generation', 6), ('policy_generation', 8), ('market_generation', 12),
    ('protection_generation', 14), ('strategy_exit_generation', 18),
    ('pending_generation', 20),
])
def test_every_generation_mismatch_holds(field, value):
    result = assert_reason(dataclasses.replace(evaluation(), **{field: value}),
                           PlanStatus.HOLD, PlanReason.GENERATION_MISMATCH)
    assert result.actions == ()


@pytest.mark.parametrize('component', ['lot', 'policy', 'market', 'protection',
                                        'strategy_exit', 'pending'])
def test_every_stale_fact_holds(component):
    ev = evaluation()
    stale = dataclasses.replace(getattr(ev, component), fresh=False)
    assert_reason(dataclasses.replace(ev, **{component: stale}),
                  PlanStatus.HOLD, PlanReason.STALE_INPUT)


@pytest.mark.parametrize('value', [1, 1.0, D('NaN'), D('sNaN'), D('Infinity'), D('-1')])
def test_non_exact_non_finite_and_negative_decimal_input_is_sanitized(value):
    ev = evaluation(l=lot(quantity=value))
    result = assert_reason(ev, PlanStatus.HOLD, PlanReason.INPUT_INVALID)
    assert not hasattr(result, 'quantity')


def test_missing_protection_has_highest_priority_and_is_place_before_cancel():
    missing = protection(status=ProtectionStatus.MISSING, confirmed_stop=None,
                         confirmed_quantity=D('0'))
    ev = evaluation(
        protect=missing, m=market(high=D('130'), forced_exit_confirmed=True),
        s=strategy(exit_requested=True), l=lot(completed_bars_held=100))
    result = assert_reason(ev, PlanStatus.ACTIONS, PlanReason.PROTECTION_MISSING)
    assert result.bindings == (Binding.PROTECTION,)
    assert len(result.actions) == 1 and type(result.actions[0]) is ProtectionRepair
    action = result.actions[0]
    assert (action.stop_price, action.quantity, action.place_before_cancel) == (
        D('95'), D('10'), True)
    with pytest.raises(TypeError):
        ProtectionRepair()


@pytest.mark.parametrize(('side', 'entry', 'initial_stop', 'risk', 'expected'), [
    (PositionSide.LONG, '100', '94.96', '5.04', '94.9'),
    (PositionSide.SHORT, '100', '105.04', '5.04', '105.1'),
])
def test_repair_stop_quantizes_conservatively_for_both_sides(
        side, entry, initial_stop, risk, expected):
    empty_policy = policy(targets=(), risk_increases=(), max_add_count=0,
                          max_single_add_risk=D('0'), max_cumulative_add_risk=D('0'))
    missing = protection(status=ProtectionStatus.MISSING, confirmed_stop=None,
                         confirmed_quantity=D('0'))
    subject = lot(side=side, entry_price=D(entry), initial_stop=D(initial_stop),
                  initial_risk_per_unit=D(risk))
    result = assert_reason(evaluation(l=subject, p=empty_policy, protect=missing),
                           PlanStatus.ACTIONS, PlanReason.PROTECTION_MISSING)
    assert result.actions[0].stop_price == D(expected)
    if side is PositionSide.LONG:
        assert result.actions[0].stop_price < market().mark
    else:
        assert result.actions[0].stop_price > market().mark


def test_undersized_working_protection_is_repaired_and_pending_repair_is_not_duplicated():
    undersized = protection(confirmed_quantity=D('9'))
    repaired = assert_reason(evaluation(protect=undersized), PlanStatus.ACTIONS,
                             PlanReason.PROTECTION_UNDERSIZED)
    assert type(repaired.actions[0]) is ProtectionRepair
    waiting = assert_reason(evaluation(protect=undersized,
                                       pend=pending(kind=PendingKind.PROTECTION)),
                            PlanStatus.HOLD, PlanReason.PENDING_INTENT)
    assert waiting.actions == ()


def test_undersized_repair_preserves_an_already_improved_stop():
    improved = protection(confirmed_stop=D('100.1'), confirmed_quantity=D('9'))
    result = assert_reason(evaluation(protect=improved,
                                      m=market(low=D('100.2'), mark=D('101'))),
                           PlanStatus.ACTIONS,
                           PlanReason.PROTECTION_UNDERSIZED)
    assert result.actions[0].stop_price == D('100.1')
    assert result.actions[0].stop_price >= improved.confirmed_stop


def test_explicit_undersized_state_must_actually_be_undersized():
    contradictory = protection(status=ProtectionStatus.UNDERSIZED,
                               confirmed_quantity=D('10'))
    assert_reason(evaluation(protect=contradictory), PlanStatus.HOLD,
                  PlanReason.INPUT_INVALID)
    oversized = protection(confirmed_quantity=D('11'))
    assert_reason(evaluation(protect=oversized), PlanStatus.HOLD,
                  PlanReason.INPUT_INVALID)


def test_forced_exit_precedes_strategy_target_stop_and_risk_increase():
    ev = evaluation(m=market(high=D('130'), low=D('94'), mark=D('100'),
                             path_order=PathOrder.UNORDERED,
                             forced_exit_confirmed=True),
                    s=strategy(exit_requested=True))
    result = assert_reason(ev, PlanStatus.ACTIONS, PlanReason.FORCED_EXIT)
    assert len(result.actions) == 1 and type(result.actions[0]) is CloseAction
    assert result.actions[0].quantity == D('10')


def test_strategy_exit_is_versioned_and_precedes_target_time_and_add():
    ev = evaluation(s=strategy(exit_requested=True),
                    m=market(high=D('130')), l=lot(completed_bars_held=100))
    result = assert_reason(ev, PlanStatus.ACTIONS, PlanReason.STRATEGY_EXIT)
    assert type(result.actions[0]) is CloseAction
    assert result.bindings == (Binding.STRATEGY_EXIT,)


def test_pending_close_prevents_duplicate_but_other_pending_is_bound_on_forced_close():
    waiting = assert_reason(evaluation(m=market(forced_exit_confirmed=True),
                                       pend=pending(kind=PendingKind.CLOSE)),
                            PlanStatus.HOLD, PlanReason.PENDING_INTENT)
    assert waiting.actions == ()
    forced = assert_reason(evaluation(m=market(forced_exit_confirmed=True),
                                      pend=pending(kind=PendingKind.ADD)),
                           PlanStatus.ACTIONS, PlanReason.FORCED_EXIT)
    assert Binding.PENDING_INTENT in forced.bindings
    assert type(forced.actions[0]) is CloseAction


def test_two_crossed_targets_reduce_once_each_and_preserve_exact_runner():
    result = assert_reason(evaluation(m=market(high=D('125'))), PlanStatus.ACTIONS,
                           PlanReason.TARGET_REDUCE)
    assert len(result.actions) == 2
    assert all(type(action) is ReduceAction for action in result.actions)
    assert [(action.level_id, action.quantity, action.remaining_quantity)
            for action in result.actions] == [
                ('tp1', D('4'), D('6')), ('tp2', D('4'), D('2'))]
    assert sum((action.quantity for action in result.actions), D('0')) == D('8')
    assert result.actions[-1].remaining_quantity == D('2')
    assert Binding.RUNNER in result.bindings


def test_completed_target_never_executes_twice_and_reduction_never_exceeds_capacity():
    once = assert_reason(evaluation(l=lot(quantity=D('6'),
                                              completed_target_levels=('tp1',)),
                                       protect=protection(confirmed_quantity=D('6')),
                                       m=market(high=D('125'))),
                         PlanStatus.ACTIONS, PlanReason.TARGET_REDUCE)
    assert [(action.level_id, action.quantity, action.remaining_quantity)
            for action in once.actions] == [('tp2', D('4'), D('2'))]
    aggressive = policy(targets=(target('a', '110', '.8'), target('b', '120', '.8')))
    capped = assert_reason(evaluation(p=aggressive, m=market(high=D('125'))),
                           PlanStatus.ACTIONS, PlanReason.TARGET_REDUCE)
    assert [(action.level_id, action.quantity, action.remaining_quantity)
            for action in capped.actions] == [('a', D('8'), D('2'))]


def test_crossed_working_target_waits_for_confirmed_exchange_state():
    working = protection(confirmed_target_levels=('tp1',))
    result = assert_reason(evaluation(protect=working, m=market(high=D('115'))),
                           PlanStatus.RECONCILE,
                           PlanReason.TARGET_CONFIRMATION_REQUIRED)
    assert result.actions == ()
    assert result.bindings == (Binding.TARGET,)


def test_unknown_or_duplicate_completed_level_refuses_instead_of_replaying():
    assert_reason(evaluation(l=lot(completed_target_levels=('not-a-level',))),
                  PlanStatus.HOLD, PlanReason.INPUT_INVALID)
    assert_reason(evaluation(l=lot(completed_risk_levels=('dca1',), add_count=0)),
                  PlanStatus.HOLD, PlanReason.INPUT_INVALID)


def test_unordered_stop_and_target_touch_is_ambiguous_and_emits_no_action():
    ev = evaluation(m=market(high=D('115'), low=D('94'), mark=D('100'),
                             path_order=PathOrder.UNORDERED))
    result = assert_reason(ev, PlanStatus.AMBIGUOUS, PlanReason.AMBIGUOUS_TOUCH)
    assert result.actions == ()
    assert result.bindings == (Binding.PROTECTION, Binding.TARGET)


def test_explicit_target_first_path_can_reduce_without_favorable_order_guessing():
    result = assert_reason(evaluation(m=market(high=D('115'), low=D('94'), mark=D('100'),
                                              path_order=PathOrder.TARGET_FIRST)),
                           PlanStatus.ACTIONS, PlanReason.TARGET_REDUCE)
    assert [action.level_id for action in result.actions] == ['tp1']


@pytest.mark.parametrize(('subject', 'rules', 'guard', 'observation'), [
    (lot(), policy(), protection(),
     market(high=D('115'), low=D('94'), mark=D('94'),
            path_order=PathOrder.TARGET_FIRST)),
    (lot(side=PositionSide.SHORT, initial_stop=D('105')),
     policy(targets=(target('tp1', '90', '.4'), target('tp2', '80', '.4')),
            risk_increases=(increase('dca1', IncreaseKind.DCA, '103', '50'),
                            increase('pyramid1', IncreaseKind.PYRAMID, '92', '30'))),
     protection(confirmed_stop=D('105')),
     market(high=D('106'), low=D('89'), mark=D('106'),
            path_order=PathOrder.TARGET_FIRST)),
])
def test_target_first_later_gap_flattens_for_both_sides(
        subject, rules, guard, observation):
    result = assert_reason(evaluation(l=subject, p=rules, protect=guard, m=observation),
                           PlanStatus.RECONCILE, PlanReason.FLATTEN_REQUIRED)
    assert len(result.actions) == 1 and type(result.actions[0]) is FlattenRequired
    assert not any(type(action) is ReduceAction for action in result.actions)


@pytest.mark.parametrize(('subject', 'rules', 'guard', 'observation'), [
    (lot(), policy(),
     protection(status=ProtectionStatus.MISSING, confirmed_stop=None,
                confirmed_quantity=D('0')),
     market(low=D('94'), mark=D('94'))),
    (lot(), policy(), protection(confirmed_quantity=D('9')),
     market(low=D('94'), mark=D('94'))),
    (lot(side=PositionSide.SHORT, initial_stop=D('105')),
     policy(targets=(target('tp1', '90', '.4'), target('tp2', '80', '.4')),
            risk_increases=(increase('dca1', IncreaseKind.DCA, '103', '50'),
                            increase('pyramid1', IncreaseKind.PYRAMID, '92', '30'))),
     protection(status=ProtectionStatus.MISSING, confirmed_stop=None,
                confirmed_quantity=D('0')),
     market(high=D('106'), mark=D('106'))),
    (lot(side=PositionSide.SHORT, initial_stop=D('105')),
     policy(targets=(target('tp1', '90', '.4'), target('tp2', '80', '.4')),
            risk_increases=(increase('dca1', IncreaseKind.DCA, '103', '50'),
                            increase('pyramid1', IncreaseKind.PYRAMID, '92', '30'))),
     protection(confirmed_stop=D('105'), confirmed_quantity=D('9')),
     market(high=D('106'), mark=D('106'))),
])
def test_missing_or_undersized_protection_gap_flattens_before_repair(
        subject, rules, guard, observation):
    result = assert_reason(evaluation(l=subject, p=rules, protect=guard, m=observation),
                           PlanStatus.RECONCILE, PlanReason.FLATTEN_REQUIRED)
    assert len(result.actions) == 1 and type(result.actions[0]) is FlattenRequired
    assert not any(type(action) is ProtectionRepair for action in result.actions)


def test_gap_suppresses_strategy_exit_but_confirmed_forced_exit_remains_explicit():
    gap = market(low=D('94'), mark=D('94'))
    unresolved = assert_reason(evaluation(m=gap, s=strategy(exit_requested=True)),
                               PlanStatus.RECONCILE, PlanReason.FLATTEN_REQUIRED)
    assert type(unresolved.actions[0]) is FlattenRequired
    confirmed = assert_reason(evaluation(
        m=dataclasses.replace(gap, forced_exit_confirmed=True)),
        PlanStatus.ACTIONS, PlanReason.FORCED_EXIT)
    assert type(confirmed.actions[0]) is CloseAction


def test_stop_touch_is_never_inferred_as_fill_and_gap_requires_flatten():
    touched = assert_reason(evaluation(m=market(low=D('94'), mark=D('100'),
                                               path_order=PathOrder.STOP_FIRST)),
                            PlanStatus.RECONCILE,
                            PlanReason.STOP_CONFIRMATION_REQUIRED)
    assert touched.actions == ()
    gap = assert_reason(evaluation(m=market(low=D('90'), mark=D('94'),
                                           path_order=PathOrder.STOP_FIRST)),
                        PlanStatus.RECONCILE, PlanReason.FLATTEN_REQUIRED)
    assert len(gap.actions) == 1 and type(gap.actions[0]) is FlattenRequired
    assert gap.actions[0].quantity == D('10')


def test_unknown_protection_forbids_add_and_beyond_stop_requires_flatten():
    unknown = protection(status=ProtectionStatus.UNKNOWN, confirmed_stop=None,
                         confirmed_quantity=D('0'))
    held = assert_reason(evaluation(protect=unknown, m=market(low=D('97'))),
                         PlanStatus.RECONCILE, PlanReason.PROTECTION_UNKNOWN)
    assert held.actions == ()
    gap = assert_reason(evaluation(protect=unknown, m=market(low=D('94'), mark=D('94'))),
                        PlanStatus.RECONCILE, PlanReason.FLATTEN_REQUIRED)
    assert type(gap.actions[0]) is FlattenRequired

    mark_gap = assert_reason(evaluation(
        protect=unknown, m=market(completed=False, low=D('97'), mark=D('94'))),
        PlanStatus.RECONCILE, PlanReason.FLATTEN_REQUIRED)
    assert type(mark_gap.actions[0]) is FlattenRequired


def test_explicit_strategy_close_is_not_blocked_by_unknown_protection():
    unknown = protection(status=ProtectionStatus.UNKNOWN, confirmed_stop=None,
                         confirmed_quantity=D('0'))
    result = assert_reason(evaluation(protect=unknown, s=strategy(exit_requested=True)),
                           PlanStatus.ACTIONS, PlanReason.STRATEGY_EXIT)
    assert type(result.actions[0]) is CloseAction


def test_breakeven_precedes_trail_and_is_monotonic_tick_quantized():
    ev = evaluation(l=lot(completed_target_levels=('tp1',)),
                    m=market(high=D('112'), low=D('97'), mark=D('110')))
    result = assert_reason(ev, PlanStatus.ACTIONS, PlanReason.BREAKEVEN)
    action = result.actions[0]
    assert type(action) is StopChange
    assert (action.replaces_stop, action.stop_price, action.place_before_cancel) == (
        D('95'), D('100.1'), True)
    assert action.stop_price > action.replaces_stop
    assert action.stop_price % D('.1') == 0


def test_trail_uses_completed_extreme_and_never_loosens_long_stop():
    ev = evaluation(l=lot(breakeven_applied=True, completed_target_levels=('tp1',)),
                    m=market(high=D('112.09'), low=D('101'), mark=D('110')),
                    protect=protection(confirmed_stop=D('100.1')))
    result = assert_reason(ev, PlanStatus.ACTIONS, PlanReason.TRAIL)
    action = result.actions[0]
    assert action.stop_price == D('107.0')
    assert action.stop_price > action.replaces_stop
    already_tighter = evaluation(l=lot(breakeven_applied=True,
                                       completed_target_levels=('tp1',)),
                                 p=policy(risk_increases=(), max_add_count=0,
                                          max_single_add_risk=D('0'),
                                          max_cumulative_add_risk=D('0')),
                                 m=market(high=D('112'), low=D('108.1'), mark=D('110')),
                                 protect=protection(confirmed_stop=D('108')))
    assert_reason(already_tighter, PlanStatus.HOLD, PlanReason.NO_CHANGE)


def test_short_breakeven_and_trail_are_symmetric_and_round_up():
    short_lot = lot(side=PositionSide.SHORT, initial_stop=D('105'))
    short_policy = policy(targets=(target('tp1', '90', '.4'), target('tp2', '80', '.4')),
                          risk_increases=(increase('dca1', IncreaseKind.DCA, '103', '50'),
                                          increase('pyramid1', IncreaseKind.PYRAMID, '92', '30')))
    be = assert_reason(evaluation(l=dataclasses.replace(
                                      short_lot, completed_target_levels=('tp1',)),
                                  p=short_policy,
                                  m=market(high=D('103'), low=D('88'), mark=D('90')),
                                  protect=protection(confirmed_stop=D('105'))),
                       PlanStatus.ACTIONS, PlanReason.BREAKEVEN)
    assert be.actions[0].stop_price == D('99.9')
    trailed = assert_reason(evaluation(
        l=dataclasses.replace(short_lot, breakeven_applied=True,
                              completed_target_levels=('tp1',)), p=short_policy,
        m=market(high=D('99'), low=D('87.91'), mark=D('90')),
        protect=protection(confirmed_stop=D('99.9'))),
        PlanStatus.ACTIONS, PlanReason.TRAIL)
    assert trailed.actions[0].stop_price == D('93.0')
    assert trailed.actions[0].stop_price < trailed.actions[0].replaces_stop


def test_short_target_and_risk_increase_transitions_are_symmetric():
    short_lot = lot(side=PositionSide.SHORT, initial_stop=D('105'))
    short_policy = policy(
        targets=(target('tp1', '90', '.4'), target('tp2', '80', '.4')),
        risk_increases=(increase('dca1', IncreaseKind.DCA, '103', '50'),
                        increase('pyramid1', IncreaseKind.PYRAMID, '92', '30')))
    reduced = assert_reason(evaluation(
        l=short_lot, p=short_policy,
        m=market(high=D('101'), low=D('89'), mark=D('95')),
        protect=protection(confirmed_stop=D('105'))),
        PlanStatus.ACTIONS, PlanReason.TARGET_REDUCE)
    assert [(action.level_id, action.quantity, action.remaining_quantity)
            for action in reduced.actions] == [('tp1', D('4'), D('6'))]

    dca = assert_reason(evaluation(
        l=short_lot, p=short_policy,
        m=market(high=D('103'), low=D('98'), mark=D('101')),
        protect=protection(confirmed_stop=D('105'))),
        PlanStatus.ACTIONS, PlanReason.RISK_INCREASE_PROPOSAL)
    assert (dca.actions[0].kind, dca.actions[0].level_id) == (
        IncreaseKind.DCA, 'dca1')

    pyramid = assert_reason(evaluation(
        l=dataclasses.replace(short_lot, breakeven_applied=True), p=short_policy,
        m=market(high=D('99'), low=D('92'), mark=D('95')),
        protect=protection(confirmed_stop=D('99.9'))),
        PlanStatus.ACTIONS, PlanReason.RISK_INCREASE_PROPOSAL)
    assert (pyramid.actions[0].kind, pyramid.actions[0].level_id) == (
        IncreaseKind.PYRAMID, 'pyramid1')


def test_incomplete_observation_cannot_trigger_target_breakeven_trail_or_add():
    result = assert_reason(evaluation(m=market(completed=False, high=D('130'), low=D('96'))),
                           PlanStatus.HOLD, PlanReason.NO_CHANGE)
    assert result.actions == ()


def test_time_exit_uses_explicit_completed_bar_count_after_stop_improvements():
    due = assert_reason(evaluation(l=lot(completed_bars_held=100)),
                        PlanStatus.ACTIONS, PlanReason.TIME_EXIT)
    assert type(due.actions[0]) is CloseAction
    not_due = assert_reason(evaluation(l=lot(completed_bars_held=99)),
                            PlanStatus.HOLD, PlanReason.NO_CHANGE)
    assert not_due.actions == ()


def test_dca_proposal_is_bounded_non_executable_and_requires_fresh_nc06():
    result = assert_reason(evaluation(m=market(low=D('96.5'), mark=D('98'))),
                           PlanStatus.ACTIONS, PlanReason.RISK_INCREASE_PROPOSAL)
    proposal = result.actions[0]
    assert type(proposal) is RiskIncreaseProposal
    assert (proposal.kind, proposal.level_id, proposal.requested_risk) == (
        IncreaseKind.DCA, 'dca1', D('50'))
    assert proposal.requires_nc06_authorization is True
    assert proposal.policy_generation == 7
    assert not hasattr(proposal, 'quantity')
    assert Binding.NC06_AUTHORIZATION in result.bindings


def test_pyramid_proposal_and_completed_level_advance_are_deterministic():
    completed = lot(completed_risk_levels=('dca1', 'dca2'), add_count=2,
                    cumulative_add_risk=D('90'))
    result = assert_reason(evaluation(l=completed,
                                      p=policy(max_cumulative_add_risk=D('120')),
                                      m=market(high=D('109'), low=D('98'))),
                           PlanStatus.ACTIONS, PlanReason.RISK_INCREASE_PROPOSAL)
    proposal = result.actions[0]
    assert proposal.kind is IncreaseKind.PYRAMID
    assert proposal.level_id == 'pyramid1'
    assert proposal.requested_risk == D('30')


@pytest.mark.parametrize('changed_lot', [
    lot(add_count=3, completed_risk_levels=('dca1', 'dca2', 'pyramid1'),
        cumulative_add_risk=D('90')),
    lot(add_count=2, completed_risk_levels=('dca1', 'dca2'),
        cumulative_add_risk=D('80')),
])
def test_add_count_and_cumulative_risk_caps_never_expand(changed_lot):
    result = plan(evaluation(l=changed_lot, m=market(low=D('97'), high=D('109'))))
    assert not any(type(action) is RiskIncreaseProposal for action in result.actions)


def test_martingale_policy_and_exceeded_existing_add_limits_are_invalid():
    martingale = policy(risk_increases=(increase('a', risk='10'),
                                        increase('b', trigger='96', risk='20')))
    assert_reason(evaluation(p=martingale), PlanStatus.HOLD, PlanReason.INPUT_INVALID)
    exceeded = lot(add_count=1, completed_risk_levels=('dca1',),
                   cumulative_add_risk=D('101'))
    assert_reason(evaluation(l=exceeded), PlanStatus.HOLD, PlanReason.INPUT_INVALID)


@pytest.mark.parametrize('bad_policy', [
    policy(targets=(target('far', '120'), target('near', '110'))),
    policy(risk_increases=(increase('past-stop', trigger='94'),)),
    policy(risk_increases=(increase('pyramid', IncreaseKind.PYRAMID, '99', '20'),)),
])
def test_ladder_order_and_risk_increase_price_zones_are_fail_closed(bad_policy):
    assert_reason(evaluation(p=bad_policy), PlanStatus.HOLD, PlanReason.INPUT_INVALID)


@pytest.mark.parametrize('kind', [PendingKind.PROTECTION, PendingKind.REDUCE,
                                  PendingKind.STOP_CHANGE, PendingKind.ADD])
def test_pending_intents_suppress_non_close_transitions(kind):
    result = assert_reason(evaluation(m=market(high=D('125')),
                                      pend=pending(kind=kind)),
                           PlanStatus.HOLD, PlanReason.PENDING_INTENT)
    assert result.actions == ()


def test_unknown_pending_state_reconciles():
    result = assert_reason(evaluation(pend=pending(kind=PendingKind.UNKNOWN)),
                           PlanStatus.RECONCILE, PlanReason.PENDING_INTENT)
    assert result.actions == ()


def test_finished_lot_only_cleans_up_or_reconciles():
    cleaned = assert_reason(evaluation(l=lot(quantity=D('0'))),
                            PlanStatus.ACTIONS, PlanReason.CLEANUP_REQUIRED)
    assert len(cleaned.actions) == 1 and type(cleaned.actions[0]) is CleanupAction
    missing = protection(status=ProtectionStatus.MISSING, confirmed_stop=None,
                         confirmed_quantity=D('0'))
    finished = assert_reason(evaluation(l=lot(quantity=D('0')), protect=missing),
                             PlanStatus.HOLD, PlanReason.LOT_FINISHED)
    assert finished.actions == ()
    unknown = protection(status=ProtectionStatus.UNKNOWN, confirmed_stop=None,
                         confirmed_quantity=D('0'))
    assert_reason(evaluation(l=lot(quantity=D('0')), protect=unknown),
                  PlanStatus.RECONCILE, PlanReason.LOT_FINISHED)


@pytest.mark.parametrize('ev', [
    evaluation(l=lot(quantity=D('2'), breakeven_applied=True,
                     completed_target_levels=('tp1', 'tp2')),
               m=market(high=D('1E+999999'), low=D('97'), mark=D('100'))),
    evaluation(p=policy(tick_size=D('1E-999999')),
               protect=protection(status=ProtectionStatus.MISSING,
                                  confirmed_stop=None, confirmed_quantity=D('0'))),
])
def test_decimal_extremes_and_trapped_arithmetic_return_stable_input_invalid(ev):
    result = evaluate(ev)
    assert type(result) is ManagementPlan
    assert result.status is PlanStatus.HOLD
    assert result.reason is PlanReason.INPUT_INVALID
    assert result.actions == ()


def test_outputs_are_immutable_tuples_without_mutable_or_callable_references():
    result = plan(evaluation(m=market(high=D('125'))))
    assert type(result.actions) is tuple and type(result.bindings) is tuple
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = PlanStatus.HOLD
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.actions[0].quantity = D('0')
    assert not any(callable(getattr(action, field.name))
                   for action in result.actions for field in dataclasses.fields(action))
    with pytest.raises(TypeError):
        class ForgedClose(CloseAction):
            pass


def test_malformed_values_are_not_echoed_in_closed_output():
    result = evaluate(evaluation(l=lot(quantity='SECRET_RAW_VALUE')))
    assert type(result) is ManagementPlan
    assert result.reason is PlanReason.INPUT_INVALID
    assert 'SECRET_RAW_VALUE' not in repr(result)


def test_import_boundary_is_stdlib_only_and_runtime_free():
    path = Path(__file__).parents[2] / 'newcore' / 'management.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or '')
    assert not [name for name in imports
                if name.startswith(('newcore', 'legacy', 'requests', 'aiohttp'))]
