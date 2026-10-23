"""AI-SCAN-01 (#199/#200) schema contract: exact-cutoff snapshot + ingest-time inputs, INSUFFICIENT inputs, no shadow
authority, immutable model identity / calibration epochs and the frozen gate, display freshness, the versioned flag-off
shape, the bounded non-blocking queue, holdout isolation, restart idempotency, measured vs simulated outcomes."""
import copy
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_scan as A                                                                         # noqa: E402

OPEN = '2026-10-20T12:00:00Z'                     # decision candle open; 15m cutoff = 12:15
CUT = '2026-10-20T12:15:00Z'
DEC = '2026-10-20T12:15:10Z'                      # the engine's decision instant
H1, H2, H3, H4 = 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64
SAMP = dict(temperature=0, max_tokens=3000)
MODEL = 'claude-sonnet-5-5'
IDENT = dict(model_id=MODEL, prompt_hash=H1, sampling=SAMP, feature_schema_hash=H3, cost_model_hash=H4)
EPOCH = A.epoch_of(MODEL, H1, SAMP, H3, H4)


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
    a = dict(IDENT, raw_response_hash=H2, created_at='2026-10-20T12:16:00Z', opinion='LONG', coverage=0.8, disagreement=0.2,
             read_snapshot_hash=s['snapshot_hash'], read_plan_hash=s['plan_hash'])
    a.update(kw)
    return A.build_shadow(s, **a)


def failure(s, reason='timeout', **kw):
    a = dict(IDENT, created_at='2026-10-20T12:16:00Z', reason=reason, read_snapshot_hash=s['snapshot_hash'],
             read_plan_hash=s['plan_hash'])
    a.update(kw)
    return A.build_shadow_failure(s, **a)


def cal(**kw):
    c = dict(id='cal-2026-10', epoch=EPOCH, target=dict(A.TARGET), disjoint=True, n=500, min_bin_n=50, ece=0.05,
             brier_improvement_ci_lo=0.001, windows=[dict(ece=0.04, beats_base=True)])
    c.update(kw)
    return c


def outcome(s, **kw):
    a = dict(source='measured', cost_rev=0, closed_at='2026-10-20T14:00:00Z', net_r=1.2, net_pnl=12.0, exit_reason='tp1',
             cost_basis='booked')
    a.update(kw)
    return A.build_outcome(s, **a)


def sim(s, **kw):
    return outcome(s, **dict(dict(source='simulated', cost_rev=None, cost_model_hash=H4, cost_basis='modelled'), **kw))


# ------------------------------------------------------------------ causal cutoff / identity / ingest time
def test_signal_id_is_the_ml_decision_id_and_cutoff_is_the_candle_close():
    from ml_shadow.ledger import decision_id
    s = sig()
    assert s['signal_id'] == decision_id('S1', 'trend_ema_mom', 'BTCUSDT', 'LONG', '15m', OPEN)
    assert (s['cutoff'], s['decided_at'], s['status'], s['input_coverage']) == (CUT, DEC, 'OK', [2, 3])
    assert s['inputs_as_of'] == inputs()


def test_a_decision_before_its_candle_closed_refuses():
    with pytest.raises(A.AIScanError, match='before its candle closed'):
        sig(decided_at='2026-10-20T12:14:59Z')


@pytest.mark.parametrize('lc, reason', [('2026-10-20T12:30:00Z', 'after_cutoff'), ('2026-10-20T12:00:00Z', 'not_at_cutoff')])
def test_the_snapshot_must_be_the_exact_cutoff_candle(lc, reason):
    s = sig(snapshot=snap(last_close=lc))
    assert s['status'] == 'INSUFFICIENT' and reason in s['insufficient']
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
def test_an_input_ingested_after_the_decision_instant_is_insufficient(kw, reason):
    s = sig(**kw)
    assert s['status'] == 'INSUFFICIENT' and reason in s['insufficient']


def test_shadow_created_before_the_decision_refuses():
    with pytest.raises(A.AIScanError, match='before the decision'):
        shadow(sig(), created_at='2026-10-20T12:15:05Z')


def test_outcome_at_or_before_the_decision_refuses():
    with pytest.raises(A.AIScanError, match='at or before'):
        outcome(sig(), closed_at=DEC)


