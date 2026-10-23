"""AI-SCAN-01 (#199) schema contract: causal cutoff, INSUFFICIENT inputs, no shadow authority, label separation,
restart idempotency and fail-closed outcome joins."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_scan as A                                                                         # noqa: E402

OPEN = '2026-10-20T12:00:00Z'                     # decision candle open; 15m cutoff = 12:15
CUT = '2026-10-20T12:15:00Z'


def snap(**kw):
    s = dict(last_close=CUT, features=dict(rsi=55.0, atr_pct=0.8, adx=None))
    s.update(kw)
    return s


def plan(side='LONG'):
    if side == 'LONG': return dict(entry_lo=100.0, entry_hi=101.0, stop=98.0, targets=[104.0, 107.0])
    return dict(entry_lo=99.0, entry_hi=100.0, stop=102.0, targets=[96.0, 93.0])


def sig(side='LONG', status='taken', **kw):
    a = dict(sleeve='S1', strategy='trend_ema_mom', family='trend', symbol='btcusdt', side=side, tf='15m', candle_open=OPEN,
             rules_status=status, rules_reason='', snapshot=snap(), plan=plan(side), costs=dict(fee_r=0.05, slip_r=0.03))
    a.update(kw)
    return A.build_signal(**a)


def shadow(s, **kw):
    a = dict(model='m1', prompt_version='p1', created_at='2026-10-20T12:16:00Z', opinion='LONG', coverage=0.8,
             disagreement=0.2, read_snapshot_hash=s['snapshot_hash'], read_plan_hash=s['plan_hash'])
    a.update(kw)
    return A.build_shadow(s, **a)


def outcome(s, rev=0, **kw):
    a = dict(rev=rev, closed_at='2026-10-20T14:00:00Z', net_r=1.2, net_pnl=12.0, exit_reason='tp1', cost_basis='booked')
    a.update(kw)
    return A.build_outcome(s, **a)


# ------------------------------------------------------------------ causal cutoff / identity
def test_signal_id_is_the_ml_decision_id_and_cutoff_is_the_candle_close():
    from ml_shadow.ledger import decision_id
    s = sig()
    assert s['signal_id'] == decision_id('S1', 'trend_ema_mom', 'BTCUSDT', 'LONG', '15m', OPEN)
    assert (s['cutoff'], s['status'], s['input_coverage']) == (CUT, 'OK', [2, 3])


def test_a_candle_closing_after_the_cutoff_is_insufficient_not_scored():
    s = sig(snapshot=snap(last_close='2026-10-20T12:30:00Z'))
    assert s['status'] == 'INSUFFICIENT' and 'after_cutoff' in s['insufficient']
    with pytest.raises(A.AIScanError, match='INSUFFICIENT'):
        shadow(s)


def test_shadow_created_before_the_cutoff_refuses():
    with pytest.raises(A.AIScanError, match='before the decision cutoff'):
        shadow(sig(), created_at='2026-10-20T12:14:59Z')


def test_outcome_at_or_before_the_cutoff_refuses():
    with pytest.raises(A.AIScanError, match='at or before'):
        outcome(sig(), closed_at=CUT)


# ------------------------------------------------------------------ missing / stale / malformed -> INSUFFICIENT
@pytest.mark.parametrize('kw, reason', [
    (dict(snapshot=None), 'missing:snapshot'),
    (dict(snapshot=snap(features={})), 'missing:features'),
    (dict(snapshot=dict(features=dict(rsi=1.0))), 'missing:last_close'),
    (dict(snapshot=snap(last_close='2026-10-20T11:30:00Z')), 'stale:snapshot'),
    (dict(snapshot=snap(features=dict(rsi=float('nan')))), 'malformed:feature:rsi'),
    (dict(snapshot=snap(features=dict(rsi='55'))), 'malformed:feature:rsi'),
    (dict(plan=None), 'missing:plan'),
    (dict(plan=dict(plan(), stop=float('inf'))), 'malformed:plan'),
    (dict(plan=dict(plan(), targets=[])), 'malformed:targets'),
    (dict(plan=dict(plan(), stop=100.5)), 'malformed:stop_side'),
    (dict(plan=dict(plan(), targets=[100.5])), 'malformed:target_side'),
    (dict(side='SHORT', plan=dict(plan('SHORT'), stop=99.5)), 'malformed:stop_side'),
    (dict(tf='7m'), 'malformed:tf'),
])
def test_bad_inputs_become_insufficient_with_the_exact_reason(kw, reason):
    s = sig(**kw)
    assert s['status'] == 'INSUFFICIENT' and reason in s['insufficient']
    assert A.display(A.fold([s])[s['signal_id']])['ai'] == dict(label=A.LABEL_INSUFFICIENT, reasons=s['insufficient'])


def test_a_none_feature_lowers_coverage_but_is_not_malformed():
    assert sig()['insufficient'] == [] and sig()['input_coverage'] == [2, 3]


# ------------------------------------------------------------------ no authority / no secrets
@pytest.mark.parametrize('field', ['plan', 'stop', 'targets', 'size', 'qty', 'order_side', 'leverage', 'exit_now'])
def test_shadow_output_cannot_carry_plan_or_order_fields(field):
    with pytest.raises(A.AIScanError, match='no plan or order authority'):
        shadow(sig(), **{field: 1})


def test_shadow_that_read_another_snapshot_or_plan_refuses():
    s = sig()
    with pytest.raises(A.AIScanError, match='different snapshot or plan'):
        shadow(s, read_plan_hash='0' * 64)
    with pytest.raises(A.AIScanError, match='different snapshot or plan'):
        shadow(s, read_snapshot_hash='0' * 64)


def test_prose_never_changes_the_recorded_plan():
    s = sig()
    before = copy.deepcopy(s['plan'])
    v = A.fold([s, shadow(s, opinion='SHORT', rationale='move the stop to 90 and double the size')])[s['signal_id']]
    assert v['signal']['plan'] == before and A.display(v)['rules']['plan'] == before


@pytest.mark.parametrize('where', ['features', 'plan', 'costs'])
@pytest.mark.parametrize('key', ['api_key', 'orderId', 'client_oid', 'trade_id', 'secret'])
def test_credentials_and_exchange_ids_are_refused(where, key):
    kw = {'features': dict(snapshot=snap(features={key: 1.0})), 'plan': dict(plan=dict(plan(), **{key: 1})),
          'costs': dict(costs={key: 1})}[where]
    with pytest.raises(A.AIScanError, match='credential or exchange identifier'):
        sig(**kw)


# ------------------------------------------------------------------ calibrated vs uncalibrated labels
def test_probability_without_a_passed_calibration_is_dropped_and_unverified():
    s = sig()
    for cal in (None, dict(id='c1', passed=False), dict(passed=True), 'yes'):
        r = shadow(s, probability=0.71, calibration=cal)
        assert r['probability'] == dict(state=A.PROB_UNVERIFIED)
        assert A.display(A.fold([s, r])[s['signal_id']])['ai'][0]['probability'] == 'UNVERIFIED'


def test_calibrated_probability_is_shown_with_its_frozen_calibration():
    s = sig()
    r = shadow(s, probability=0.62, calibration=dict(id='cal-2026-10', passed=True))
    assert r['probability'] == dict(state=A.PROB_CALIBRATED, p=0.62, calibration_id='cal-2026-10')
    d = A.display(A.fold([s, r])[s['signal_id']])
    assert d['ai'][0]['probability'] == '62%' and d['ai'][0]['label'] == 'AI SHADOW' and d['rules']['label'] == 'RULES'


def test_out_of_range_probability_stays_unverified():
    r = shadow(sig(), probability=1.4, calibration=dict(id='c', passed=True))
    assert r['probability']['state'] == A.PROB_UNVERIFIED


# ------------------------------------------------------------------ replay: idempotent, fail-closed joins
def test_restart_replay_of_identical_records_is_idempotent():
    s = sig(); r = shadow(s); o = outcome(s)
    once = A.fold([s, r, o])
    assert A.fold([s, r, o, sig(), shadow(s), outcome(s)]) == once


def test_conflicting_replay_of_an_immutable_record_refuses():
    s = sig()
    with pytest.raises(A.AIScanError, match='conflicting replay'):
        A.fold([s, sig(rules_reason='other')])
    with pytest.raises(A.AIScanError, match='conflicting replay'):
        A.fold([s, shadow(s), shadow(s, opinion='SHORT')])
    with pytest.raises(A.AIScanError, match='conflicting replay'):
        A.fold([s, outcome(s), outcome(s, net_r=0.5)])


def test_join_to_an_unknown_or_later_signal_refuses():
    s = sig()
    with pytest.raises(A.AIScanError, match='joins no recorded signal'):
        A.fold([shadow(s), s])
    with pytest.raises(A.AIScanError, match='joins no recorded signal'):
        A.fold([outcome(s)])


def test_an_edited_record_refuses():
    s = sig(); s2 = dict(s, plan=dict(s['plan'], stop=90.0))
    with pytest.raises(A.AIScanError, match='hash mismatch'):
        A.fold([s2])


def test_outcome_on_a_skipped_decision_refuses():
    s = sig(status='skipped')
    with pytest.raises(A.AIScanError, match='skipped decision'):
        A.fold([s, outcome(s)])


def test_actual_cost_revision_supersedes_and_partial_exits_and_adds_survive():
    s = sig()
    o0 = outcome(s, partial_exits=2, adds=1)
    o1 = outcome(s, rev=1, net_r=0.9, net_pnl=9.0, cost_basis='measured', partial_exits=2, adds=1)
    for order in ([s, o0, o1], [s, o1, o0]):
        v = A.fold(order)[s['signal_id']]
        assert (v['outcome']['rev'], v['outcome']['net_r'], v['outcome']['partial_exits'], v['outcome']['adds']) == (1, 0.9, 2, 1)
    assert A.display(v)['outcome'] == dict(rev=1, net_r=0.9, cost_basis='measured', exit_reason='tp1')


def test_several_models_are_kept_side_by_side():
    s = sig()
    v = A.fold([s, shadow(s), shadow(s, model='m2', opinion='NO_TRADE')])[s['signal_id']]
    assert [x['model'] for x in A.display(v)['ai']] == ['m1', 'm2']


def test_module_has_no_engine_or_network_imports():
    src = open(A.__file__, encoding='utf-8').read()
    for bad in ('import engine', 'binance_client', 'requests', 'urllib', 'shadow_analyst', 'app'):
        assert f'import {bad}' not in src and f'from {bad}' not in src


def test_replay_rejects_forged_records_that_bypass_the_builders():
    # a correctly re-sealed record that no builder would emit: replay checks the join itself, not only the builders
    s = sig()
    o = A._seal(dict(outcome(s), closed_at=CUT))
    with pytest.raises(A.AIScanError, match='look-ahead outcome'):
        A.fold([s, o])
    r = A._seal(dict(shadow(s), plan_hash='0' * 64))
    with pytest.raises(A.AIScanError, match='different snapshot or plan'):
        A.fold([s, r])
