"""AI-SCAN-01 (#199) schema contract: causal cutoff + availability, INSUFFICIENT inputs, no shadow authority, model
reproducibility / calibration epochs, label separation, restart idempotency, measured vs simulated outcomes."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_scan as A                                                                         # noqa: E402

OPEN = '2026-10-20T12:00:00Z'                     # decision candle open; 15m cutoff = 12:15
CUT = '2026-10-20T12:15:00Z'
DEC = '2026-10-20T12:15:10Z'                      # the engine's decision instant
H1, H2 = 'a' * 64, 'b' * 64
SAMP = dict(temperature=0, max_tokens=3000)


def snap(**kw):
    s = dict(last_close=CUT, available_at='2026-10-20T12:15:03Z', features=dict(rsi=55.0, atr_pct=0.8, adx=None))
    s.update(kw)
    return s


def inputs(**kw):
    d = dict(funding='2026-10-20T12:00:00Z', universe='2026-10-20T00:00:00Z', exchange_rules='2026-10-20T06:00:00Z')
    d.update(kw)
    return d


def plan(side='LONG'):
    if side == 'LONG': return dict(entry_lo=100.0, entry_hi=101.0, stop=98.0, targets=[104.0, 107.0])
    return dict(entry_lo=99.0, entry_hi=100.0, stop=102.0, targets=[96.0, 93.0])


def sig(side='LONG', status='taken', **kw):
    a = dict(sleeve='S1', strategy='trend_ema_mom', family='trend', symbol='btcusdt', side=side, tf='15m', candle_open=OPEN,
             decided_at=DEC, rules_status=status, rules_reason='', snapshot=snap(), plan=plan(side), inputs=inputs(),
             costs=dict(fee_r=0.05, slip_r=0.03))
    a.update(kw)
    return A.build_signal(**a)


def shadow(s, **kw):
    a = dict(model_id='claude-sonnet-5-5', prompt_hash=H1, sampling=SAMP, raw_response_hash=H2,
             created_at='2026-10-20T12:16:00Z', opinion='LONG', coverage=0.8, disagreement=0.2,
             read_snapshot_hash=s['snapshot_hash'], read_plan_hash=s['plan_hash'])
    a.update(kw)
    return A.build_shadow(s, **a)


TARGET = dict(label='net_r_gt_0', horizon_bars=16, cost_basis='measured')


def cal(**kw):
    c = dict(id='cal-2026-10', passed=True, epoch=A.epoch_of('claude-sonnet-5-5', H1, SAMP), target=TARGET)
    c.update(kw)
    return c


def outcome(s, rev=0, **kw):
    a = dict(source='measured', rev=rev, closed_at='2026-10-20T14:00:00Z', net_r=1.2, net_pnl=12.0, exit_reason='tp1',
             cost_basis='booked')
    a.update(kw)
    return A.build_outcome(s, **a)


# ------------------------------------------------------------------ causal cutoff / identity / availability
def test_signal_id_is_the_ml_decision_id_and_cutoff_is_the_candle_close():
    from ml_shadow.ledger import decision_id
    s = sig()
    assert s['signal_id'] == decision_id('S1', 'trend_ema_mom', 'BTCUSDT', 'LONG', '15m', OPEN)
    assert (s['cutoff'], s['decided_at'], s['status'], s['input_coverage']) == (CUT, DEC, 'OK', [2, 3])
    assert s['inputs_as_of'] == inputs()


def test_a_decision_before_its_candle_closed_refuses():
    with pytest.raises(A.AIScanError, match='before its candle closed'):
        sig(decided_at='2026-10-20T12:14:59Z')


def test_a_candle_closing_after_the_cutoff_is_insufficient_not_scored():
    s = sig(snapshot=snap(last_close='2026-10-20T12:30:00Z'))
    assert s['status'] == 'INSUFFICIENT' and 'after_cutoff' in s['insufficient']
    with pytest.raises(A.AIScanError, match='INSUFFICIENT'):
        shadow(s)


@pytest.mark.parametrize('kw, reason', [
    (dict(snapshot=snap(available_at='2026-10-20T12:15:11Z')), 'after_decision:snapshot'),
    (dict(snapshot=snap(available_at=None)), 'missing:snapshot'),
    (dict(inputs=inputs(funding='2026-10-20T12:20:00Z')), 'after_decision:funding'),
    (dict(inputs=inputs(universe=None)), 'missing:universe'),
    (dict(inputs=inputs(exchange_rules='2026-10-21T00:00:00Z')), 'after_decision:exchange_rules'),
    (dict(inputs=None), 'missing:funding'),
])
def test_an_input_not_available_by_the_decision_instant_is_insufficient(kw, reason):
    s = sig(**kw)
    assert s['status'] == 'INSUFFICIENT' and reason in s['insufficient']


def test_shadow_created_before_the_decision_refuses():
    with pytest.raises(A.AIScanError, match='before the decision'):
        shadow(sig(), created_at='2026-10-20T12:15:05Z')


def test_outcome_at_or_before_the_decision_refuses():
    with pytest.raises(A.AIScanError, match='at or before'):
        outcome(sig(), closed_at=DEC)


# ------------------------------------------------------------------ missing / stale / malformed -> INSUFFICIENT
@pytest.mark.parametrize('kw, reason', [
    (dict(snapshot=None), 'missing:snapshot'),
    (dict(snapshot=snap(features={})), 'missing:features'),
    (dict(snapshot=dict(available_at=DEC, features=dict(rsi=1.0))), 'missing:last_close'),
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


# ------------------------------------------------------------------ model reproducibility (Cowork #200 F3)
@pytest.mark.parametrize('kw', [dict(model_id=''), dict(model_id=None), dict(prompt_hash='v3'), dict(raw_response_hash=None),
                                dict(sampling=None), dict(prompt_hash='A' * 64)])
def test_a_shadow_without_pinned_model_prompt_sampling_and_response_hashes_refuses(kw):
    with pytest.raises(A.AIScanError, match='pinned model id'):
        shadow(sig(), **kw)


def test_any_change_of_model_prompt_or_sampling_opens_a_new_epoch():
    base = A.epoch_of('m', H1, SAMP)
    assert len({base, A.epoch_of('m2', H1, SAMP), A.epoch_of('m', H2, SAMP), A.epoch_of('m', H1, dict(SAMP, temperature=1))}) == 4
    s = sig()
    v = A.fold([s, shadow(s), shadow(s, sampling=dict(SAMP, temperature=0.5))])[s['signal_id']]
    assert len(v['shadows']) == 2


# ------------------------------------------------------------------ calibrated vs uncalibrated labels (F1/F2)
@pytest.mark.parametrize('c', [None, 'yes', dict(cal(), passed=False), dict(cal(), id=''), dict(cal(), epoch='other'),
                               dict(cal(), target=None), dict(cal(), target=dict(TARGET, horizon_bars=None)),
                               dict(cal(), target=dict(label='x', cost_basis='measured'))])
def test_probability_without_a_passed_same_epoch_calibration_and_target_is_unverified(c):
    s = sig()
    r = shadow(s, probability=0.71, calibration=c)
    assert r['probability'] == dict(state=A.PROB_UNVERIFIED)
    assert A.display(A.fold([s, r])[s['signal_id']])['ai'][0]['probability'] == 'UNVERIFIED'


def test_a_calibration_of_another_model_epoch_never_carries_over():
    r = shadow(sig(), model_id='claude-opus-5-5', probability=0.6, calibration=cal())
    assert r['probability']['state'] == A.PROB_UNVERIFIED


def test_calibrated_probability_is_shown_with_its_target():
    s = sig()
    r = shadow(s, probability=0.62, calibration=cal())
    assert r['probability'] == dict(state=A.PROB_CALIBRATED, p=0.62, calibration_id='cal-2026-10', target=TARGET)
    d = A.display(A.fold([s, r])[s['signal_id']])
    assert d['ai'][0]['probability'] == dict(value='62%', target=TARGET)
    assert d['ai'][0]['label'] == 'AI SHADOW' and d['rules']['label'] == 'RULES'


def test_out_of_range_probability_stays_unverified():
    assert shadow(sig(), probability=1.4, calibration=cal())['probability']['state'] == A.PROB_UNVERIFIED


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


def test_replay_rejects_forged_records_that_bypass_the_builders():
    # correctly re-sealed records no builder would emit: replay checks the joins itself, not only the builders
    s = sig()
    with pytest.raises(A.AIScanError, match='look-ahead outcome'):
        A.fold([s, A._seal(dict(outcome(s), closed_at=DEC))])
    with pytest.raises(A.AIScanError, match='different snapshot or plan'):
        A.fold([s, A._seal(dict(shadow(s), plan_hash='0' * 64))])
    k = sig(status='skipped')
    with pytest.raises(A.AIScanError, match='measured outcome on a skipped'):
        A.fold([k, A._seal(dict(outcome(sig()), signal_id=k['signal_id']))])


# ------------------------------------------------------------------ measured vs simulated outcomes (F4)
def test_an_untraded_candidate_gets_only_a_simulated_outcome():
    s = sig(status='skipped')
    with pytest.raises(A.AIScanError, match='measured outcome on a skipped'):
        outcome(s)
    v = A.fold([s, outcome(s, source='simulated', cost_basis='modelled')])[s['signal_id']]
    assert set(v['outcomes']) == {'simulated'}


@pytest.mark.parametrize('src, basis', [('measured', 'modelled'), ('simulated', 'booked'), ('simulated', 'measured'),
                                        ('replayed', 'modelled')])
def test_cost_basis_must_match_the_outcome_source(src, basis):
    with pytest.raises(A.AIScanError):
        outcome(sig(), source=src, cost_basis=basis)


def test_measured_and_simulated_outcomes_are_never_merged():
    s = sig()
    v = A.fold([s, outcome(s), outcome(s, source='simulated', cost_basis='modelled', net_r=2.0)])[s['signal_id']]
    d = A.display(v)['outcomes']
    assert (d['measured']['net_r'], d['simulated']['net_r']) == (1.2, 2.0)


def test_actual_cost_revision_supersedes_and_partial_exits_and_adds_survive():
    s = sig()
    o0 = outcome(s, partial_exits=2, adds=1)
    o1 = outcome(s, rev=1, net_r=0.9, net_pnl=9.0, cost_basis='measured', partial_exits=2, adds=1)
    for order in ([s, o0, o1], [s, o1, o0]):
        v = A.fold(order)[s['signal_id']]
        m = v['outcomes']['measured']
        assert (m['rev'], m['net_r'], m['partial_exits'], m['adds']) == (1, 0.9, 2, 1)
    assert A.display(v)['outcomes'] == dict(measured=dict(rev=1, net_r=0.9, cost_basis='measured', exit_reason='tp1'))


def test_several_models_are_kept_side_by_side():
    s = sig()
    v = A.fold([s, shadow(s), shadow(s, model_id='claude-opus-5-5', opinion='NO_TRADE')])[s['signal_id']]
    assert [x['model_id'] for x in A.display(v)['ai']] == ['claude-opus-5-5', 'claude-sonnet-5-5']


def test_module_has_no_engine_or_network_imports():
    src = open(A.__file__, encoding='utf-8').read()
    for bad in ('import engine', 'binance_client', 'requests', 'urllib', 'shadow_analyst', 'app'):
        assert f'import {bad}' not in src and f'from {bad}' not in src
