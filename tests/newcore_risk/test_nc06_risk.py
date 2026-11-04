from __future__ import annotations

import ast
import copy
import dataclasses
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from newcore.risk import (
    AccountRiskSnapshot,
    Allocation,
    ApprovedDecision,
    Ceiling,
    CorrelationAssignment,
    CorrelationCap,
    DailyLossState,
    Environment,
    Exposure,
    FollowerProfile,
    LossLimits,
    OpeningReservation,
    PortfolioRiskSnapshot,
    PositionSide,
    RefusalCode,
    RefusedDecision,
    RiskAction,
    RiskEvaluation,
    RiskPolicySnapshot,
    RiskRequest,
    VenueRiskFilters,
    authorization_is_current,
    evaluate,
    requires_authorization,
)


D = Decimal
ACCOUNT = 'acct_' + 'a' * 32


def account(**changes):
    row = AccountRiskSnapshot(
        account_id=ACCOUNT, environment=Environment.TESTNET, generation=3,
        confirmed=True, fresh=True, hedge_mode=True, equity=D('10000'),
        available_margin=D('5000'), reserved_capital=D('0'))
    return dataclasses.replace(row, **changes)


def allocation(**changes):
    row = Allocation(strategy_id='strategy-a', book_id='book-a',
                     notional_fraction=D('.5'), risk_fraction=D('.1'))
    return dataclasses.replace(row, **changes)


# NC-06 fixtures keep the RISK-LOSS-01 limits non-binding so these tests keep
# exercising the original ceilings; tests/newcore_risk/test_risk_loss01.py owns
# the loss-limit behavior.
def loss_limits(**changes):
    row = LossLimits(daily_loss_fraction=D('1'), daily_loss_r=D('1000'),
                     risk_unit_fraction=D('.01'), max_bucket_side_risk_r=D('1000'),
                     one_position_per_symbol_side=True)
    return dataclasses.replace(row, **changes)


def daily(**changes):
    row = DailyLossState(trading_day='2026-11-05', day_start_equity=D('10000'),
                         realized_pnl=D('0'), unrealized_pnl=D('0'), halt_latched=False)
    return dataclasses.replace(row, **changes)


def policy(**changes):
    row = RiskPolicySnapshot(
        policy_id='policy-v1', account_id=ACCOUNT, environment=Environment.TESTNET,
        generation=5, fresh=True, capital_cap=D('5000'),
        max_trade_risk_fraction=D('.05'), max_portfolio_risk_fraction=D('.2'),
        max_symbol_side_risk_fraction=D('.1'), max_gross_multiple=D('2'),
        max_net_multiple=D('1'), max_symbol_fraction=D('.5'),
        max_symbol_side_fraction=D('.5'), max_leverage=D('10'),
        allocations=(allocation(),),
        correlation_map=(CorrelationAssignment(symbol='BTCUSDT', bucket='crypto-major'),
                         CorrelationAssignment(symbol='ETHUSDT', bucket='other-eth'),
                         CorrelationAssignment(symbol='SOLUSDT', bucket='other-sol')),
        correlation_caps=(CorrelationCap(bucket='crypto-major', exposure_fraction=D('.75')),
                          CorrelationCap(bucket='other-eth', exposure_fraction=D('1')),
                          CorrelationCap(bucket='other-sol', exposure_fraction=D('1'))),
        loss_limits=loss_limits())
    return dataclasses.replace(row, **changes)


# Policy with RISK-LOSS-01 one-position-per-symbol-side switched off, so the
# original same-symbol-side ceilings stay reachable in these NC-06 tests.
SHARED_SIDE = policy(loss_limits=loss_limits(one_position_per_symbol_side=False))


def filters(**changes):
    row = VenueRiskFilters(
        filter_id='binance-btc-v1', symbol='BTCUSDT', generation=7,
        fresh=True, verified=True, step_size=D('.1'), tick_size=D('.1'),
        min_quantity=D('.1'), max_quantity=D('1000'), min_notional=D('5'),
        max_notional=D('100000'), max_leverage=D('10'))
    return dataclasses.replace(row, **changes)


