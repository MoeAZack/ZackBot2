"""AI-SCAN-01 (#199/#200) schema contract: the STALE_BARS=2 timeframe-relative freshness ceiling with absolute ages +
ingest-time inputs, INSUFFICIENT inputs, no shadow authority, immutable model identity / calibration epochs and the
frozen gate, the stable {on:false, rows:[]} flag-off shape, the bounded non-blocking queue, holdout isolation, restart
idempotency, the (decision id, cost revision id) outcome join with rev for order only, measured vs simulated outcomes."""
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
COSTS0 = dict(fees_actual=0.5, funding_actual=0.0, pnl_gross=12.5, pnl_fill_adj=0.0, risk_usd=10.0)    # net 12.0, 1.2R
COSTS1 = dict(COSTS0, fees_actual=0.75, funding_actual=-2.75)   # a late-fee / funding restatement: net 9.0, 0.9R
CMID = 'actual-cost-model-v1'
CR0 = A.cost_revision_id(COSTS0, net_basis='booked', cost_model_id=CMID)
CR1 = A.cost_revision_id(COSTS1, net_basis='measured', cost_model_id=CMID)


def snap(**kw):
    s = dict(last_close=CUT, available_at='2026-10-20T12:15:03Z', features=dict(rsi=55.0, atr_pct=0.8, adx=None))
    s.update(kw)
    return s


def at(event_at, available_at):
    return dict(event_at=event_at, available_at=available_at)


def inputs(**kw):
    d = dict(funding=at('2026-10-20T12:00:00Z', '2026-10-20T12:00:05Z'),
             universe=at('2026-10-20T00:00:00Z', '2026-10-20T00:00:30Z'),
             exchange_rules=at('2026-10-20T06:00:00Z', '2026-10-20T06:00:02Z'))
    d.update(kw)
    return d


MAN = A.seal_manifest(fit=['2026-01-01T00:00:00Z', '2026-07-01T00:00:00Z'],
                      calibration=['2026-07-01T00:00:00Z', '2026-10-01T00:00:00Z'],
                      holdout=['2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z'], prereg_hash=H3)


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
             read_snapshot_hash=s['snapshot_hash'], read_plan_hash=s['plan_hash'], manifest=MAN)
    a.update(kw)
    return A.build_shadow(s, **a)


def failure(s, reason='timeout', **kw):
    a = dict(IDENT, created_at='2026-10-20T12:16:00Z', reason=reason, read_snapshot_hash=s['snapshot_hash'],
             read_plan_hash=s['plan_hash'])
    a.update(kw)
    return A.build_shadow_failure(s, **a)


def cal(**kw):
    c = dict(epoch=EPOCH, target=dict(A.TARGET), manifest_hash=MAN['manifest_hash'], window=list(MAN['calibration']),
             n=500, min_bin_n=50, ece=0.05, brier_improvement_ci_lo=0.001, windows=[dict(ece=0.04, beats_base=True)])
    c.update(kw)
    if 'id' not in kw:                                                   # the id is the record's own content hash
        try: c['id'] = A.calibration_id(c)
        except A.AIScanError: c['id'] = ''
    return c


def outcome(s, costs=COSTS0, **kw):
    a = dict(source='measured', actual_costs=costs, cost_model_id=CMID, rev=0, closed_at='2026-10-20T14:00:00Z',
             exit_reason='tp1', cost_basis='booked')
    a.update(kw)
    return A.build_outcome(s, **a)


def outcome1(s, **kw):
    return outcome(s, **dict(dict(costs=COSTS1, rev=1, cost_basis='measured'), **kw))


def sim(s, **kw):
    a = dict(source='simulated', cost_model_hash=H4, cost_basis='modelled', closed_at='2026-10-20T14:00:00Z', net_r=1.2,
             net_pnl=12.0, exit_reason='tp1')
    a.update(kw)
    return A.build_outcome(s, **a)


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


@pytest.mark.parametrize('lc, reason', [('2026-10-20T12:30:00Z', 'after_cutoff'), ('2026-10-20T11:44:59Z', 'stale_snapshot')])
def test_a_snapshot_after_the_cutoff_or_older_than_two_bars_is_insufficient(lc, reason):
    s = sig(snapshot=snap(last_close=lc))
    assert s['status'] == 'INSUFFICIENT' and reason in s['insufficient']
    with pytest.raises(A.AIScanError, match='INSUFFICIENT'):
        shadow(s)


