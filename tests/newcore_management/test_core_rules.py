"""NC-07 core rules driven directly through `step` (the live path: confirmed fills / refusals, closed candles)."""
import dataclasses
import itertools
from decimal import Decimal as D

import pytest

from mg_factories import GOLDEN_COSTS, H4, LONG, SHORT, T0, ZERO_COSTS, candle, flat, kinds, plan, px, rules
from newcore.domain import DomainError, InvalidRecord, ReasonCode as R
from newcore.management import (ActionKind as AK, AddPhase, ConfirmedFill, Leg, ManagementError, PlanNote, PlanRefused,
                                Rejected, Stage, StepInput, Tier, admit_entry, average, build_plan, initial_state,
                                planned_risk, realized_pnl, replay, step)
from newcore.management.plan import sgn

SIDES = (LONG, SHORT)


_IDS = itertools.count()


def fill(leg, qty, price, fee='0', fid=None):
    return ConfirmedFill(fill_id=fid or f'f{next(_IDS)}', leg=leg, qty=D(qty), price=D(price), fee=D(fee))


def start(p, fee='0'):
    return step(p, initial_state(p, D(fee)))


def add_plan(side, **kw):
    kw.setdefault('cap', '20')
    return plan(side, add='99.02', tp1_frac='0.5', tp1_off='1', tp2_off='2', be=True, **kw)


# ------------------------------------------------------------------------------------------------------------ plan
@pytest.mark.parametrize('side', SIDES)
def test_planned_risk_reserves_the_add_and_respects_the_cap(side):
    p = add_plan(side)
    assert p.add_qty == D(5) and planned_risk(p) <= p.risk_cap
    no_add = plan(side, cap='20')
    assert planned_risk(p) > planned_risk(no_add)              # the add's risk is reserved at entry
    with pytest.raises(InvalidRecord, match='planned risk'):
        dataclasses.replace(p, risk_cap=planned_risk(p) - D('0.01'))


@pytest.mark.parametrize('side', SIDES)
def test_build_plan_drops_an_add_over_the_cap_and_refuses_an_entry_over_it(side):
    b = build_plan(**_kw(side, cap='12', add=px(side, '99.02'), scale=D(1)))
    assert b.plan.add_price is None and b.notes == (PlanNote.ADD_OVER_CAP,)
    b = build_plan(**_kw(side, cap='20', add=px(side, '99.02'), scale=D('0.0001'), qty='5'))
    assert b.plan.add_price is None and b.notes == (PlanNote.ADD_BELOW_MIN,)
    with pytest.raises(PlanRefused):
        build_plan(**_kw(side, cap='5'))


def _kw(side, cap, add=None, scale=None, qty='5'):
    return dict(rules=rules(min_notional='5'), side=side, entry_price=px(side, '100.02'), entry_qty=D(qty),
                entry_candle_open_ms=T0, candle_seconds=14400, stop_price=px(side, '98.02'), risk_cap=D(cap),
                costs=GOLDEN_COSTS, add_price=add, add_scale=scale)


@pytest.mark.parametrize('side', SIDES)
def test_add_quantity_is_floored_never_rounded_up(side):
    p = plan(side, r=rules(step='0.01'), qty='0.33', add='99.02', scale='1.5', cap='20')
    assert p.add_qty == D('0.49')                              # 1.5 x 0.33 = 0.495 -> 0.49
    with pytest.raises(InvalidRecord, match='floor'):
        dataclasses.replace(p, add_qty=D('0.5'))


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('bad', ['stop_side', 'add_beyond_stop', 'max_qty', 'tp2_inside_tp1', 'be_without_tp1'])
def test_plan_rejects_invalid_levels(side, bad):
    p = add_plan(side)
    change = {'stop_side': dict(stop_price=px(side, '101')), 'add_beyond_stop': dict(add_price=px(side, '97')),
              'max_qty': dict(rules=rules(max_qty='9')), 'tp2_inside_tp1': dict(tp2_offset=D('0.5')),
              'be_without_tp1': dict(tp1_frac=None, tp1_offset=None)}[bad]
    with pytest.raises(InvalidRecord):
        dataclasses.replace(p, **change)