def request(**changes):
    row = RiskRequest(
        request_id='req_' + 'b' * 32, account_id=ACCOUNT, environment=Environment.TESTNET,
        account_generation=3, portfolio_generation=11, policy_generation=5, filter_generation=7,
        action=RiskAction.ENTRY, strategy_id='strategy-a', book_id='book-a',
        symbol='BTCUSDT', side=PositionSide.LONG, entry_price=D('100'),
        stop_price=D('95'), requested_risk=D('100'), requested_leverage=D('5'))
    return dataclasses.replace(row, **changes)


def exposure(*, symbol='ETHUSDT', side=PositionSide.LONG, strategy='other', book='other',
             qty='1', notional='1', risk='0'):
    return Exposure(symbol=symbol, side=side, strategy_id=strategy, book_id=book,
                    quantity=D(qty), notional=D(notional), risk=D(risk))


def reservation(*, request_id=None, auth=None, symbol='BTCUSDT', side=PositionSide.LONG,
                strategy='strategy-a', book='book-a', qty='1', notional='100', risk='5'):
    return OpeningReservation(
        request_id=request_id or 'req_' + 'c' * 32,
        authorization_id=auth or 'dec_' + 'd' * 32,
        symbol=symbol, side=side, strategy_id=strategy, book_id=book,
        quantity=D(qty), notional=D(notional), risk=D(risk), margin=D('20'))


def portfolio(*, exposures=(), reservations=(), **changes):
    row = PortfolioRiskSnapshot(account_id=ACCOUNT, generation=11, fresh=True,
                                exposures=tuple(exposures), reservations=tuple(reservations),
                                daily=daily())
    return dataclasses.replace(row, **changes)


def follower(profile_id, capital, **changes):
    value = D(str(capital))
    row = FollowerProfile(
        profile_id=profile_id, generation=1, fresh=True, capital=value,
        reserved_capital=D('0'), available_margin=value, gross_notional=D('0'),
        reserved_margin=D('0'),
        net_notional=D('0'), symbol_notional=D('0'), symbol_side_notional=D('0'),
        correlation_notional=D('0'), allocation_notional=D('0'),
        portfolio_risk=D('0'), symbol_side_risk=D('0'), allocation_risk=D('0'),
        leverage_ceiling=D('10'))
    return dataclasses.replace(row, **changes)


def evaluation(*, a=None, p=None, pol=None, f=None, r=None, followers=()):
    return RiskEvaluation(account=a or account(), portfolio=p or portfolio(),
                          policy=pol or policy(), filters=f or filters(),
                          request=r or request(), followers=tuple(followers))


def approved(ev=None):
    result = evaluate(ev or evaluation())
    assert type(result) is ApprovedDecision
    return result


def refused(ev, reason):
    result = evaluate(ev)
    assert type(result) is RefusedDecision
    assert reason in result.reasons
    assert not hasattr(result, 'quantity')
    return result


def test_basic_plan_is_exact_decimal_and_generation_bound():
    result = approved()
    assert result.authorization_id == result.decision_id
    assert result.authorization_id.startswith('dec_') and len(result.authorization_id) == 36
    assert result.quantity == D('20')
    assert result.notional == D('2000')
    assert result.risk == D('100')
    assert result.margin == D('400')
    assert result.leverage == D('5')
    assert result.controlled_capital == D('5000')
    assert result.account_generation == 3
    assert (result.portfolio_generation, result.policy_generation, result.filter_generation) == (11, 5, 7)
    assert Ceiling.REQUESTED_RISK in result.bindings


def test_deterministic_replay_and_decimal_spelling_are_identical():
    ev = evaluation()
    assert evaluate(ev) == evaluate(ev)
    equivalent = dataclasses.replace(
        ev, request=dataclasses.replace(ev.request, entry_price=D('100.00'), stop_price=D('95.000')))
    assert evaluate(equivalent) == evaluate(ev)
    with localcontext() as context:
        context.prec = 8
        assert evaluate(ev) == evaluate(equivalent)