@pytest.mark.parametrize('tf, open_, lc, age, ok', [
    ('15m', OPEN, CUT, 0, True),                                            # the exact cutoff candle
    ('15m', OPEN, '2026-10-20T11:45:00Z', 1800, True),                      # exactly 2 bars old = fresh
    ('15m', OPEN, '2026-10-20T11:44:59Z', 1801, False),                     # just over = INSUFFICIENT
    ('1h', '2026-10-20T11:00:00Z', '2026-10-20T10:00:00Z', 7200, True),     # timeframe-relative: 2 h on 1h
    ('1h', '2026-10-20T11:00:00Z', '2026-10-20T09:59:59Z', 7201, False),
    ('4h', '2026-10-20T08:00:00Z', '2026-10-20T04:00:00Z', 28800, True),    # 8 h on 4h (no 30-min cap)
    ('4h', '2026-10-20T08:00:00Z', '2026-10-20T03:59:59Z', 28801, False),
])
def test_stale_bars_is_a_timeframe_relative_ceiling_with_the_absolute_age_exposed(tf, open_, lc, age, ok):
    s = sig(tf=tf, candle_open=open_, snapshot=snap(last_close=lc, available_at='2026-10-20T12:15:05Z'),
            decided_at='2026-10-20T12:15:10Z')
    assert s['snapshot_age_s'] == age and A.stale_after_s(tf) == 2 * A.TF_SEC[tf]
    assert (s['status'] == 'OK') is ok and ('stale_snapshot' in s['insufficient']) is (not ok)
    assert A.display(A.fold([s])[s['signal_id']])['snapshot_age_s'] == age


@pytest.mark.parametrize('kw, reason', [
    (dict(snapshot=snap(available_at='2026-10-20T12:15:11Z')), 'after_decision:snapshot'),
    (dict(snapshot=snap(available_at='2026-10-20T12:14:59Z')), 'available_before_event:snapshot'),
    (dict(snapshot=snap(available_at=None)), 'missing:snapshot'),
    (dict(snapshot=snap(available_at='garbage')), 'missing:snapshot'),
    (dict(inputs=inputs(funding=at('2026-10-20T12:15:01Z', '2026-10-20T12:15:05Z'))), 'after_cutoff:funding'),
    (dict(inputs=inputs(funding=at('2026-10-20T12:00:00Z', '2026-10-20T12:15:11Z'))), 'after_decision:funding'),
    (dict(inputs=inputs(universe=at('2026-10-20T00:00:00Z', '2026-10-19T23:59:59Z'))), 'available_before_event:universe'),
    (dict(inputs=inputs(exchange_rules=at('2026-10-21T00:00:00Z', '2026-10-21T00:00:00Z'))), 'after_cutoff:exchange_rules'),
    (dict(inputs=inputs(universe=None)), 'missing:universe'),
    (dict(inputs=inputs(universe='2026-10-20T00:00:00Z')), 'missing:universe'),            # the old single timestamp
    (dict(inputs=inputs(funding=dict(event_at='2026-10-20T12:00:00Z'))), 'missing:funding'),
    (dict(inputs=inputs(funding=at('x', '2026-10-20T12:00:05Z'))), 'missing:funding'),
    (dict(inputs=None), 'missing:funding'),
])
def test_causal_inputs_need_event_le_cutoff_and_event_le_available_le_decision(kw, reason):
    s = sig(**kw)
    assert s['status'] == 'INSUFFICIENT' and reason in s['insufficient']
    with pytest.raises(A.AIScanError, match='INSUFFICIENT'):
        shadow(s)


def test_causal_input_boundaries_are_inclusive():
    s = sig(snapshot=snap(available_at=DEC), inputs=inputs(funding=at(CUT, DEC), universe=at(OPEN, OPEN)))
    assert s['status'] == 'OK' and s['inputs_as_of']['funding'] == at(CUT, DEC)
    assert sig(snapshot=snap(available_at=CUT))['status'] == 'OK'                    # available exactly at the close


def test_shadow_created_before_the_decision_refuses():
    with pytest.raises(A.AIScanError, match='before the decision'):
        shadow(sig(), created_at='2026-10-20T12:15:05Z')