# ------------------------------------------------------------------------------------------------------- admission
@pytest.mark.parametrize('side', SIDES)
def test_cost_to_stop_veto(side):
    e = px(side, '100')
    # 4h-like 2-ATR stop (2% away): 0.0014 x 100 / 2 = 0.07R -> admitted
    a = admit_entry(side=side, entry_price=e, stop_price=e - sgn(side) * 2, costs=GOLDEN_COSTS)
    assert a.admitted and a.cost_r == D('0.07')
    # 1h-like tight stop (0.5% away): 0.28R -> refused
    a = admit_entry(side=side, entry_price=e, stop_price=e - sgn(side) * D('0.5'), costs=GOLDEN_COSTS)
    assert not a.admitted and a.verdict.value == 'cost_to_stop' and a.cost_r == D('0.28')
    # default 0.15R: a 0.9 stop costs 0.1555R (refused), a 1.0 stop 0.14R (admitted)
    assert not admit_entry(side=side, entry_price=e, stop_price=e - sgn(side) * D('0.9'), costs=GOLDEN_COSTS).admitted
    one = e - sgn(side) * 1
    assert admit_entry(side=side, entry_price=e, stop_price=one, costs=GOLDEN_COSTS).admitted
    # configurable; exactly at the limit is admitted, only "exceeds" is refused
    assert admit_entry(side=side, entry_price=e, stop_price=one, costs=GOLDEN_COSTS, max_cost_r=D('0.14')).admitted
    assert not admit_entry(side=side, entry_price=e, stop_price=one, costs=GOLDEN_COSTS,
                           max_cost_r=D('0.1399')).admitted
    assert admit_entry(side=side, entry_price=e, stop_price=e + sgn(side), costs=GOLDEN_COSTS).verdict.value == \
        'stop_wrong_side'


# ------------------------------------------------------------------------------------------------- first step / order
@pytest.mark.parametrize('side', SIDES)
def test_first_step_places_protection_first_and_the_add_last(side):
    p = add_plan(side)
    r = start(p)
    assert kinds(r.actions) == [('place_stop', 'stop'), ('place_target', 'tp1'), ('place_target', 'tp2'),
                                ('place_add', 'add')]
    assert [a.tier for a in r.actions] == sorted(a.tier for a in r.actions)
    st = r.state
    assert st.stage is Stage.ACTIVE and st.stop.qty == D(5) and st.stop.price == px(side, '98.02')
    assert st.tp1.qty == D('2.5') and st.tp2.qty == D('2.5') and st.add.qty == D(5)
    assert r.actions[-1].reason is R.ENTRY_DCA_LEVEL


@pytest.mark.parametrize('side', SIDES)
def test_pyramid_add_is_supported_and_labelled(side):
    p = plan(side, stop='99.02', add='101.02', cap='20', tp2_off='3')
    r = start(p)
    assert p.add_is_pyramid and r.actions[-1].kind is AK.PLACE_ADD and r.actions[-1].reason is R.ENTRY_PYRAMID


# --------------------------------------------------------------------------------------------------- partial fills
@pytest.mark.parametrize('side', SIDES)
def test_partial_add_fills_recompute_average_stop_and_targets_from_actual_fills(side):
    p = add_plan(side, costs=ZERO_COSTS)
    st = start(p).state
    a1 = px(side, '99.03')                                      # actual fill price, not the level
    r = step(p, st, (fill(Leg.ADD, '2', a1),))
    st = r.state
    assert st.qty == D(7) and st.add.qty == D(3) and st.add_phase is AddPhase.WORKING
    assert average(st) == (D(5) * px(side, '100.02') + 2 * a1) / 7
    assert st.stop.qty == D(7) and kinds(r.actions)[0] == ('replace_stop', 'stop')
    assert r.actions[0].reason is R.PROTECT_RESIZE
    assert st.tp1.qty == D('3.5') and st.tp2.qty == D('3.5')
    assert not any(a.kind is AK.PLACE_ADD for a in r.actions)
    r = step(p, st, (fill(Leg.ADD, '3', px(side, '99.01')),))
    st = r.state
    assert st.qty == D(10) and st.add is None and st.add_phase is AddPhase.CLOSED and st.stop.qty == D(10)
    assert st.tp1.qty + st.tp2.qty == D(10)