def test_authorization_is_closed_current_and_one_shot():
    ev = evaluation()
    result = approved(ev)
    assert authorization_is_current(result, ev, ())
    assert not authorization_is_current(result, ev, (result.authorization_id,))
    with pytest.raises(TypeError):
        authorization_is_current(result, ev)
    with pytest.raises(TypeError):
        ApprovedDecision()
    with pytest.raises(TypeError):
        dataclasses.replace(result, quantity=D('999'))
    copied = copy.copy(result)
    assert copied == result
    assert copied.authorization_id == result.authorization_id
    assert authorization_is_current(copied, ev, ())
    assert not authorization_is_current(copied, ev, (result.authorization_id,))
    forged = object.__new__(ApprovedDecision)
    for field in dataclasses.fields(result):
        object.__setattr__(forged, field.name, getattr(result, field.name))
    object.__setattr__(forged, 'quantity', D('999'))
    assert not authorization_is_current(forged, ev, ())


@pytest.mark.parametrize('field', [
    'quantity', 'notional', 'risk', 'margin', 'leverage', 'controlled_capital',
])
def test_authorization_rejects_equal_non_decimal_executable_fields(field):
    ev = evaluation()
    result = approved(ev)
    forged = object.__new__(ApprovedDecision)
    for item in dataclasses.fields(result):
        object.__setattr__(forged, item.name, getattr(result, item.name))
    value = getattr(result, field)
    assert value == int(value)
    object.__setattr__(forged, field, int(value))
    assert not authorization_is_current(forged, ev, ())


def test_changed_generation_invalidates_old_authorization():
    ev = evaluation()
    old = approved(ev)
    changed = dataclasses.replace(
        ev, portfolio=dataclasses.replace(ev.portfolio, generation=12),
        request=dataclasses.replace(ev.request, portfolio_generation=12))
    new = approved(changed)
    assert new.authorization_id != old.authorization_id
    assert not authorization_is_current(old, changed, ())
    refused(dataclasses.replace(ev, portfolio=dataclasses.replace(ev.portfolio, generation=12)),
            RefusalCode.GENERATION_MISMATCH)
    refused(dataclasses.replace(ev, account=dataclasses.replace(ev.account, generation=4)),
            RefusalCode.GENERATION_MISMATCH)


@pytest.mark.parametrize('value', [
    1, 1.0, D('NaN'), D('sNaN'), D('Infinity'), D('0'), D('-1'),
])
def test_non_decimal_non_finite_and_non_positive_requested_risk_refuse(value):
    ev = evaluation(r=request(requested_risk=value))
    refused(ev, RefusalCode.INPUT_INVALID)


@pytest.mark.parametrize(('replacement', 'reason'), [
    (lambda ev: dataclasses.replace(ev, account=dataclasses.replace(ev.account, confirmed=False)),
     RefusalCode.ACCOUNT_UNCONFIRMED),
    (lambda ev: dataclasses.replace(ev, account=dataclasses.replace(ev.account, fresh=False)),
     RefusalCode.STALE_ACCOUNT),
    (lambda ev: dataclasses.replace(ev, portfolio=dataclasses.replace(ev.portfolio, fresh=False)),
     RefusalCode.STALE_PORTFOLIO),
    (lambda ev: dataclasses.replace(ev, policy=dataclasses.replace(ev.policy, fresh=False)),
     RefusalCode.STALE_POLICY),
    (lambda ev: dataclasses.replace(ev, filters=dataclasses.replace(ev.filters, fresh=False)),
     RefusalCode.STALE_FILTER),
    (lambda ev: dataclasses.replace(ev, filters=dataclasses.replace(ev.filters, verified=False)),
     RefusalCode.FILTER_UNVERIFIED),
    (lambda ev: dataclasses.replace(ev, request=dataclasses.replace(ev.request,
                                                                     environment=Environment.MAINNET)),
     RefusalCode.ENVIRONMENT_MISMATCH),
    (lambda ev: dataclasses.replace(ev, portfolio=dataclasses.replace(ev.portfolio,
                                                                       account_id='acct_' + 'z' * 32)),
     RefusalCode.ACCOUNT_MISMATCH),
])
def test_confirmation_freshness_and_identity_refusals_are_stable(replacement, reason):
    refused(replacement(evaluation()), reason)