def test_outcome_at_or_before_the_decision_refuses():
    with pytest.raises(A.AIScanError, match='at or before'):
        outcome(sig(), closed_at=DEC)


# ------------------------------------------------------------------ snapshot provenance (Codex 6455762348)
GOLDEN_SNAP = 'd4e11793383a1587b947435e1195f6eb83363855396b8e65f370dd9eab29bd73'   # independent: sha256(json {as_of:{available_at,event_at}, features_hash}) sorted keys


def test_snapshot_as_of_is_persisted_and_bound_into_the_snapshot_hash():
    s = sig()
    assert s['snapshot_as_of'] == dict(event_at=CUT, available_at='2026-10-20T12:15:03Z')
    assert s['snapshot_hash'] == A.snapshot_hash(snap()) == GOLDEN_SNAP
    assert s['features_hash'] == A.features_hash(snap()['features'])


@pytest.mark.parametrize('kw', [dict(available_at=None), dict(available_at='garbage')])
def test_a_snapshot_without_its_availability_time_is_insufficient(kw):
    s = sig(snapshot=snap(**kw))
    assert s['status'] == 'INSUFFICIENT' and 'missing:snapshot' in s['insufficient']
    assert s['snapshot_as_of'] is None and s['snapshot_hash'] is None
    with pytest.raises(A.AIScanError):
        A.snapshot_hash(snap(**kw))
    with pytest.raises(A.AIScanError, match='INSUFFICIENT'):
        shadow(s)


def test_changing_only_available_at_changes_the_identity_and_cannot_replay_as_the_same_snapshot():
    s = sig(); s2 = sig(snapshot=snap(available_at='2026-10-20T12:15:04Z'))
    assert s2['snapshot_hash'] != s['snapshot_hash'] and s2['record_hash'] != s['record_hash']
    with pytest.raises(A.AIScanError, match='conflicting replay'):
        A.fold([s, s2])
    with pytest.raises(A.AIScanError, match='different snapshot or plan'):
        shadow(s2, read_snapshot_hash=s['snapshot_hash'])
    with pytest.raises(A.AIScanError, match='different snapshot or plan'):
        A.fold([s2, A._seal(dict(shadow(s), signal_id=s2['signal_id']))])


def test_changing_only_the_event_time_changes_the_identity():
    a = sig(tf='1h', candle_open='2026-10-20T11:00:00Z', snapshot=snap(last_close='2026-10-20T12:00:00Z'))
    b = sig(tf='1h', candle_open='2026-10-20T11:00:00Z', snapshot=snap(last_close='2026-10-20T11:00:00Z'))
    assert a['features_hash'] == b['features_hash'] and a['snapshot_hash'] != b['snapshot_hash']


@pytest.mark.parametrize('field, value', [('available_at', '2026-10-20T12:15:04Z'), ('event_at', '2026-10-20T12:00:00Z'),
                                          ('available_at', None), ('event_at', 'x')])
def test_a_resealed_signal_with_tampered_snapshot_times_is_refused(field, value):
    s = sig()
    forged = A._seal(dict(s, snapshot_as_of=dict(s['snapshot_as_of'], **{field: value})))
    with pytest.raises(A.AIScanError, match='snapshot provenance does not match'):
        A.fold([forged])


def test_a_resealed_signal_that_drops_snapshot_as_of_or_breaks_causality_is_refused():
    s = sig()
    with pytest.raises(A.AIScanError, match='without snapshot_as_of'):
        A.fold([A._seal(dict(s, snapshot_as_of=None, features_hash=None, snapshot_hash=None))])
    with pytest.raises(A.AIScanError, match='snapshot provenance does not match'):
        A.fold([A._seal(dict(s, snapshot_as_of=None))])
    late = dict(event_at=CUT, available_at='2026-10-20T12:15:11Z')        # available after decided_at, hash re-derived
    forged = dict(s, snapshot_as_of=late, snapshot_hash=A.provenance_hash(late, s['features_hash']))
    with pytest.raises(A.AIScanError, match='not causal'):
        A.fold([A._seal(forged)])


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