@pytest.mark.parametrize('side', SIDES)
def test_partial_tp1_fill_cancels_add_keeps_remainder_and_arms_be_only_when_complete(side):
    p = add_plan(side, costs=ZERO_COSTS)
    st = start(p).state
    tp1 = st.tp1.price
    r = step(p, st, (fill(Leg.TP1, '1', tp1),))
    st = r.state
    assert st.tp1_confirmed and not st.tp1_done and st.qty == D(4) and st.tp1.qty == D('1.5') and st.tp2.qty == D('2.5')
    assert st.stop.qty == D(4) and st.stop.price == px(side, '98.02')       # a partial TP1 does not arm break-even
    # the partly filled TP1 order keeps working with its remainder: no new target request
    assert kinds(r.actions) == [('replace_stop', 'stop'), ('cancel_add', 'add')]
    assert r.actions[0].reason is R.PROTECT_RESIZE and r.actions[1].reason is R.EXIT_TP1 and st.add is None
    r = step(p, st, (fill(Leg.TP1, '1.5', tp1),))
    assert r.state.tp1_done and r.state.stop.price == px(side, '100.02') and r.state.stop.qty == D('2.5')


@pytest.mark.parametrize('side', SIDES)
def test_tp1_partial_that_rounds_to_zero_is_skipped_then_appears_after_the_add(side):
    p = plan(side, r=rules(step='5', min_qty='5'), add='99.02', cap='30', tp1_frac='0.5', tp1_off='1', tp2_off='2')
    st = start(p).state
    assert st.tp1 is None and st.tp2.qty == D(5)
    st = step(p, st, (fill(Leg.ADD, '5', px(side, '99.02')),)).state
    assert st.tp1.qty == D(5) and st.tp2.qty == D(5)          # 0.5 x 10 = 5: one step


# ------------------------------------------------------------------------------------------------ break-even rule
@pytest.mark.parametrize('side', SIDES)
def test_break_even_never_on_a_touch(side):
    p = add_plan(side)
    st = start(p).state
    tp1 = st.tp1.price
    c = candle(0, '100', '101.6', '99.9', '101.5', side)      # the candle trades through TP1 but nothing filled
    assert (c.high > tp1) if side is LONG else (c.low < tp1)
    r = step(p, st, (), c)
    assert not any(a.kind is AK.REPLACE_STOP for a in r.actions) and r.state.stop.price == px(side, '98.02')
    assert not r.state.tp1_confirmed


@pytest.mark.parametrize('side', SIDES)
def test_no_add_after_tp1_and_a_late_add_fill_is_flattened(side):
    p = add_plan(side, costs=ZERO_COSTS)
    st = start(p).state
    st = step(p, st, (fill(Leg.TP1, '2.5', st.tp1.price),)).state
    assert st.add is None and st.add_phase is AddPhase.CLOSED
    r = step(p, st, (fill(Leg.ADD, '5', px(side, '99.02')),))      # raced with the cancel
    assert kinds(r.actions)[:2] == [('replace_stop', 'stop'), ('reduce', 'close')]
    red = r.actions[1]
    assert red.qty == D(5) and red.reason is R.EXIT_FLATTEN and r.state.stop.qty == r.state.qty == D('7.5')
    for x in range(3):
        r = step(p, r.state, (), flat(x + 1, side=side))
        assert not any(a.kind is AK.PLACE_ADD for a in r.actions)


# ------------------------------------------------------------------------------------------------- refusals
@pytest.mark.parametrize('side', SIDES)
def test_stop_raise_refused_after_add_flattens_the_add(side):
    p = add_plan(side, costs=ZERO_COSTS)
    st = start(p).state
    st = step(p, st, (fill(Leg.ADD, '5', px(side, '99.02')),)).state
    assert st.stop.qty == D(10) and st.stop_prev.qty == D(5)
    r = step(p, st, (Rejected(leg=Leg.STOP),))
    st = r.state
    assert st.stop_locked and st.stop.qty == D(5)
    red = [a for a in r.actions if a.kind is AK.REDUCE]
    assert len(red) == 1 and red[0].qty == D(5) and red[0].reason is R.EXIT_STOP_FAILED
    assert st.closing == D(5) and st.tp1.qty + st.tp2.qty <= D(5)
    st = step(p, st, (fill(Leg.CLOSE, '5', px(side, '99')),)).state
    assert st.qty == D(5) and st.stop.qty == D(5) and st.closing == 0