# ------------------------------------------------------------------ missing / malformed -> INSUFFICIENT
@pytest.mark.parametrize('kw, reason', [
    (dict(snapshot=None), 'missing:snapshot'),
    (dict(snapshot=snap(features={})), 'missing:features'),
    (dict(snapshot=dict(available_at=DEC, features=dict(rsi=1.0))), 'missing:last_close'),
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
    with pytest.raises(A.AIScanError, match='no plan or order authority'):
        failure(sig(), **{field: 1})


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


# ------------------------------------------------------------------ immutable model identity / epochs
@pytest.mark.parametrize('kw', [dict(model_id=''), dict(model_id=None), dict(prompt_hash='v3'), dict(sampling=None),
                                dict(prompt_hash='A' * 64), dict(feature_schema_hash=None), dict(cost_model_hash='x')])
def test_a_shadow_without_an_immutable_identity_refuses(kw):
    with pytest.raises(A.AIScanError, match='immutable model id'):
        shadow(sig(), **kw)


def test_a_shadow_result_without_a_raw_response_hash_refuses():
    with pytest.raises(A.AIScanError, match='raw response'):
        shadow(sig(), raw_response_hash=None)


def test_any_identity_or_label_change_opens_a_new_epoch(monkeypatch):
    eps = {EPOCH, A.epoch_of('m2', H1, SAMP, H3, H4), A.epoch_of(MODEL, H2, SAMP, H3, H4),
           A.epoch_of(MODEL, H1, dict(SAMP, temperature=1), H3, H4), A.epoch_of(MODEL, H1, SAMP, H2, H4),
           A.epoch_of(MODEL, H1, SAMP, H3, H2)}
    assert len(eps) == 6
    monkeypatch.setattr(A, 'TARGET', dict(A.TARGET, horizon='fixed_16_bars'))
    assert A.epoch_of(MODEL, H1, SAMP, H3, H4) != EPOCH
    s = sig()
    assert len(A.fold([s, shadow(s), shadow(s, sampling=dict(SAMP, temperature=0.5))])[s['signal_id']]['shadows']) == 2


# ------------------------------------------------------------------ the frozen calibration gate and demotion
def test_a_calibration_meeting_every_gate_bar_shows_the_probability_with_its_target():
    s = sig()
    r = shadow(s, probability=0.62, calibration=cal())
    assert r['probability'] == dict(state=A.PROB_CALIBRATED, p=0.62, calibration_id='cal-2026-10', target=A.TARGET)
    d = A.display(A.fold([s, r])[s['signal_id']])
    assert d['ai'][0]['probability'] == dict(value='62%', target=A.TARGET)
    assert d['ai'][0]['label'] == 'AI SHADOW' and d['rules']['label'] == 'RULES'


@pytest.mark.parametrize('kw', [dict(n=499), dict(min_bin_n=49), dict(ece=0.0501), dict(brier_improvement_ci_lo=0.0),
                                dict(disjoint=False), dict(disjoint='yes'), dict(id=''), dict(epoch='other'),
                                dict(target=dict(A.TARGET, horizon='fixed_16_bars')), dict(n=float('nan')),
                                dict(windows=[dict(ece=0.081, beats_base=True)])])
def test_a_calibration_missing_any_gate_bar_stays_unverified(kw):
    s = sig()
    r = shadow(s, probability=0.71, calibration=cal(**kw))
    assert r['probability'] == dict(state=A.PROB_UNVERIFIED)
    assert A.display(A.fold([s, r])[s['signal_id']])['ai'][0]['probability'] == 'UNVERIFIED'


def test_a_calibration_never_carries_to_another_epoch():
    assert shadow(sig(), model_id='claude-opus-5-5', probability=0.6, calibration=cal())['probability']['state'] == A.PROB_UNVERIFIED
    assert shadow(sig(), cost_model_hash=H1, probability=0.6, calibration=cal())['probability']['state'] == A.PROB_UNVERIFIED


@pytest.mark.parametrize('windows, out', [
    ([], False), (None, False), ([dict(ece=0.08, beats_base=True)], False),
    ([dict(ece=0.081, beats_base=True)], True),
    ([dict(ece=0.01, beats_base=False), dict(ece=0.01, beats_base=True), dict(ece=0.01, beats_base=False)], False),
    ([dict(ece=0.01, beats_base=True), dict(ece=0.01, beats_base=False), dict(ece=0.01, beats_base=False)], True),
    ([dict(ece=0.01)], True), (['x'], True),
])
def test_rolling_demotion(windows, out):
    assert A.demoted(windows) is out


def test_out_of_range_probability_stays_unverified():
    assert shadow(sig(), probability=1.4, calibration=cal())['probability']['state'] == A.PROB_UNVERIFIED


# ------------------------------------------------------------------ display freshness and the flag-off shape
def test_a_shadow_is_display_fresh_for_the_earlier_of_two_bars_or_thirty_minutes():
    s = sig(); v = A.fold([s, shadow(s)])[s['signal_id']]          # 15m: two bars = 30 min = the cap
    assert A.display(v, now='2026-10-20T12:46:00Z')['ai'][0]['stale'] is False
    assert A.display(v, now='2026-10-20T12:46:01Z')['ai'][0]['stale'] is True
    h = sig(tf='1h', candle_open='2026-10-20T11:00:00Z', snapshot=snap(last_close='2026-10-20T12:00:00Z',
                                                                        available_at='2026-10-20T12:00:03Z'),
            decided_at='2026-10-20T12:00:10Z')
    w = A.fold([h, shadow(h, created_at='2026-10-20T12:01:00Z')])[h['signal_id']]
    assert A.display(w, now='2026-10-20T12:31:01Z')['ai'][0]['stale'] is True     # 1h: capped at 30 min, not 2 h
    assert A.display(v)['ai'][0]['stale'] is None


def test_flag_off_is_the_stable_versioned_shape():
    assert A.api_off() == {'schema_version': 1, 'on': False, 'mode': 'off', 'updated_at': None, 'rows': []}


# ------------------------------------------------------------------ bounded queue, failures never block
def test_queue_is_bounded_and_a_refused_offer_becomes_an_insufficient_record():
    q = A.ScanQueue(2)
    assert [q.offer(i) for i in range(3)] == [True, True, False] and len(q) == 2
    s = sig(); f = failure(s, reason='queue_full')
    d = A.display(A.fold([s, f])[s['signal_id']])['ai'][0]
    assert (d['label'], d['reasons']) == (A.LABEL_INSUFFICIENT, ['queue_full']) and 'probability' not in d
    assert (q.take(), q.take(), q.take()) == (0, 1, None)


@pytest.mark.parametrize('bad', [0, -1, None, 2.5])
def test_an_unbounded_queue_refuses(bad):
    with pytest.raises(A.AIScanError, match='bounded'):
        A.ScanQueue(bad)


def test_offer_never_waits_on_a_held_queue():
    q = A.ScanQueue(5)
    got = []
    q._lock.acquire()
    try:                                        # offer from another thread, so a blocking regression fails, never hangs
        t0 = time.perf_counter()
        th = threading.Thread(target=lambda: got.append(q.offer('x')), daemon=True)
        th.start(); th.join(1.0)
        dt = time.perf_counter() - t0
    finally:
        q._lock.release()
    assert got == [False] and dt < 0.5


@pytest.mark.parametrize('reason', A.SHADOW_FAILURES)
def test_every_remote_failure_is_insufficient_never_a_score(reason):
    s = sig(); f = failure(s, reason=reason)
    assert f['status'] == 'INSUFFICIENT' and 'opinion' not in f and 'probability' not in f


def test_unknown_failure_and_a_late_result_for_a_failed_epoch_refuse():
    s = sig()
    with pytest.raises(A.AIScanError, match='unknown shadow failure'):
        failure(s, reason='slow')
    with pytest.raises(A.AIScanError, match='conflicting replay'):
        A.fold([s, failure(s), shadow(s)])


# ------------------------------------------------------------------ holdout isolation
HOLD = dict(start='2026-10-20T00:00:00Z', end='2026-10-21T00:00:00Z')


def test_fitting_refuses_a_sealed_holdout_row_and_never_filters_it():
    s = sig()
    with pytest.raises(A.AIScanError, match='sealed-holdout row'):
        A.fitting_view([s], HOLD)
    out = A.fitting_view([s], dict(start='2026-10-21T00:00:00Z', end='2026-10-22T00:00:00Z'))
    assert list(out) == [s['signal_id']]
    assert list(A.fitting_view([s], dict(start='2026-10-19T00:00:00Z', end=OPEN))) == [s['signal_id']]   # end exclusive


@pytest.mark.parametrize('h', [None, {}, dict(start=OPEN), dict(end=OPEN)])
def test_fitting_without_sealed_bounds_refuses(h):
    with pytest.raises(A.AIScanError, match='sealed holdout bounds'):
        A.fitting_view([sig()], h)


# ------------------------------------------------------------------ replay: idempotent, fail-closed joins
def test_restart_replay_of_identical_records_is_idempotent():
    s = sig(); r = shadow(s); o = outcome(s); m = sim(s)
    once = A.fold([s, r, o, m])
    assert A.fold([s, r, o, m, sig(), shadow(s), outcome(s), sim(s)]) == once


def test_conflicting_replay_of_an_immutable_record_refuses():
    s = sig()
    for later in ([sig(rules_reason='other')], [shadow(s), shadow(s, opinion='SHORT')],
                  [outcome(s), outcome(s, net_r=0.5)], [sim(s), sim(s, net_r=0.5)]):
        with pytest.raises(A.AIScanError, match='conflicting replay'):
            A.fold([s] + later)


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


# ------------------------------------------------------------------ measured (cost_rev) vs simulated outcomes
def test_an_untaken_candidate_gets_only_a_simulated_outcome():
    s = sig(status='skipped')
    with pytest.raises(A.AIScanError, match='measured outcome on a skipped'):
        outcome(s)
    v = A.fold([s, sim(s)])[s['signal_id']]
    assert v['measured'] is None and set(v['simulated']) == {H4}


@pytest.mark.parametrize('kw', [dict(source='measured', cost_basis='modelled'), dict(source='simulated', cost_basis='booked'),
                                dict(source='replayed', cost_basis='modelled'), dict(cost_rev=None), dict(cost_rev=-1),
                                dict(cost_rev=True), dict(cost_rev=1.0), dict(cost_model_hash=H4)])
def test_measured_outcomes_key_on_an_int_cost_rev_only(kw):
    with pytest.raises(A.AIScanError):
        outcome(sig(), **kw)


@pytest.mark.parametrize('kw', [dict(cost_model_hash=None), dict(cost_model_hash='v1'), dict(cost_rev=0)])
def test_simulated_outcomes_key_on_a_cost_model_hash_only(kw):
    with pytest.raises(A.AIScanError, match='simulated outcome'):
        sim(sig(), **kw)


def test_measured_and_simulated_outcomes_are_never_merged():
    s = sig()
    v = A.fold([s, outcome(s), sim(s, net_r=2.0), sim(s, cost_model_hash=H2, net_r=-1.0)])[s['signal_id']]
    d = A.display(v)['outcomes']
    assert d['measured']['net_r'] == 1.2 and {h: o['net_r'] for h, o in d['simulated'].items()} == {H2: -1.0, H4: 2.0}


def test_actual_cost_revision_supersedes_and_partial_exits_and_adds_survive():
    s = sig()
    o0 = outcome(s, partial_exits=2, adds=1)
    o1 = outcome(s, cost_rev=1, net_r=0.9, net_pnl=9.0, cost_basis='measured', partial_exits=2, adds=1)
    for order in ([s, o0, o1], [s, o1, o0]):
        v = A.fold(order)[s['signal_id']]
        m = v['measured']
        assert (m['cost_rev'], m['net_r'], m['partial_exits'], m['adds']) == (1, 0.9, 2, 1)
    assert A.display(v)['outcomes']['measured'] == dict(cost_rev=1, net_r=0.9, cost_basis='measured', exit_reason='tp1')


def test_several_models_are_kept_side_by_side():
    s = sig()
    v = A.fold([s, shadow(s), shadow(s, model_id='claude-opus-5-5', opinion='NO_TRADE')])[s['signal_id']]
    assert [x['model_id'] for x in A.display(v)['ai']] == ['claude-opus-5-5', MODEL]


def test_module_has_no_engine_or_network_imports():
    src = open(A.__file__, encoding='utf-8').read()
    for bad in ('import engine', 'binance_client', 'requests', 'urllib', 'shadow_analyst', 'app'):
        assert f'import {bad}' not in src and f'from {bad}' not in src