@pytest.mark.parametrize('where', ['features', 'plan', 'costs', 'nested_costs', 'nested_list', 'inputs'])
@pytest.mark.parametrize('key', ['api_key', 'orderId', 'client_oid', 'trade_id', 'secret'])
def test_credentials_and_exchange_ids_are_refused_at_any_depth(where, key):
    kw = {'features': dict(snapshot=snap(features={key: 1.0})), 'plan': dict(plan=dict(plan(), **{key: 1})),
          'costs': dict(costs={key: 1}), 'nested_costs': dict(costs=dict(meta=dict(src={key: 1}))),
          'nested_list': dict(costs=dict(legs=[dict(px=1.0), {key: 1}])),
          'inputs': dict(inputs=inputs(funding=dict(at(OPEN, DEC), meta={key: 'x'})))}[where]
    with pytest.raises(A.AIScanError, match='credential or exchange identifier'):
        sig(**kw)


@pytest.mark.parametrize('sampling', [dict(SAMP, leverage=3), dict(SAMP, api_key='x'), dict(SAMP, meta=dict(order_id=1)),
                                      dict(SAMP, temperature=dict(stop=1)), dict(SAMP, stop_sequences=[dict(size=2)]),
                                      dict(SAMP, top_p='0.9'), dict(SAMP, order_size=1)])
def test_sampling_is_a_flat_allow_list_so_nothing_hides_inside_it(sampling):
    with pytest.raises(A.AIScanError, match='sampling'):
        shadow(sig(), sampling=sampling)
    with pytest.raises(A.AIScanError, match='sampling'):
        failure(sig(), sampling=sampling)
    assert shadow(sig(), sampling=dict(SAMP, top_p=0.9, stop_sequences=['</x>'], seed=7))['status'] == 'OK'


@pytest.mark.parametrize('extra', [dict(meta=dict(nested=dict(api_key='x'))), dict(meta=[dict(stop=1)])])
def test_nested_extras_are_refused(extra):
    with pytest.raises(A.AIScanError):
        shadow(sig(), **extra)


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
    c = cal()
    r = shadow(s, probability=0.62, calibration=c)
    assert r['probability'] == dict(state=A.PROB_CALIBRATED, p=0.62, calibration_id=c['id'], target=A.TARGET)
    d = A.display(A.fold([s, r])[s['signal_id']])
    assert d['ai'][0]['probability'] == dict(value='62%', target=A.TARGET)
    assert d['ai'][0]['label'] == 'AI SHADOW' and d['rules']['label'] == 'RULES'


@pytest.mark.parametrize('kw', [dict(n=499), dict(min_bin_n=49), dict(ece=0.0501), dict(brier_improvement_ci_lo=0.0),
                                dict(id=''), dict(id='cal-2026-10'), dict(id='0' * 64), dict(epoch='other'),
                                dict(disjoint=True, manifest_hash=H1), dict(manifest_hash=None),
                                dict(window=['2026-07-01T00:00:00Z', '2026-10-02T00:00:00Z']),        # runs into holdout
                                dict(window=['2026-06-30T00:00:00Z', '2026-10-01T00:00:00Z']),        # runs into fit
                                dict(window=['2026-10-01T00:00:00Z', '2026-07-01T00:00:00Z']),        # reversed
                                dict(window=None),
                                dict(target=dict(A.TARGET, horizon='fixed_16_bars')), dict(n=float('nan')),
                                dict(windows=[dict(ece=0.081, beats_base=True)])])
def test_a_calibration_missing_any_gate_bar_stays_unverified(kw):
    s = sig()
    r = shadow(s, probability=0.71, calibration=cal(**kw))
    assert r['probability'] == dict(state=A.PROB_UNVERIFIED)
    assert A.display(A.fold([s, r])[s['signal_id']])['ai'][0]['probability'] == 'UNVERIFIED'