@pytest.mark.parametrize('side', SIDES)
def test_first_stop_refused_closes_everything(side):
    p = add_plan(side)
    st = start(p).state
    r = step(p, st, (Rejected(leg=Leg.STOP),))
    assert kinds(r.actions) == [('cancel_add', 'add'), ('close', 'close'), ('cancel_target', 'tp1'),
                                ('cancel_target', 'tp2')]
    assert r.actions[1].qty == D(5) and r.actions[1].reason is R.EXIT_STOP_FAILED and r.state.stop is None


@pytest.mark.parametrize('side', SIDES)
def test_break_even_move_refused_closes_the_remainder(side):
    p = add_plan(side, costs=ZERO_COSTS)
    st = start(p).state
    st = step(p, st, (fill(Leg.TP1, '2.5', st.tp1.price),)).state
    r = step(p, st, (Rejected(leg=Leg.STOP),))
    assert r.state.stop.price == px(side, '98.02')
    assert [a for a in r.actions if a.kind is AK.CLOSE][0].qty == D('2.5')


@pytest.mark.parametrize('side', SIDES)
def test_add_and_target_refusals_are_not_retried(side):
    p = add_plan(side)
    st = start(p).state
    r = step(p, st, (Rejected(leg=Leg.ADD), Rejected(leg=Leg.TP1)))
    assert r.state.add is None and r.state.add_phase is AddPhase.CLOSED and r.state.targets_off
    assert kinds(r.actions) == [('cancel_target', 'tp2')]
    r = step(p, r.state, (), flat(0, side=side))
    assert r.actions == ()


# ----------------------------------------------------------------------------------------- time exit / crossed / trail
@pytest.mark.parametrize('side', SIDES)
def test_time_exit_sequence_keeps_the_stop_until_flat(side):
    p = add_plan(side, time_exit=3)
    st = start(p).state
    for i in range(2):
        r = step(p, st, (), flat(i, side=side))
        assert r.actions == ()
        st = r.state
    r = step(p, st, (), flat(2, side=side))
    assert kinds(r.actions) == [('cancel_add', 'add'), ('time_exit', 'close'), ('cancel_target', 'tp1'),
                                ('cancel_target', 'tp2')]
    assert r.state.stop.qty == D(5) and r.state.closing == D(5)
    r = step(p, r.state, (fill(Leg.CLOSE, '5', px(side, '100')),))
    assert kinds(r.actions) == [('cancel_stop', 'stop')] and r.state.stage is Stage.DONE


@pytest.mark.parametrize('side', SIDES)
def test_stop_crossed_at_the_close_is_a_bot_market_close(side):
    p = plan(side)
    st = start(p).state
    r = step(p, st, (), candle(0, '100', '100.1', '97.9', '98.0', side))
    assert kinds(r.actions) == [('close', 'close')] and r.actions[0].reason is R.EXIT_STOP_CROSSED


@pytest.mark.parametrize('side', SIDES)
def test_trail_ratchets_after_tp1_and_never_loosens(side):
    p = plan(side, tp1_frac='0.5', tp1_off='1', tp2_off='5', be=True, trail='1.5', costs=ZERO_COSTS, cap='12')
    st = start(p).state
    r = step(p, st, (), candle(0, '100', '102', '99.9', '101.9', side))
    assert r.state.stop.price == px(side, '98.02')              # trail waits for the confirmed TP1
    st = step(p, r.state, (fill(Leg.TP1, '2.5', r.state.tp1.price),)).state
    assert st.stop.price == px(side, '100.02')
    st = step(p, st, (), candle(1, '101.9', '103', '101.8', '102.9', side)).state
    assert st.stop.price == px(side, '101.4')                  # 102.9 - 1.5
    st2 = step(p, st, (), candle(2, '102.9', '103', '101.9', '102', side)).state
    assert st2.stop.price == px(side, '101.4')                 # 102 - 1.5 = 100.5 is looser: kept