@pytest.mark.parametrize(('change', 'reason'), [
    ({'entry_price': D('100.05')}, RefusalCode.ENTRY_TICK_INVALID),
    ({'stop_price': D('95.05')}, RefusalCode.STOP_TICK_INVALID),
    ({'stop_price': D('101')}, RefusalCode.STOP_DIRECTION_INVALID),
])
def test_tick_and_stop_direction_are_never_guessed(change, reason):
    refused(evaluation(r=request(**change)), reason)


def test_short_requires_hedge_mode_and_has_correct_stop_direction():
    short = request(side=PositionSide.SHORT, stop_price=D('105'))
    refused(evaluation(a=account(hedge_mode=False), r=short), RefusalCode.HEDGE_MODE_REQUIRED)
    result = approved(evaluation(r=short))
    assert result.quantity == D('20')


def test_controlled_capital_is_min_equity_cap_minus_reserve():
    result = approved(evaluation(a=account(equity=D('3000'), reserved_capital=D('500'))))
    assert result.controlled_capital == D('2500')
    assert result.quantity == D('12.5')
    refused(evaluation(a=account(equity=D('1000'), reserved_capital=D('1000'))),
            RefusalCode.CAPITAL_UNAVAILABLE)


@pytest.mark.parametrize(('requested_risk', 'expected'), [
    ('5', '1.0'), ('5.49', '1.0'), ('5.5', '1.1'), ('5.99', '1.1'),
])
def test_quantity_rounds_down_once_and_never_up(requested_risk, expected):
    result = approved(evaluation(r=request(requested_risk=D(requested_risk))))
    assert result.quantity == D(expected)
    assert result.risk <= D(requested_risk)


def test_rounded_plan_refuses_below_minimums_and_filter_contradictions():
    refused(evaluation(r=request(requested_risk=D('4.5')),
                       f=filters(min_quantity=D('1'))), RefusalCode.BELOW_MIN_QUANTITY)
    refused(evaluation(r=request(requested_risk=D('.5')),
                       f=filters(min_notional=D('20'))), RefusalCode.BELOW_MIN_NOTIONAL)
    refused(evaluation(f=filters(min_quantity=D('2'), max_quantity=D('1'))),
            RefusalCode.FILTER_CONTRADICTION)
    refused(evaluation(f=filters(min_notional=D('200'), max_notional=D('100'))),
            RefusalCode.FILTER_CONTRADICTION)