def test_a_calibration_needs_the_sealed_manifest_it_names():
    c = cal()
    assert shadow(sig(), probability=0.6, calibration=c)['probability']['state'] == A.PROB_CALIBRATED
    edited = dict(MAN, calibration=['2026-06-01T00:00:00Z', '2026-10-01T00:00:00Z'])   # an edit without re-sealing
    other = A.seal_manifest(fit=MAN['fit'], calibration=MAN['calibration'], holdout=MAN['holdout'], prereg_hash=H4)
    for m in (None, {}, edited, other, dict(MAN, manifest_hash='0' * 64)):
        assert shadow(sig(), probability=0.6, calibration=c, manifest=m)['probability']['state'] == A.PROB_UNVERIFIED


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
def test_a_shadow_older_than_two_bars_of_its_timeframe_shows_insufficient_with_its_age():
    s = sig(); v = A.fold([s, shadow(s)])[s['signal_id']]          # created 12:16:00; 15m: 2 bars = 1800 s
    r = A.display(v, now='2026-10-20T12:46:00Z')['ai'][0]
    assert r['age_s'] == 1800 and r['label'] == A.LABEL_AI and r['opinion'] == 'LONG'
    r = A.display(v, now='2026-10-20T12:46:01Z')['ai'][0]
    assert r['age_s'] == 1801 and r['label'] == A.LABEL_INSUFFICIENT and r['reasons'] == ['stale'] and 'opinion' not in r
    f = sig(tf='4h', candle_open='2026-10-20T08:00:00Z', snapshot=snap(last_close='2026-10-20T12:00:00Z',
                                                                        available_at='2026-10-20T12:00:03Z'),
            decided_at='2026-10-20T12:00:10Z')
    x = A.fold([f, shadow(f, created_at='2026-10-20T12:01:00Z')])[f['signal_id']]
    assert A.display(x, now='2026-10-20T20:01:00Z')['ai'][0]['label'] == A.LABEL_AI           # 4h: 8 h is fresh
    assert A.display(x, now='2026-10-20T20:01:01Z')['ai'][0]['label'] == A.LABEL_INSUFFICIENT
    assert A.display(v)['ai'][0]['age_s'] is None and A.display(v)['ai'][0]['label'] == A.LABEL_AI


def test_a_future_dated_shadow_result_is_insufficient_at_display_time():
    s = sig(); v = A.fold([s, shadow(s)])[s['signal_id']]                  # created 12:16:00
    r = A.display(v, now='2026-10-20T12:15:59Z')['ai'][0]
    assert r['label'] == A.LABEL_INSUFFICIENT and r['reasons'] == ['future_dated'] and 'opinion' not in r
    assert A.display(v, now='2026-10-20T12:16:00Z')['ai'][0]['label'] == A.LABEL_AI         # age 0 is fine


def test_flag_off_is_exactly_on_false_rows_empty_and_stable():
    a, b = A.api_off(), A.api_off()
    assert a == {'on': False, 'rows': []} and list(a) == ['on', 'rows'] and a['on'] is False
    a['rows'].append('x')                                        # a caller mutating its copy never leaks into the next
    assert b == {'on': False, 'rows': []} and A.api_off() == {'on': False, 'rows': []}


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
def at_open(o):
    end = A._iso(A._ms(o) + 900_000)
    return sig(candle_open=o, decided_at=end, snapshot=snap(last_close=end, available_at=end),
               inputs=inputs(funding=at(o, end), universe=at(o, end), exchange_rules=at(o, end)))


def test_fitting_refuses_a_calibration_or_holdout_row_and_never_filters_it():
    s = sig()                                                              # 2026-10-20: inside the sealed holdout
    with pytest.raises(A.AIScanError, match='sealed-holdout row'):
        A.fitting_view([s], MAN)
    with pytest.raises(A.AIScanError, match='sealed-calibration row'):
        A.fitting_view([at_open('2026-08-01T00:00:00Z')], MAN)
    f, edge = at_open('2026-03-01T00:00:00Z'), at_open('2026-06-30T23:45:00Z')   # last fit candle (end exclusive)
    assert set(A.fitting_view([f, edge], MAN)) == {f['signal_id'], edge['signal_id']}
    with pytest.raises(A.AIScanError, match='sealed-calibration row'):
        A.fitting_view([at_open('2026-07-01T00:00:00Z')], MAN)              # first calibration candle


def test_fitting_without_a_sealed_manifest_refuses():
    edited = dict(MAN, holdout=['2026-10-21T00:00:00Z', '2026-11-01T00:00:00Z'])   # moved without re-sealing
    for m in (None, {}, edited, dict(MAN, manifest_hash='0' * 64), dict(MAN, schema='v0')):
        with pytest.raises(A.AIScanError, match='manifest'):
            A.fitting_view([sig()], m)