# ------------------------------------------------------------------------------------------------- input guards
def test_bad_inputs_are_typed_failures():
    p = add_plan(LONG)
    st = start(p).state
    with pytest.raises(ManagementError):
        step(p, st, (), candle(-1, '100', '100.5', '99.5', '100'))         # before the entry candle
    c = flat(1)
    with pytest.raises(ManagementError):
        step(p, st, (), dataclasses.replace(c, open_ms=c.open_ms + 1))     # off the candle grid
    st1 = step(p, st, (), c).state
    with pytest.raises(ManagementError):
        step(p, st1, (), flat(1))                                         # candles only go forward
    with pytest.raises(DomainError):
        step(p, st, (fill(Leg.TP1, '3', '101'),))                          # beyond its order
    with pytest.raises(DomainError):
        step(p, st, (fill(Leg.ADD, '0.0005', '99'),))                       # off the step
    with pytest.raises(DomainError):
        step(p, st, (fill(Leg.STOP, '6', '98'),))                           # beyond the position
    st2 = step(p, st, (fill(Leg.ADD, '5', '99.02'),)).state
    with pytest.raises(DomainError):
        step(p, st2, (fill(Leg.ADD, '1', '99.02'),))                        # a second add: the cap is one


# ---------------------------------------------------------------------------------------- accounting / restart
@pytest.mark.parametrize('side', SIDES)
def test_fees_and_funding_are_accounted(side):
    p = plan(side, costs=ZERO_COSTS)
    st = step(p, initial_state(p, D('0.25'))).state
    st = step(p, st, (), flat(0, side=side), funding=D('0.1')).state
    st = step(p, st, (), flat(1, side=side), funding=D('-0.03')).state
    st = step(p, st, (fill(Leg.STOP, '5', px(side, '98.02'), fee='0.24'),)).state
    assert st.fees == D('0.49') and st.funding == D('0.07')
    gross = sgn(side) * (5 * px(side, '98.02') - 5 * px(side, '100.02'))
    assert realized_pnl(p, st) == gross - D('0.49') - D('0.07')


def _log(side):
    return [StepInput(confirmed=(), candle=None, close_request=None, funding=None),
            StepInput(confirmed=(fill(Leg.ADD, '2', px(side, '99.02')),), candle=None, close_request=None, funding=None),
            StepInput(confirmed=(), candle=flat(0, '99.5', side=side), close_request=None, funding=D('0.01')),
            StepInput(confirmed=(fill(Leg.ADD, '3', px(side, '99.0')),), candle=None, close_request=None, funding=None),
            StepInput(confirmed=(), candle=flat(1, '99.5', side=side), close_request=None, funding=None),
            StepInput(confirmed=(fill(Leg.TP1, '5', px(side, '100.53')),), candle=None, close_request=None,
                      funding=None),
            StepInput(confirmed=(), candle=flat(2, '100.6', side=side), close_request=None, funding=None)]


@pytest.mark.parametrize('side', SIDES)
def test_restart_from_persisted_state_or_from_the_input_log_is_identical(side):
    p = add_plan(side)
    log = _log(side)
    full = replay(p, D(0), log)
    # restart after input 3: rebuild the state record from its fields (what NC-02 persists) and continue
    head = replay(p, D(0), log[:4])
    saved = {f.name: getattr(head[-1].state, f.name) for f in dataclasses.fields(head[-1].state)}
    st = type(head[-1].state)(**saved)
    tail = []
    for x in log[4:]:
        r = step(p, st, x.confirmed, x.candle, close_request=x.close_request, funding=x.funding)
        tail.append(r)
        st = r.state
    assert head + tuple(tail) == full
    assert replay(p, D(0), log) == full                       # deterministic


def test_steps_on_a_done_position_are_inert():
    p = plan(LONG)
    st = start(p).state
    st = step(p, st, (fill(Leg.STOP, '5', '98.02'),)).state
    assert st.stage is Stage.DONE
    assert step(p, st, (), flat(0)).actions == ()
    with pytest.raises(DomainError):
        step(p, st, (fill(Leg.STOP, '1', '98'),))


def test_tier_order_is_protect_close_reduce_add_release():
    assert [t.name for t in Tier] == ['PROTECT', 'CLOSE', 'REDUCE', 'ADD', 'RELEASE']
    assert H4 == 14_400_000
