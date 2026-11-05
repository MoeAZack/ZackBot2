"""RISK-LOSS-01: daily loss breaker, same-direction correlated R cap and one
position per symbol and side, inside the NC-06 risk gateway.

Fixture numbers follow the 4 Nov 2026 PAPER loss (equity about 10k, about 1R
per stop-out, two correlated stop clusters).  Policy here: 1R = 0.5% of
day-start equity = 50, daily breaker at min(3%, 6R) = 300, bucket-side cap 2R
= 100.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from newcore.risk import (
    ClusterEntry,
    AccountRiskSnapshot,
    Allocation,
    ApprovedDecision,
    Ceiling,
    CorrelationAssignment,
    CorrelationCap,
    LossState,
    Environment,
    Exposure,
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
    daily_loss_breached,
    drawdown_kill_breached,
    evaluate,
    requires_authorization,
    strategy_paused,
)

D = Decimal
ACCOUNT = 'acct_' + 'a' * 32
LONG, SHORT = PositionSide.LONG, PositionSide.SHORT
DAY_MS = 24 * 3600 * 1000
ALTS = ('BTCUSDT', 'DOGEUSDT', 'BNBUSDT', 'BCHUSDT', 'NEARUSDT', 'SOLUSDT')


def limits(**changes):
    row = LossLimits(daily_loss_fraction=D('.03'), daily_loss_r=D('6'),
                     risk_unit_fraction=D('.005'), max_bucket_side_risk_r=D('2'),
                     max_drawdown_fraction=D('.1'), max_net_side_risk_r=D('4'),
                     max_entries_per_strategy_per_candle=2, max_entries_per_side_per_candle=3,
                     cooldown_stop_outs=3, cooldown_window_ms=DAY_MS, cooldown_pause_ms=DAY_MS,
                     one_position_per_symbol_side=True)
    return dataclasses.replace(row, **changes)


def day(**changes):
    row = LossState(trading_day='2026-11-04', day_start_equity=D('10000'),
                         realized_pnl=D('0'), unrealized_pnl=D('0'), halt_latched=False,
                         peak_equity=D('10000'), drawdown_kill_latched=False,
                         paused_strategies=(), cluster_entries=())
    return dataclasses.replace(row, **changes)


def policy(**changes):
    strategies = ('vwaprev', 'trend', 'range', 'breakout')
    row = RiskPolicySnapshot(
        policy_id='policy-loss01', account_id=ACCOUNT, environment=Environment.TESTNET,
        generation=5, fresh=True, capital_cap=D('100000'),
        max_trade_risk_fraction=D('.05'), max_portfolio_risk_fraction=D('.5'),
        max_symbol_side_risk_fraction=D('.5'), max_gross_multiple=D('10'),
        max_net_multiple=D('10'), max_symbol_fraction=D('1'),
        max_symbol_side_fraction=D('1'), max_leverage=D('10'),
        allocations=tuple(Allocation(strategy_id=s, book_id='book', notional_fraction=D('1'),
                                     risk_fraction=D('1')) for s in strategies),
        correlation_map=tuple(CorrelationAssignment(symbol=s, bucket='crypto-beta') for s in ALTS)
        + (CorrelationAssignment(symbol='PAXGUSDT', bucket='gold'),),
        correlation_caps=(CorrelationCap(bucket='crypto-beta', exposure_fraction=D('1')),
                          CorrelationCap(bucket='gold', exposure_fraction=D('1'))),
        loss_limits=limits())
    return dataclasses.replace(row, **changes)


def exposure(symbol, side=LONG, strategy='trend', risk='50', notional='1000'):
    return Exposure(symbol=symbol, side=side, strategy_id=strategy, book_id='book',
                    quantity=D('1'), notional=D(notional), risk=D(risk))


def reservation(symbol, side=LONG, strategy='trend', risk='50', tag='c'):
    return OpeningReservation(
        request_id='req_' + tag * 32, authorization_id='dec_' + tag * 32, symbol=symbol,
        side=side, strategy_id=strategy, book_id='book', quantity=D('1'),
        notional=D('1000'), risk=D(risk), margin=D('100'))


def portfolio(*, exposures=(), reservations=(), **daily_changes):
    return PortfolioRiskSnapshot(account_id=ACCOUNT, generation=11, fresh=True,
                                 exposures=tuple(exposures), reservations=tuple(reservations),
                                 loss=day(**daily_changes))


def request(symbol='BTCUSDT', side=LONG, strategy='vwaprev', action=RiskAction.ENTRY,
            risk='50', **changes):
    entry, stop = (D('100'), D('95')) if side is LONG else (D('100'), D('105'))
    row = RiskRequest(
        request_id='req_' + 'b' * 32, account_id=ACCOUNT, environment=Environment.TESTNET,
        account_generation=3, portfolio_generation=11, policy_generation=5, filter_generation=7,
        action=action, strategy_id=strategy, book_id='book', symbol=symbol, side=side,
        entry_price=entry, stop_price=stop, requested_risk=D(risk), requested_leverage=D('5'))
    return dataclasses.replace(row, **changes)


def evaluation(*, p=None, pol=None, r=None):
    r = r or request()
    account = AccountRiskSnapshot(
        account_id=ACCOUNT, environment=Environment.TESTNET, generation=3, confirmed=True,
        fresh=True, hedge_mode=True, equity=D('10000'), available_margin=D('10000'),
        reserved_capital=D('0'))
    filters = VenueRiskFilters(
        filter_id='filters-v1', symbol=r.symbol, generation=7, fresh=True, verified=True,
        step_size=D('.01'), tick_size=D('.1'), min_quantity=D('.01'), max_quantity=D('100000'),
        min_notional=D('5'), max_notional=D('10000000'), max_leverage=D('10'))
    return RiskEvaluation(account=account, portfolio=p or portfolio(), policy=pol or policy(),
                          filters=filters, request=r, followers=())


def approved(ev):
    result = evaluate(ev)
    assert type(result) is ApprovedDecision, getattr(result, 'reasons', None)
    return result


def refused(ev, reason):
    result = evaluate(ev)
    assert type(result) is RefusedDecision
    assert reason in result.reasons, result.reasons
    return result


# ---------------------------------------------------------------- daily breaker

def test_fresh_day_approves_one_full_r():
    result = approved(evaluation())
    assert result.quantity == D('10') and result.risk == D('50')


@pytest.mark.parametrize(('realized', 'unrealized'), [
    ('-300', '0'), ('-400', '0'), ('0', '-300'), ('-200', '-100'), ('-301', '1'),
])
def test_breaker_halts_at_and_beyond_limit_counting_mark_to_market(realized, unrealized):
    refused(evaluation(p=portfolio(realized_pnl=D(realized), unrealized_pnl=D(unrealized))),
            RefusalCode.DAILY_LOSS_HALT)


def test_breaker_uses_the_tighter_of_percent_and_r_limits():
    # 6R = 300 = 3%: equal.  R-bound tighter: 4R = 200 < 300.
    tight_r = policy(loss_limits=limits(daily_loss_r=D('4')))
    refused(evaluation(p=portfolio(realized_pnl=D('-200')), pol=tight_r), RefusalCode.DAILY_LOSS_HALT)
    approved(evaluation(p=portfolio(realized_pnl=D('-199')), pol=tight_r))
    # Percent-bound tighter: 2% = 200 < 6R = 300.
    tight_pct = policy(loss_limits=limits(daily_loss_fraction=D('.02')))
    refused(evaluation(p=portfolio(realized_pnl=D('-200')), pol=tight_pct), RefusalCode.DAILY_LOSS_HALT)
    approved(evaluation(p=portfolio(realized_pnl=D('-199')), pol=tight_pct))


def test_remaining_daily_budget_clips_new_risk_so_a_stop_out_cannot_cross_the_limit():
    result = approved(evaluation(p=portfolio(realized_pnl=D('-270'))))
    assert result.risk == D('30') and result.quantity == D('6')
    assert Ceiling.DAILY_LOSS_BUDGET in result.bindings
    assert D('270') + result.risk <= D('300')


def test_daily_budget_counts_reserved_and_open_risk_to_stop():
    # Review finding A: 250 lost + 50 in flight + 50 new would reach 350 > 300.
    gold = reservation('PAXGUSDT', risk='50')
    result = refused(evaluation(p=portfolio(reservations=(gold,), realized_pnl=D('-250'))),
                     RefusalCode.NO_CAPACITY)
    assert Ceiling.DAILY_LOSS_BUDGET in result.bindings
    held = (exposure('PAXGUSDT', risk='20'),)
    result = approved(evaluation(p=portfolio(exposures=held, realized_pnl=D('-250'))))
    assert result.risk == D('30')
    assert Ceiling.DAILY_LOSS_BUDGET in result.bindings


def test_profits_never_widen_the_daily_budget():
    wide_bucket = policy(loss_limits=limits(max_bucket_side_risk_r=D('10'), max_net_side_risk_r=D('10')))
    flat = approved(evaluation(pol=wide_bucket, r=request(risk='1000')))
    up = approved(evaluation(p=portfolio(realized_pnl=D('5000')), pol=wide_bucket,
                             r=request(risk='1000')))
    assert up.quantity == flat.quantity == D('60')
    assert Ceiling.DAILY_LOSS_BUDGET in up.bindings


def test_latched_halt_survives_recovery():
    refused(evaluation(p=portfolio(realized_pnl=D('500'), halt_latched=True)),
            RefusalCode.DAILY_LOSS_HALT)


def test_breaker_helper_matches_gateway_and_fails_closed():
    assert not daily_loss_breached(limits(), day(realized_pnl=D('-299.99')))
    assert daily_loss_breached(limits(), day(realized_pnl=D('-300')))
    assert daily_loss_breached(limits(), day(halt_latched=True))
    assert daily_loss_breached(None, day())
    assert daily_loss_breached(limits(), None)
    assert daily_loss_breached(limits(), day(realized_pnl=D('NaN')))
    assert daily_loss_breached(limits(daily_loss_fraction=D('0')), day())
    assert daily_loss_breached(limits(), day(day_start_equity=D('0')))
    assert daily_loss_breached(limits(), day(trading_day='2026-02-30'))


def test_breaker_does_not_gate_risk_reducing_actions():
    for action in (RiskAction.REDUCE, RiskAction.CLOSE, RiskAction.PROTECT):
        assert not requires_authorization(action)


def test_new_loss_invalidates_an_authorization_issued_before_it():
    before = evaluation()
    decision = approved(before)
    after = dataclasses.replace(before, portfolio=portfolio(realized_pnl=D('-300')))
    assert not authorization_is_current(decision, after, ())
    assert authorization_is_current(decision, before, ())


@pytest.mark.parametrize('bad_day', [
    {'trading_day': '2026-11-4'}, {'trading_day': '2026-02-30'}, {'trading_day': '20261104xx'},
    {'trading_day': '２０２６-11-04'}, {'trading_day': 20261104},
    {'day_start_equity': D('0')}, {'day_start_equity': D('-1')}, {'day_start_equity': 10000},
    {'day_start_equity': D('Infinity')}, {'realized_pnl': D('NaN')}, {'unrealized_pnl': -1.0},
    {'halt_latched': 0}, {'halt_latched': None},
])
def test_malformed_daily_state_refuses_as_input_invalid(bad_day):
    p = dataclasses.replace(portfolio(), loss=dataclasses.replace(day(), **bad_day))
    refused(evaluation(p=p), RefusalCode.INPUT_INVALID)


@pytest.mark.parametrize('bad_limit', [
    {'daily_loss_fraction': D('0')}, {'daily_loss_fraction': D('1.01')},
    {'daily_loss_r': D('0')}, {'daily_loss_r': 6}, {'risk_unit_fraction': D('0')},
    {'risk_unit_fraction': D('NaN')}, {'max_bucket_side_risk_r': D('-2')},
    {'one_position_per_symbol_side': 1},
])
def test_malformed_loss_limits_refuse_as_input_invalid(bad_limit):
    refused(evaluation(pol=policy(loss_limits=limits(**bad_limit))), RefusalCode.INPUT_INVALID)


@pytest.mark.parametrize('missing', [None, 'off', ()])
def test_missing_loss_state_or_limits_fail_closed(missing):
    refused(evaluation(p=dataclasses.replace(portfolio(), loss=missing)), RefusalCode.INPUT_INVALID)
    refused(evaluation(pol=policy(loss_limits=missing)), RefusalCode.INPUT_INVALID)


def test_loss_types_cannot_be_constructed_without_every_field():
    with pytest.raises(TypeError):
        PortfolioRiskSnapshot(account_id=ACCOUNT, generation=1, fresh=True,
                              exposures=(), reservations=())
    with pytest.raises(TypeError):
        LossLimits(daily_loss_fraction=D('.03'), daily_loss_r=D('6'),
                   risk_unit_fraction=D('.005'), max_bucket_side_risk_r=D('2'))


# --------------------------------------------- same-direction correlated R cap

def test_overnight_cluster_third_same_direction_long_is_refused():
    """4 Nov overnight: DOGE/BTC/BNB/BCH longs, one strategy, about 1R each."""
    held = (exposure('DOGEUSDT', strategy='vwaprev'), exposure('BNBUSDT', strategy='vwaprev'))
    result = refused(evaluation(p=portfolio(exposures=held), r=request('BCHUSDT')),
                     RefusalCode.NO_CAPACITY)
    assert Ceiling.CORRELATION_SIDE_RISK in result.bindings


def test_bucket_side_cap_clips_partial_room_and_counts_reservations():
    result = approved(evaluation(p=portfolio(exposures=(exposure('DOGEUSDT', risk='40'),),
                                             reservations=(reservation('BNBUSDT', risk='30'),)),
                                 r=request('BCHUSDT')))
    assert result.risk == D('30')
    assert Ceiling.CORRELATION_SIDE_RISK in result.bindings


def test_bucket_side_cap_ignores_opposite_direction_and_other_buckets():
    held = (exposure('DOGEUSDT', side=SHORT), exposure('BNBUSDT', side=SHORT),
            exposure('PAXGUSDT', risk='100'))
    result = approved(evaluation(p=portfolio(exposures=held), r=request('BCHUSDT')))
    assert result.risk == D('50')


def test_afternoon_short_squeeze_cluster_is_capped_across_strategies():
    """4 Nov afternoon: alt shorts from four strategies were one correlated bet."""
    held = (exposure('SOLUSDT', side=SHORT, strategy='trend'),
            exposure('DOGEUSDT', side=SHORT, strategy='range'))
    refused(evaluation(p=portfolio(exposures=held),
                       r=request('NEARUSDT', side=SHORT, strategy='breakout')),
            RefusalCode.NO_CAPACITY)


def test_zero_risk_positions_do_not_consume_bucket_room():
    held = (exposure('DOGEUSDT', risk='0'), exposure('BNBUSDT', risk='0'))
    assert approved(evaluation(p=portfolio(exposures=held), r=request('BCHUSDT'))).risk == D('50')


# ------------------------------------------- one position per symbol and side

def test_second_strategy_on_same_symbol_and_side_is_refused():
    """4 Nov: NEAR was shorted by two strategies at once."""
    held = (exposure('NEARUSDT', side=SHORT, strategy='trend'),)
    refused(evaluation(p=portfolio(exposures=held),
                       r=request('NEARUSDT', side=SHORT, strategy='range')),
            RefusalCode.SYMBOL_SIDE_OCCUPIED)
    refused(evaluation(p=portfolio(reservations=(reservation('NEARUSDT', side=SHORT),)),
                       r=request('NEARUSDT', side=SHORT, strategy='range')),
            RefusalCode.SYMBOL_SIDE_OCCUPIED)


def test_fresh_entry_by_the_same_strategy_cannot_double_a_held_position():
    held = (exposure('BTCUSDT', strategy='vwaprev'),)
    refused(evaluation(p=portfolio(exposures=held), r=request('BTCUSDT')),
            RefusalCode.SYMBOL_SIDE_OCCUPIED)


def test_add_by_the_owning_strategy_is_allowed_and_by_another_is_refused():
    held = (exposure('BTCUSDT', strategy='vwaprev', risk='10'),)
    approved(evaluation(p=portfolio(exposures=held), r=request('BTCUSDT', action=RiskAction.ADD)))
    refused(evaluation(p=portfolio(exposures=held),
                       r=request('BTCUSDT', strategy='trend', action=RiskAction.ADD)),
            RefusalCode.SYMBOL_SIDE_OCCUPIED)
    mixed = held + (exposure('BTCUSDT', strategy='trend', risk='10'),)
    refused(evaluation(p=portfolio(exposures=mixed), r=request('BTCUSDT', action=RiskAction.ADD)),
            RefusalCode.SYMBOL_SIDE_OCCUPIED)


def test_same_strategy_in_another_book_is_a_different_holder():
    held = (exposure('BTCUSDT', strategy='vwaprev', risk='10'),)
    other_book = dataclasses.replace(request('BTCUSDT', action=RiskAction.ADD), book_id='book2')
    pol = policy(allocations=policy().allocations + (
        Allocation(strategy_id='vwaprev', book_id='book2', notional_fraction=D('1'),
                   risk_fraction=D('1')),))
    refused(evaluation(p=portfolio(exposures=held), pol=pol, r=other_book),
            RefusalCode.SYMBOL_SIDE_OCCUPIED)


def test_opposite_side_on_same_symbol_is_a_different_slot():
    held = (exposure('BTCUSDT', side=SHORT, strategy='trend'),)
    approved(evaluation(p=portfolio(exposures=held), r=request('BTCUSDT')))


def test_owner_can_switch_off_one_position_rule_explicitly():
    held = (exposure('NEARUSDT', side=SHORT, strategy='trend', risk='10'),)
    shared = policy(loss_limits=limits(one_position_per_symbol_side=False))
    approved(evaluation(p=portfolio(exposures=held), pol=shared,
                        r=request('NEARUSDT', side=SHORT, strategy='range')))


# ----------------------------------------------------------- whole-day replay

def test_replay_of_4_nov_clusters_bounds_the_day_well_inside_the_breaker():
    """Feed the 4 Nov entry sequence through the gateway, stopping each
    approved trade out at -1R before the next cluster.  Unbounded, nine
    entries lost about 9R; gated, at most 2R per cluster, about 4R in total."""
    overnight = [('DOGEUSDT', LONG, 'vwaprev'), ('BTCUSDT', LONG, 'vwaprev'),
                 ('BNBUSDT', LONG, 'vwaprev'), ('BCHUSDT', LONG, 'vwaprev')]
    afternoon = [('NEARUSDT', SHORT, 'trend'), ('NEARUSDT', SHORT, 'range'),
                 ('SOLUSDT', SHORT, 'breakout'), ('DOGEUSDT', SHORT, 'range'),
                 ('BNBUSDT', SHORT, 'vwaprev')]
    realized = D('0')
    approvals = 0
    for cluster in (overnight, afternoon):
        held: list[Exposure] = []
        for symbol, side, strategy in cluster:
            decision = evaluate(evaluation(p=portfolio(exposures=held, realized_pnl=realized),
                                           r=request(symbol, side=side, strategy=strategy)))
            if type(decision) is ApprovedDecision:
                approvals += 1
                held.append(exposure(symbol, side=side, strategy=strategy, risk=str(decision.risk)))
        realized -= sum((x.risk for x in held), D('0'))  # the whole cluster stops out
    assert approvals == 4
    assert realized == D('-200')
    assert not daily_loss_breached(limits(), day(realized_pnl=realized))


def test_replay_breaker_trips_when_losses_continue():
    realized = D('0')
    for index in range(10):
        decision = evaluate(evaluation(p=portfolio(realized_pnl=realized),
                                       r=request(ALTS[index % len(ALTS)])))
        if type(decision) is not ApprovedDecision:
            assert RefusalCode.DAILY_LOSS_HALT in decision.reasons
            break
        realized -= decision.risk
    else:
        raise AssertionError('breaker never tripped')
    assert realized == D('-300') and index == 6


# ------------------------------------------------------------ monotonicity

def test_tighter_loss_limits_never_increase_quantity():
    p = portfolio(exposures=(exposure('DOGEUSDT', risk='20'),), realized_pnl=D('-150'))
    previous = None
    for fraction, r_limit, bucket_r in [('.05', '10', '4'), ('.03', '6', '2'), ('.025', '5', '1.5'),
                                        ('.02', '4', '1.2')]:
        pol = policy(loss_limits=limits(daily_loss_fraction=D(fraction), daily_loss_r=D(r_limit),
                                        max_bucket_side_risk_r=D(bucket_r)))
        result = evaluate(evaluation(p=p, pol=pol, r=request('BCHUSDT', risk='1000')))
        quantity = result.quantity if type(result) is ApprovedDecision else D('0')
        assert previous is None or quantity <= previous
        previous = quantity


def test_decision_is_deterministic_and_spelling_invariant():
    ev = evaluation(p=portfolio(realized_pnl=D('-120.50')))
    same = evaluation(p=portfolio(realized_pnl=D('-120.5000')))
    assert evaluate(ev) == evaluate(ev) == evaluate(same)


# ------------------------------------------------------- peak-drawdown kill

def with_equity(ev, equity):
    return dataclasses.replace(ev, account=dataclasses.replace(ev.account, equity=D(equity)))


def test_drawdown_kill_trips_at_ten_percent_below_peak():
    p = portfolio(peak_equity=D('11112'))
    approved(with_equity(evaluation(p=p), '10001'))           # 9.999% below peak
    refused(with_equity(evaluation(p=portfolio(peak_equity=D('11120'))), '10008'),
            RefusalCode.DRAWDOWN_KILL)                         # exactly 10%
    refused(with_equity(evaluation(p=portfolio(peak_equity=D('20000'))), '10000'),
            RefusalCode.DRAWDOWN_KILL)


def test_drawdown_kill_latch_needs_owner_resume_even_after_recovery():
    refused(evaluation(p=portfolio(drawdown_kill_latched=True, peak_equity=D('10000'))),
            RefusalCode.DRAWDOWN_KILL)


def test_new_high_above_stale_peak_is_not_a_drawdown():
    approved(with_equity(evaluation(p=portfolio(peak_equity=D('5000'))), '10000'))


def test_drawdown_helper_fails_closed():
    assert not drawdown_kill_breached(limits(), day(), D('10000'))
    assert drawdown_kill_breached(limits(), day(peak_equity=D('11112')), D('10000'))
    assert drawdown_kill_breached(limits(), day(), D('0'))
    assert drawdown_kill_breached(limits(), day(), 10000)
    assert drawdown_kill_breached(None, day(), D('10000'))
    assert drawdown_kill_breached(limits(), None, D('10000'))


# ------------------------------------------------------ loss-streak cooldown

def test_strategy_cooldown_refuses_paused_strategy_only():
    p = portfolio(paused_strategies=('vwaprev',))
    refused(evaluation(p=p), RefusalCode.STRATEGY_COOLDOWN)
    refused(evaluation(p=p, r=request(action=RiskAction.ADD)), RefusalCode.STRATEGY_COOLDOWN)
    approved(evaluation(p=p, r=request(strategy='trend')))


def test_strategy_paused_helper_three_stops_in_a_day_pause_for_a_day():
    now = 10 * DAY_MS
    hour = 3600 * 1000
    three = (now - 5 * hour, now - 3 * hour, now - 1 * hour)
    assert strategy_paused(limits(), three, now)
    assert not strategy_paused(limits(), three[1:], now)                    # only two
    spread = (now - 30 * hour, now - 3 * hour, now - 1 * hour)              # 29h apart
    assert not strategy_paused(limits(), spread, now)
    exactly_window = (now - 26 * hour, now - 14 * hour, now - 2 * hour)     # 24h apart
    assert strategy_paused(limits(), exactly_window, now)
    edge = (now - 26 * hour, now - 25 * hour, now - DAY_MS)                 # pause just ended
    assert not strategy_paused(limits(), edge, now)
    assert strategy_paused(limits(), edge, now - 1)


def test_strategy_paused_helper_fails_closed():
    now = 10 * DAY_MS
    assert strategy_paused(limits(), (now + 1,), now)                       # future stop
    assert strategy_paused(limits(), [now - 1], now)
    assert strategy_paused(limits(), (D('1'),), now)
    assert strategy_paused(limits(), (), 0)
    assert strategy_paused(None, (), now)
    assert not strategy_paused(limits(), (), now)


# -------------------------------------------------- same-candle cluster limit

def entry(strategy, symbol='DOGEUSDT', side=LONG):
    return ClusterEntry(strategy_id=strategy, symbol=symbol, side=side)


def test_two_entries_per_strategy_per_candle():
    """4 Nov: three exp_vwaprev longs opened on one candle."""
    p = portfolio(cluster_entries=(entry('vwaprev'), entry('vwaprev', 'BNBUSDT')))
    refused(evaluation(p=p, r=request('PAXGUSDT')), RefusalCode.CANDLE_CLUSTER_LIMIT)
    approved(evaluation(p=p, r=request('PAXGUSDT', strategy='trend')))


def test_three_same_direction_entries_per_candle_across_strategies():
    p = portfolio(cluster_entries=(entry('trend'), entry('range', 'BNBUSDT'),
                                   entry('breakout', 'SOLUSDT')))
    refused(evaluation(p=p, r=request('PAXGUSDT')), RefusalCode.CANDLE_CLUSTER_LIMIT)
    approved(evaluation(p=p, r=request('PAXGUSDT', side=SHORT)))


def test_cluster_limit_does_not_count_against_adds():
    held = (exposure('BTCUSDT', strategy='vwaprev', risk='10'),)
    p = portfolio(exposures=held, cluster_entries=(entry('vwaprev'), entry('vwaprev', 'BNBUSDT')))
    approved(evaluation(p=p, r=request('BTCUSDT', action=RiskAction.ADD)))


# ---------------------------------------------------- net directional R cap

def test_net_side_risk_cap_is_four_r_and_opposite_side_offsets():
    wide = policy(loss_limits=limits(max_bucket_side_risk_r=D('100'), daily_loss_r=D('100'),
                                     daily_loss_fraction=D('1')))
    longs = (exposure('DOGEUSDT', risk='90'), exposure('PAXGUSDT', risk='90'))
    result = approved(evaluation(p=portfolio(exposures=longs), pol=wide, r=request(risk='100')))
    assert result.risk == D('20') and Ceiling.NET_SIDE_RISK in result.bindings
    hedged = longs + (exposure('SOLUSDT', side=SHORT, risk='80'),)
    assert approved(evaluation(p=portfolio(exposures=hedged), pol=wide,
                               r=request(risk='100'))).risk == D('100')


def test_net_offset_ignores_unfilled_and_cross_bucket_opposites():
    # Review 2: a reserved short may never fill, and a gold short does not
    # hedge crypto longs, so neither widens the long allowance.
    wide = policy(loss_limits=limits(max_bucket_side_risk_r=D('100'), daily_loss_r=D('100'),
                                     daily_loss_fraction=D('1')))
    longs = (exposure('DOGEUSDT', risk='90'), exposure('BNBUSDT', risk='90'))
    reserved_short = (reservation('SOLUSDT', side=SHORT, risk='80'),)
    assert approved(evaluation(p=portfolio(exposures=longs, reservations=reserved_short), pol=wide,
                               r=request(risk='100'))).risk == D('20')
    gold_short = longs + (exposure('PAXGUSDT', side=SHORT, risk='80'),)
    assert approved(evaluation(p=portfolio(exposures=gold_short), pol=wide,
                               r=request(risk='100'))).risk == D('20')


def test_stale_peak_below_day_start_cannot_hide_a_drawdown():
    # Review 2: peak 5000 but the day started at 10000; equity 8500 is 15% down.
    p = portfolio(peak_equity=D('5000'))
    refused(with_equity(evaluation(p=p), '8500'), RefusalCode.DRAWDOWN_KILL)
    assert drawdown_kill_breached(limits(), day(peak_equity=D('5000')), D('8500'))


def test_one_below_each_cluster_limit_still_approves():
    approved(evaluation(p=portfolio(cluster_entries=(entry('vwaprev'),)), r=request('PAXGUSDT')))
    approved(evaluation(p=portfolio(cluster_entries=(entry('trend'), entry('range', 'BNBUSDT'))),
                        r=request('PAXGUSDT')))


def test_strategy_paused_edge_inputs():
    now = 10 * DAY_MS
    assert not strategy_paused(limits(), (now - 1, now - 1), now)          # fewer than three
    assert strategy_paused(limits(), (now, now, now), now)                  # duplicates, at now
    assert strategy_paused(limits(), (True,), now)
    assert strategy_paused(limits(), (2 ** 63,), now)


# --------------------------------------------------------- malformed state

@pytest.mark.parametrize('bad', [
    {'peak_equity': D('0')}, {'peak_equity': None}, {'drawdown_kill_latched': 1},
    {'paused_strategies': ['vwaprev']}, {'paused_strategies': ('vwaprev', 'vwaprev')},
    {'paused_strategies': ('',)}, {'cluster_entries': [entry('trend')]},
    {'cluster_entries': (('trend', 'BTCUSDT', LONG),)},
    {'cluster_entries': (ClusterEntry(strategy_id='trend', symbol='btc', side=LONG),)},
])
def test_malformed_new_loss_state_refuses(bad):
    p = dataclasses.replace(portfolio(), loss=dataclasses.replace(day(), **bad))
    refused(evaluation(p=p), RefusalCode.INPUT_INVALID)


@pytest.mark.parametrize('bad', [
    {'max_drawdown_fraction': D('0')}, {'max_drawdown_fraction': D('1.5')},
    {'max_net_side_risk_r': D('0')}, {'max_entries_per_strategy_per_candle': 0},
    {'max_entries_per_side_per_candle': True}, {'cooldown_stop_outs': 2.0},
    {'cooldown_window_ms': 0}, {'cooldown_pause_ms': -1},
])
def test_malformed_new_limits_refuse(bad):
    refused(evaluation(pol=policy(loss_limits=limits(**bad))), RefusalCode.INPUT_INVALID)