@pytest.mark.parametrize('kw, msg', [
    (dict(holdout=['2026-11-01T00:00:00Z', '2026-10-01T00:00:00Z']), 'start must be before end'),      # reversed
    (dict(holdout=['2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z']), 'start must be before end'),      # empty
    (dict(calibration=['2026-06-01T00:00:00Z', '2026-10-01T00:00:00Z']), 'ordered'),                   # overlaps fit
    (dict(holdout=['2026-09-01T00:00:00Z', '2026-11-01T00:00:00Z']), 'ordered'),                       # overlaps calib
    (dict(fit=['2026-10-01T00:00:00Z', '2026-12-01T00:00:00Z'], holdout=['2026-01-01T00:00:00Z', '2026-07-01T00:00:00Z']),
     'ordered'),                                                                                        # out of order
    (dict(holdout=None), 'needs'), (dict(prereg_hash='x'), 'preregistration'),
])
def test_a_reversed_overlapping_or_unbound_manifest_refuses(kw, msg):
    a = dict(fit=MAN['fit'], calibration=MAN['calibration'], holdout=MAN['holdout'], prereg_hash=H3)
    a.update(kw)
    with pytest.raises(A.AIScanError, match=msg):
        A.seal_manifest(**a)
    forged = dict(MAN, **{k: v for k, v in kw.items() if k in A.WINDOWS})
    forged['manifest_hash'] = A.sha256_hex(A.canonical_json(A._manifest_body(forged)))   # re-hashed by hand
    if 'prereg_hash' not in kw:
        with pytest.raises(A.AIScanError):
            A.verify_manifest(forged)


# ------------------------------------------------------------------ replay: idempotent, fail-closed joins
def test_restart_replay_of_identical_records_is_idempotent():
    s = sig(); r = shadow(s); o = outcome(s); m = sim(s)
    once = A.fold([s, r, o, m])
    assert A.fold([s, r, o, m, sig(), shadow(s), outcome(s), sim(s)]) == once


def test_conflicting_replay_of_an_immutable_record_refuses():
    s = sig()
    for later in ([sig(rules_reason='other')], [shadow(s), shadow(s, opinion='SHORT')],
                  [outcome(s), outcome(s, exit_reason='sl')], [sim(s), sim(s, net_r=0.5)]):
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


# ------------------------------------------------------------------ measured (decision id + cost revision id) vs simulated
def test_an_untaken_candidate_gets_only_a_simulated_outcome():
    s = sig(status='skipped')
    with pytest.raises(A.AIScanError, match='measured outcome on a skipped'):
        outcome(s)
    v = A.fold([s, sim(s)])[s['signal_id']]
    assert v['measured'] is None and set(v['simulated']) == {H4}


@pytest.mark.parametrize('kw', [dict(cost_basis='modelled'), dict(source='replayed'), dict(rev=None), dict(rev=-1),
                                dict(rev=True), dict(rev=1.0), dict(cost_model_hash=H4), dict(costs=None),
                                dict(cost_model_id=''), dict(cost_model_id=None), dict(costs=dict(COSTS0, risk_usd=0.0)),
                                dict(costs=dict(COSTS0, risk_usd=-10.0)), dict(costs=dict(COSTS0, risk_usd=float('inf'))),
                                dict(costs={k: v for k, v in COSTS0.items() if k != 'funding_actual'}),
                                dict(costs=dict(COSTS0, pnl_gross='12.5'))])
def test_measured_outcomes_need_the_actual_cost_payload_and_an_int_rev(kw):
    with pytest.raises(A.AIScanError):
        outcome(sig(), **kw)


@pytest.mark.parametrize('kw, msg', [
    (dict(cost_revision_id='0' * 64, net_pnl=999.0, net_r=99.9), 'cost_revision_id does not match'),   # Codex's probe
    (dict(cost_revision_id='0' * 64), 'cost_revision_id does not match'), (dict(cost_revision_id=CR1), 'does not match'),
    (dict(net_pnl=999.0), 'net_pnl / net_r do not match'), (dict(net_r=1.21), 'net_pnl / net_r do not match'),
    (dict(net_pnl=float('nan')), 'do not match'),
])
def test_a_supplied_cost_id_or_net_that_differs_from_the_payload_is_refused(kw, msg):
    with pytest.raises(A.AIScanError, match=msg):
        outcome(sig(), **kw)
    o = outcome(sig(), cost_revision_id=CR0, net_pnl=12.0, net_r=1.2)            # matching values are accepted
    assert (o['cost_revision_id'], o['net_pnl'], o['net_r']) == (CR0, 12.0, 1.2)