@pytest.mark.parametrize(('ev', 'expected', 'binding'), [
    (evaluation(r=request(requested_risk=D('50'))), D('10'), Ceiling.REQUESTED_RISK),
    (evaluation(pol=policy(max_trade_risk_fraction=D('.01'))), D('10'), Ceiling.TRADE_RISK),
    (evaluation(p=portfolio(exposures=(exposure(risk='950'),))), D('10'), Ceiling.PORTFOLIO_RISK),
    (evaluation(p=portfolio(exposures=(exposure(symbol='BTCUSDT', risk='450'),)), pol=SHARED_SIDE),
     D('10'), Ceiling.SYMBOL_SIDE_RISK),
    (evaluation(p=portfolio(exposures=(exposure(strategy='strategy-a', book='book-a', risk='450'),))),
     D('10'), Ceiling.ALLOCATION_RISK),
    (evaluation(p=portfolio(exposures=(exposure(strategy='strategy-a', book='book-a', notional='2000'),))),
     D('5'), Ceiling.ALLOCATION_NOTIONAL),
    (evaluation(p=portfolio(exposures=(exposure(symbol='ETHUSDT', side=PositionSide.LONG,
                                                notional='4750'),
                                       exposure(symbol='SOLUSDT', side=PositionSide.SHORT,
                                                notional='4750')))),
     D('5'), Ceiling.ACCOUNT_GROSS),
    (evaluation(p=portfolio(exposures=(exposure(notional='4500'),))), D('5'), Ceiling.ACCOUNT_NET),
    (evaluation(p=portfolio(exposures=(exposure(symbol='BTCUSDT', side=PositionSide.SHORT,
                                                notional='2000'),))), D('5'), Ceiling.SYMBOL),
    (evaluation(p=portfolio(exposures=(exposure(symbol='BTCUSDT', notional='2000'),)), pol=SHARED_SIDE),
     D('5'), Ceiling.SYMBOL_SIDE),
    (evaluation(
        p=portfolio(exposures=(exposure(symbol='ETHUSDT', notional='3250'),)),
        pol=policy(correlation_map=(CorrelationAssignment(symbol='BTCUSDT', bucket='crypto-major'),
                                    CorrelationAssignment(symbol='ETHUSDT', bucket='crypto-major'),
                                    CorrelationAssignment(symbol='SOLUSDT', bucket='other-sol')))),
     D('5'), Ceiling.CORRELATION),
    (evaluation(a=account(available_margin=D('100'))), D('5'), Ceiling.AVAILABLE_MARGIN),
    (evaluation(f=filters(max_quantity=D('5'))), D('5'), Ceiling.VENUE_MAX_QUANTITY),
    (evaluation(f=filters(max_notional=D('500'))), D('5'), Ceiling.VENUE_MAX_NOTIONAL),
])
def test_all_frozen_ceilings_bind_without_raising_quantity(ev, expected, binding):
    result = approved(ev)
    assert result.quantity == expected
    assert binding in result.bindings


def test_tighter_caps_are_monotone_and_never_increase_quantity():
    loose = approved()
    for fraction in (D('.49'), D('.4'), D('.3'), D('.2')):
        tighter = approved(evaluation(pol=policy(max_symbol_side_fraction=fraction)))
        assert tighter.quantity <= loose.quantity
        loose = tighter


def test_requested_leverage_is_reduced_never_increased():
    result = approved(evaluation(r=request(requested_leverage=D('20')),
                                 pol=policy(max_leverage=D('8')),
                                 f=filters(max_leverage=D('6'))))
    assert result.leverage == D('6')
    assert Ceiling.LEVERAGE in result.bindings
    with localcontext() as context:
        context.prec = 64
        assert result.margin == result.notional / D('6')


def test_adds_and_inflight_reservations_use_complete_post_action_risk():
    base = approved(evaluation(r=request(action=RiskAction.ADD)))
    reserved = reservation(qty='10', notional='1000', risk='50')
    with_reserved = approved(evaluation(
        p=portfolio(reservations=(reserved,)), r=request(action=RiskAction.ADD)))
    assert with_reserved.quantity < base.quantity
    assert with_reserved.risk + reserved.risk <= D('500')
    two = approved(evaluation(
        p=portfolio(reservations=(reserved, dataclasses.replace(
            reserved, request_id='req_' + 'e' * 32, authorization_id='dec_' + 'f' * 32))),
        r=request(action=RiskAction.ADD)))
    assert two.quantity <= with_reserved.quantity


def test_inflight_margin_is_reserved_before_new_authorization():
    held = dataclasses.replace(reservation(), margin=D('4900'))
    result = approved(evaluation(p=portfolio(reservations=(held,)), pol=SHARED_SIDE))
    assert result.quantity == D('5')
    assert Ceiling.AVAILABLE_MARGIN in result.bindings