def test_the_measured_net_formula_and_funding_sign_are_pinned():
    # actual_costs.measured: net = pnl_gross + pnl_fill_adj - fees_actual + funding_actual (funding + = received)
    c = dict(pnl_gross=100.0, pnl_fill_adj=-2.0, fees_actual=3.0, funding_actual=-1.5, risk_usd=20.0)
    assert A.measured_net(c) == (93.5, 93.5 / 20.0)
    assert A.measured_net(dict(c, funding_actual=1.5)) == (96.5, 96.5 / 20.0)
    o = outcome(sig(), costs=c)
    assert (o['net_pnl'], o['net_r'], o['actual_costs']) == (93.5, 4.675, c)


def test_replay_refuses_a_resealed_record_whose_cost_identity_was_forged():
    s = sig(); o = outcome(s)
    for forged in (dict(o, net_pnl=999.0), dict(o, net_r=99.0), dict(o, cost_revision_id='0' * 64),
                   dict(o, actual_costs=dict(COSTS0, fees_actual=0.0)), dict(o, cost_model_id='other'),
                   dict(o, cost_basis='measured'), dict(o, actual_costs=None)):
        with pytest.raises(A.AIScanError, match='forged cost identity'):
            A.fold([s, A._seal(forged)])


@pytest.mark.parametrize('status', ['skipped', 'pending'])
def test_a_measured_outcome_is_valid_only_for_a_taken_decision(status):
    k = sig(status=status)
    with pytest.raises(A.AIScanError, match=f'measured outcome on a {status}'):
        outcome(k)
    with pytest.raises(A.AIScanError, match=f'measured outcome on a {status}'):
        A.fold([k, A._seal(dict(outcome(sig()), signal_id=k['signal_id']))])
    assert set(A.fold([k, sim(k)])[k['signal_id']]['simulated']) == {H4}


@pytest.mark.parametrize('kw', [dict(cost_model_hash=None), dict(cost_model_hash='v1'), dict(rev=0),
                                dict(cost_revision_id=CR0), dict(actual_costs=COSTS0), dict(cost_model_id=CMID)])
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
    o1 = outcome1(s, partial_exits=2, adds=1)
    for order in ([s, o0, o1], [s, o1, o0]):
        v = A.fold(order)[s['signal_id']]
        m = v['measured']
        assert (m['cost_revision_id'], m['rev'], m['net_r'], m['partial_exits'], m['adds']) == (CR1, 1, 0.9, 2, 1)
    assert A.display(v)['outcomes']['measured'] == dict(cost_revision_id=CR1, rev=1, net_r=0.9, cost_basis='measured',
                                                        exit_reason='tp1')


def test_two_revisions_of_one_decision_each_join_to_their_own_outcome():
    s = sig()
    o0, o1 = outcome(s), outcome1(s)
    v = A.fold([s, o0, o1])[s['signal_id']]
    assert set(v['revisions']) == {CR0, CR1}
    assert (v['revisions'][CR0]['net_r'], v['revisions'][CR1]['net_r']) == (1.2, 0.9)
    assert all(r['signal_id'] == s['signal_id'] for r in v['revisions'].values())


def test_rev_reordering_never_changes_the_economic_join():
    s = sig()
    a = [outcome(s), outcome1(s)]
    b = [outcome(s, rev=7), outcome1(s, rev=3)]
    c = [outcome(s, rev=5), outcome1(s, rev=2)]
    joins = []
    for recs in (a, b, b[::-1], c):
        v = A.fold([s] + recs)[s['signal_id']]
        joins.append({cid: (r['net_r'], r['net_pnl']) for cid, r in v['revisions'].items()})
    assert all(j == {CR0: (1.2, 12.0), CR1: (0.9, 9.0)} for j in joins)      # renumbered revs: same join every time
    assert A.fold([s] + c)[s['signal_id']]['measured']['cost_revision_id'] == CR0   # rev decides ORDER only


def test_rev_deduplicates_and_a_reused_rev_or_renumbered_identity_refuses():
    s = sig(); o0 = outcome(s)
    assert A.fold([s, o0, outcome(s)]) == A.fold([s, o0])                     # same id + rev: restart no-op
    with pytest.raises(A.AIScanError, match='share rev 0'):
        A.fold([s, o0, outcome1(s, rev=0)])
    with pytest.raises(A.AIScanError, match='conflicting replay'):
        A.fold([s, o0, outcome(s, rev=1)])                                    # one economic identity, two revs


GOLDEN_CR0 = '16d0a15a8d3b87fae92d66abe2194fb160afff06d949d651e7de9352764c2c70'   # independent: sha256(json [schema, pairs, basis, model])


def test_cost_revision_id_schema_and_golden_hash_are_pinned():
    assert A.COST_REV_SCHEMA == 'ai-scan-cost-rev-v2'
    assert A.COST_REV_FIELDS == ('fees_actual', 'funding_actual', 'pnl_gross', 'pnl_fill_adj', 'risk_usd')
    assert CR0 == GOLDEN_CR0


def test_cost_revision_id_ignores_input_order_timestamps_and_presentation_fields():
    rev = dict(reversed(list(COSTS0.items())))
    noisy = dict(COSTS0, closed_at='2026-10-20T14:00:00Z', ts=1760968800, cost_rev=3, label='TP1 hit', pnl_str='+$12.00',
                 id='row-7', pnl_net=1e9, net_r=1e9)
    ints = dict(COSTS0, funding_actual=0, pnl_fill_adj=0, risk_usd=10)            # 0 and 0.0 are one value
    for d in (rev, noisy, ints):
        assert A.cost_revision_id(d, net_basis='booked', cost_model_id=CMID) == CR0


@pytest.mark.parametrize('change', [dict(fees_actual=0.51), dict(funding_actual=-0.01), dict(pnl_gross=12.51),
                                    dict(pnl_fill_adj=0.1), dict(risk_usd=10.01),
                                    dict(net_basis='measured'), dict(cost_model_id='actual-cost-model-v2')])
def test_any_cost_field_net_basis_or_cost_model_changes_the_id(change):
    nb, cm = change.pop('net_basis', 'booked'), change.pop('cost_model_id', CMID)
    assert A.cost_revision_id(dict(COSTS0, **change), net_basis=nb, cost_model_id=cm) != CR0


def test_a_schema_version_bump_changes_the_id(monkeypatch):
    monkeypatch.setattr(A, 'COST_REV_SCHEMA', 'ai-scan-cost-rev-v3')
    assert A.cost_revision_id(COSTS0, net_basis='booked', cost_model_id=CMID) != CR0


@pytest.mark.parametrize('costs, kw', [(None, {}), ([1], {}), ({k: v for k, v in COSTS0.items() if k != 'funding_actual'}, {}),
                                       (dict(COSTS0, funding_actual=None), {}), (dict(COSTS0, risk_usd=float('nan')), {}),
                                       (dict(COSTS0, risk_usd=0), {}),
                                       (dict(COSTS0, fees_actual='0.42'), {}), (dict(COSTS0, fees_actual=True), {}),
                                       (COSTS0, dict(net_basis='modelled')), (COSTS0, dict(cost_model_id=''))])
def test_a_missing_or_partial_cost_has_no_identity(costs, kw):
    a = dict(dict(net_basis='booked', cost_model_id=CMID), **kw)
    with pytest.raises(A.AIScanError):
        A.cost_revision_id(costs, **a)


def test_an_id_collision_between_two_different_revisions_fails_closed():
    s = sig()
    other = outcome(s, rev=1, exit_reason='sl', partial_exits=1)     # a different record with the identical id CR0
    assert other['cost_revision_id'] == CR0
    with pytest.raises(A.AIScanError, match='conflicting replay'):
        A.fold([s, outcome(s), other])
    with pytest.raises(A.AIScanError, match='conflicting replay'):
        A.fold([s, other, outcome(s)])


def test_several_models_are_kept_side_by_side():
    s = sig()
    v = A.fold([s, shadow(s), shadow(s, model_id='claude-opus-5-5', opinion='NO_TRADE')])[s['signal_id']]
    assert [x['model_id'] for x in A.display(v)['ai']] == ['claude-opus-5-5', MODEL]


def test_module_has_no_engine_or_network_imports():
    src = open(A.__file__, encoding='utf-8').read()
    for bad in ('import engine', 'binance_client', 'requests', 'urllib', 'shadow_analyst', 'app'):
        assert f'import {bad}' not in src and f'from {bad}' not in src