@pytest.mark.parametrize('field', ['risk', 'margin'])
def test_inflight_reservation_risk_and_margin_must_be_strictly_positive(field):
    held = dataclasses.replace(reservation(), **{field: D('0')})
    refused(evaluation(p=portfolio(reservations=(held,))), RefusalCode.INPUT_INVALID)


@pytest.mark.parametrize('ev', [
    evaluation(r=request(entry_price=D('1E+999999'), stop_price=D('1'))),
    evaluation(f=filters(tick_size=D('1E-999999'))),
    evaluation(f=filters(step_size=D('1E-999999'))),
])
def test_decimal_extremes_and_trapped_arithmetic_refuse_without_leaking(ev):
    result = evaluate(ev)
    assert type(result) is RefusedDecision
    assert result.reasons == (RefusalCode.INPUT_INVALID,)


def test_duplicate_request_cannot_bypass_with_another_entry_source():
    existing = reservation(request_id=request().request_id)
    refused(evaluation(p=portfolio(reservations=(existing,))), RefusalCode.DUPLICATE_REQUEST)
    first = reservation()
    reused = dataclasses.replace(first, request_id='req_' + 'e' * 32)
    refused(evaluation(p=portfolio(reservations=(first, reused))), RefusalCode.INPUT_INVALID)


def test_policy_requires_explicit_allocation_and_correlation():
    refused(evaluation(pol=policy(allocations=())), RefusalCode.ALLOCATION_MISSING)
    refused(evaluation(pol=policy(correlation_map=())), RefusalCode.CORRELATION_MAPPING_MISSING)
    refused(evaluation(pol=policy(correlation_caps=())), RefusalCode.CORRELATION_CAP_MISSING)
    unknown = exposure(symbol='XRPUSDT')
    refused(evaluation(p=portfolio(exposures=(unknown,))),
            RefusalCode.CORRELATION_MAPPING_MISSING)


def test_follower_matrix_is_exact_only_and_cannot_change_master_plan():
    master = approved(evaluation())
    with_profiles = approved(evaluation(followers=(follower('small', 1000), follower('large', 4000))))
    assert (with_profiles.quantity, with_profiles.risk, with_profiles.leverage,
            with_profiles.authorization_id) == (
                master.quantity, master.risk, master.leverage, master.authorization_id)
    matrix = {row.profile_id: row for row in with_profiles.followers}
    assert not matrix['small'].exact_feasible
    assert matrix['small'].exact_quantity == master.quantity
    assert matrix['large'].exact_feasible
    assert matrix['large'].reasons == ()


def test_invalid_or_stale_follower_is_diagnostic_and_never_vetoes_master():
    master = approved()
    bad = follower('bad', 500)
    stale = follower('stale', 4000, fresh=False)
    result = approved(evaluation(followers=(bad, stale)))
    assert result.quantity == master.quantity
    assert result.authorization_id == master.authorization_id
    assert result.followers[0].reasons == (RefusalCode.INPUT_INVALID,)
    assert result.followers[1].reasons == (RefusalCode.STALE_ACCOUNT,)


def test_reduce_close_and_protection_are_never_subject_to_gateway():
    assert requires_authorization(RiskAction.ENTRY)
    assert requires_authorization(RiskAction.ADD)
    assert not requires_authorization(RiskAction.REDUCE)
    assert not requires_authorization(RiskAction.CLOSE)
    assert not requires_authorization(RiskAction.PROTECT)
    assert requires_authorization('unknown-action')
    refused(evaluation(r=request(action=RiskAction.REDUCE)), RefusalCode.INPUT_INVALID)


def test_outputs_are_immutable_and_do_not_echo_malformed_values():
    result = approved()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.quantity = D('0')
    bad = evaluate(evaluation(r=request(requested_risk='SECRET')))
    assert type(bad) is RefusedDecision
    assert 'SECRET' not in repr(bad)


def test_import_boundary_is_stdlib_only():
    path = Path(__file__).parents[2] / 'newcore' / 'risk.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or '')
    assert not [name for name in imports if name.startswith('newcore') or name.startswith('legacy')]
