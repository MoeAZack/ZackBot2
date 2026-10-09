"""PR #50 round 2 (Codex exact-head review of a9a47cb): regressions for the two P1 and two P2 findings, each failing on
a9a47cb, plus Claude's own adversarial probes of the validator (type confusion, boundary times, cross-field
contradictions, redaction bypass)."""
import copy

import pytest

from contract_schema import errors, load
from newcore.contracts import signal_v1 as S
from newcore.domain.errors import InvalidRecord
from test_signal_contract_schema import (ID, T0, accepts_eval, candidate, evaluation, level, mutate, result,
                                         schema_errors, signal)

NOW = T0 + 1_000
SECRET = 'hunter2xyz'


def sealed():
    return evaluation('live_forward', sealed=True)


def reseal(ev, cands):
    ev['prior_commitment']['commitment_sha256'] = S.commitment_digest(ev, cands)
    return ev, cands


# ------------------------------------------------- P1-1: the serializer emits only an allowlisted, fully checked result
@pytest.mark.parametrize('extra', [{'secret_dump': SECRET}, {'detail_raw': SECRET}, {'Detail': SECRET},
                                   {'': SECRET}, {'decision_id ': SECRET}])
def test_p1_encode_result_refuses_unknown_fields_and_never_echoes_them(extra):
    p = result('rejected')
    p.update(extra)
    with pytest.raises(InvalidRecord) as ei:
        S.encode_result(p)
    assert SECRET not in str(ei.value)


@pytest.mark.parametrize('name,fn', [
    ('signal_id free text', lambda p: p.update(signal_id=SECRET)),
    ('result_id free text', lambda p: p.update(result_id=SECRET)),
    ('decision_id free text', lambda p: p.update(decision_id=SECRET)),
    ('original_result_id free text', lambda p: p.update(original_result_id=SECRET)),
    ('status unknown', lambda p: p.update(status=SECRET)),
    ('contract_version true', lambda p: p.update(contract_version=True)),
    ('contract_version 2', lambda p: p.update(contract_version=2)),
    ('retryable 0', lambda p: p.update(retryable=0)),
    ('detail as list', lambda p: p.update(detail=[SECRET])),
    ('reason_code as dict', lambda p: p.update(reason_code={'a': SECRET})),
])
def test_p1_encode_result_does_not_rely_on_the_schema_for_any_field(name, fn):
    with pytest.raises(InvalidRecord) as ei:
        S.encode_result(mutate(result('duplicate'), fn))
    assert SECRET not in str(ei.value)


def test_p1_encoded_bytes_are_the_allowlisted_fields_only():
    p = result('duplicate')
    p['detail'] = 'replay of the original'
    assert set(S.parse_strict(S.encode_result(p))) <= S.RESULT_KEYS


@pytest.mark.parametrize('build,path', [
    (lambda: signal(), ()), (lambda: signal(), ('order',)), (lambda: signal(), ('risk',)),
    (lambda: signal(), ('stop',)), (lambda: signal(), ('stop', 'distance')),
    (lambda: candidate(), ()), (lambda: candidate(), ('scores',)), (lambda: candidate(), ('structural_stop',)),
])
def test_p1_semantic_layer_refuses_unknown_fields_at_every_level(build, path):
    p = build()
    node = p
    for k in path:
        node = node[k]
    node['x_secret'] = SECRET
    check = S.check_signal_intent if 'signal_id' in p else S.check_candidate_score
    with pytest.raises(InvalidRecord):
        check(p, now_ms=NOW)


@pytest.mark.parametrize('path', [(), ('universe',), ('universe', 'members', 0), ('scoring',), ('scoring', 'weights'),
                                  ('exclusions', 0), ('benchmarks', 0), ('prior_commitment',)])
def test_p1_evaluation_refuses_unknown_fields_at_every_level(path):
    ev, cands = sealed()
    node = ev
    for k in path:
        node = node[k]
    node['x_secret'] = SECRET
    with pytest.raises(InvalidRecord):
        S.check_candidate_evaluation(ev, cands)


def test_p1_python_allowlists_match_the_schemas():
    """Drift guard: the semantic allowlists are exactly the schema property sets."""
    si, sr, sc, se = (load(f'{k}_v1.schema.json') for k in ('signal_intent', 'signal_result', 'candidate_score',
                                                           'candidate_evaluation'))
    d = si['$defs']
    assert S.INTENT_KEYS == set(si['properties'])
    assert S.ORDER_KEYS == set(d['order']['properties']) and S.RISK_KEYS == set(d['risk']['properties'])
    assert S.LEVEL_KEYS == set(d['level']['properties']) and S.VALUE_KEYS == set(d['value']['properties'])
    assert S.TRAIL_KEYS == set(d['trail']['properties'])
    assert S.RESULT_KEYS == set(sr['properties'])
    assert S.CANDIDATE_KEYS == set(sc['properties']) and S.STOP_KEYS == set(sc['properties']['structural_stop']['properties'])
    assert set(S.SCORE_DIMENSIONS) == set(sc['properties']['scores']['properties'])
    p = se['properties']
    assert S.EVALUATION_KEYS == set(p) and S.UNIVERSE_KEYS == set(p['universe']['properties'])
    assert S.MEMBER_KEYS == set(p['universe']['properties']['members']['items']['properties'])
    assert S.SCORING_KEYS == set(p['scoring']['properties'])
    assert S.EXCLUSION_KEYS == set(p['exclusions']['items']['properties'])
    assert S.BENCHMARK_KEYS == set(p['benchmarks']['items']['properties'])
    assert S.PRIOR_KEYS == set(p['prior_commitment']['properties'])


# ------------------------------------------------------------------ P1-2: the commitment covers the evaluated window
@pytest.mark.parametrize('field,delta', [('window_start_ms', 1), ('window_start_ms', -500), ('window_end_ms', 1),
                                         ('window_end_ms', -86_000_000)])
def test_p1_window_bounds_cannot_move_after_anchoring(field, delta):
    ev, cands = sealed()
    ev['prior_commitment'][field] += delta
    assert errors(load('candidate_evaluation_v1.schema.json'), ev) == []
    with pytest.raises(InvalidRecord, match='commitment_sha256'):
        S.check_candidate_evaluation(ev, cands)


def test_p1_sealed_flag_is_committed_and_anchor_metadata_is_not():
    ev, cands = sealed()
    ev['prior_commitment'].update(anchor_ref='tsa:other/xyz', anchored_at_ms=T0 + 2_000)   # anchor facts come after
    S.check_candidate_evaluation(ev, cands)
    assert S.COMMITTED_PRIOR_FIELDS == {'window_start_ms', 'window_end_ms', 'benchmark_set_sha256'}


# ------------------------------------------------------------ P2-1: freshness is mandatory at promotion / use
def test_p2_signal_freshness_needs_the_trusted_clock():
    with pytest.raises(TypeError):
        S.check_signal_intent(signal())                           # no silent "fresh" without a clock
    S.check_signal_shape(signal())                                # explicit, non-promoting structural check
    with pytest.raises(InvalidRecord, match='expired'):
        S.check_signal_intent(signal(), now_ms=T0 + 60_000)


def test_p2_candidate_freshness_needs_the_trusted_clock():
    c = candidate()
    S.check_candidate_score(c, now_ms=NOW)
    with pytest.raises(TypeError):
        S.check_candidate_score(c)
    for now in (T0 + 900_000, T0 + 900_001, T0 - S.CLOCK_SKEW_MS - 1):
        with pytest.raises(InvalidRecord):
            S.check_candidate_score(c, now_ms=now)
    S.check_candidate_score(c, now_ms=T0 + 899_999)
    S.check_candidate_score(c, now_ms=T0 - S.CLOCK_SKEW_MS)
    with pytest.raises(InvalidRecord):
        S.check_candidate_score(c, now_ms=True)


def test_p2_candidate_must_still_be_valid_when_the_sealed_window_opens():
    ev, cands = sealed()
    cands[0]['expires_at_ms'] = ev['prior_commitment']['window_start_ms']          # expires exactly as it opens
    with pytest.raises(InvalidRecord, match='window'):
        S.check_candidate_evaluation(*reseal(ev, cands))
    ev, cands = sealed()
    cands[0]['expires_at_ms'] = ev['prior_commitment']['window_start_ms'] + 1
    S.check_candidate_evaluation(*reseal(ev, cands))


# ------------------------------------------------------------------------ P2-2: unique evidence cells
def _eval_with(fn):
    ev, cands = evaluation()
    fn(ev, cands)
    return ev, cands


@pytest.mark.parametrize('name,fn', [
    ('duplicate exclusion row inflates counts',
     lambda e, c: (e['exclusions'].append(dict(e['exclusions'][0])), e['exclusion_counts'].update({'exclusion.stale_data': 2}))),
    ('same cell excluded twice for two reasons',
     lambda e, c: (e['exclusions'].append({'symbol': 'ETHUSDT', 'reason_code': 'exclusion.liquidity_floor'}),
                   e['exclusion_counts'].update({'exclusion.liquidity_floor': 1}))),
    ('symbol-wide exclusion overlaps a strategy exclusion',
     lambda e, c: (e['exclusions'].append({'symbol': 'ETHUSDT', 'strategy_id': 'ny_pullback', 'side': 'SHORT',
                                           'reason_code': 'exclusion.liquidity_floor'}),
                   e['exclusion_counts'].update({'exclusion.liquidity_floor': 1}))),
    ('a scored cell is also excluded',
     lambda e, c: (e['exclusions'].append({'symbol': 'BTCUSDT', 'strategy_id': 'ny_pullback',
                                           'reason_code': 'exclusion.liquidity_floor'}),
                   e['exclusion_counts'].update({'exclusion.liquidity_floor': 1}))),
])
def test_p2_duplicate_or_overlapping_exclusions_refused(name, fn):
    with pytest.raises(InvalidRecord):
        S.check_candidate_evaluation(*_eval_with(fn))


def test_p2_duplicate_candidate_cell_under_another_id_refused():
    ev, cands = evaluation()
    cands[1]['side'] = 'LONG'                                     # b already LONG: same strategy/version/symbol/side as a
    assert cands[0]['side'] == cands[1]['side']
    with pytest.raises(InvalidRecord, match='cell'):
        S.check_candidate_evaluation(ev, cands)


def test_p2_distinct_cells_on_one_symbol_are_valid():
    ev, cands = evaluation()
    cands[1]['side'] = 'SHORT'
    accepts_eval(ev, cands)
    ev, cands = evaluation()
    cands[1]['strategy_version'] = '1.1.0'
    ev['exclusions'].append({'symbol': 'BTCUSDT', 'strategy_id': 'range_scalp', 'reason_code': 'exclusion.liquidity_floor'})
    ev['exclusion_counts']['exclusion.liquidity_floor'] = 1
    accepts_eval(ev, cands)


# ---------------------------------------------------------------- self-attack: type confusion
@pytest.mark.parametrize('name,fn', [
    ('dry_run as 0', lambda p: p.update(dry_run=0)),
    ('contract_version true', lambda p: p.update(contract_version=True)),
    ('action list', lambda p: p.update(action=['enter'])),
    ('order as list', lambda p: p.update(order=[{'type': 'market'}])),
    ('decimal as number', lambda p: p['risk'].update(risk_percent=1)),
    ('decimal as bool', lambda p: p['risk'].update(risk_percent=True)),
    ('decimal with spaces', lambda p: p['risk'].update(risk_percent=' 1')),
    ('decimal NaN text', lambda p: p['risk'].update(risk_percent='NaN')),
    ('decimal Infinity text', lambda p: p['risk'].update(risk_percent='Infinity')),
    ('decimal exponent text', lambda p: p['risk'].update(risk_percent='1E1')),
    ('decimal unicode digits', lambda p: p['risk'].update(risk_percent='١')),
    ('decimal underscore', lambda p: p['risk'].update(risk_percent='1_0')),
    ('lookback bool', lambda p: p.update(stop=level('recent_high_low', 'ticks', '5', True))),
    ('lookback float', lambda p: p.update(stop=level('recent_high_low', 'ticks', '5', 20.0))),
    ('unit unknown', lambda p: p.update(stop={'method': 'fixed', 'distance': {'unit': 'bps', 'value': '5'}})),
])
def test_self_type_confusion_in_signals_is_refused_without_the_schema(name, fn):
    with pytest.raises(InvalidRecord):
        S.check_signal_intent(mutate(signal(), fn), now_ms=NOW)


@pytest.mark.parametrize('name,fn', [
    ('score bool', lambda p: p['scores'].update(opportunity=True)),
    ('score float', lambda p: p['scores'].update(opportunity=50.0)),
    ('reason codes as string', lambda p: p.update(reason_codes='stand_down.regime_veto')),
    ('reason codes duplicated', lambda p: p.update(reason_codes=['candidate.profile_ready'] * 2)),
    ('evidence class list', lambda p: p.update(evidence_class=['live_forward'])),
    ('entry state unknown', lambda p: p.update(entry_state='GO')),
    ('side unknown', lambda p: p.update(side='BOTH')),
    ('hash uppercase', lambda p: p.update(weights_sha256='A' * 64)),
])
def test_self_type_confusion_in_candidates_is_refused_without_the_schema(name, fn):
    with pytest.raises(InvalidRecord):
        S.check_candidate_score(mutate(candidate(), fn), now_ms=NOW)


def test_self_stand_down_reason_as_string_cannot_satisfy_the_veto_rule():
    """A string reason_codes iterated per character must never satisfy 'names a veto'."""
    p = mutate(candidate('STAND_DOWN'), lambda q: q.update(reason_codes='stand_down.regime_veto'))
    with pytest.raises(InvalidRecord):
        S.check_candidate_score(p, now_ms=NOW)


# ---------------------------------------------------------------- self-attack: boundary times
def test_self_ttl_and_expiry_boundaries_are_exact():
    S.check_signal_intent(mutate(signal(), lambda p: p.update(expires_at_ms=T0 + S.MAX_SIGNAL_TTL_MS)), now_ms=NOW)
    order = {'type': 'limit_post_only', 'price': {'unit': 'price', 'value': '1'}}
    S.check_signal_intent(mutate(signal(), lambda p: p.update(order=dict(order, expires_at_ms=T0 + 60_000))), now_ms=NOW)
    with pytest.raises(InvalidRecord):
        S.check_signal_intent(mutate(signal(), lambda p: p.update(order=dict(order, expires_at_ms=T0))), now_ms=NOW)
    with pytest.raises(InvalidRecord):                            # order already expired at intake
        S.check_signal_intent(mutate(signal(), lambda p: p.update(order=dict(order, expires_at_ms=T0 + 500))),
                              now_ms=NOW)
    c = mutate(candidate(), lambda p: p.update(expires_at_ms=T0 + S.MAX_CANDIDATE_TTL_MS))
    S.check_candidate_score(c, now_ms=NOW)
    for now in (S.MIN_TS_MS - 1, S.MAX_TS_MS + 1, float(NOW)):
        with pytest.raises(InvalidRecord):
            S.check_signal_intent(signal(), now_ms=now)


def test_self_proof_of_prior_boundaries():
    ev, cands = sealed()
    ev['prior_commitment']['anchored_at_ms'] = T0                 # anchored at the scoring instant: allowed
    S.check_candidate_evaluation(ev, cands)
    ev['prior_commitment']['anchored_at_ms'] = ev['prior_commitment']['window_start_ms']
    with pytest.raises(InvalidRecord):
        S.check_candidate_evaluation(ev, cands)


# ---------------------------------------------------------------- self-attack: cross-field contradictions
@pytest.mark.parametrize('name,fn', [
    ('validated without dry run evidence of a decision', lambda p: p.update(decision_id=None)),
    ('accepted with original status', lambda p: p.update(original_status='accepted')),
])
def test_self_result_contradictions(name, fn):
    for status in ('accepted', 'validated'):
        with pytest.raises(InvalidRecord):
            S.check_signal_result(mutate(result(status), fn))


def test_self_evaluation_contradictions():
    ev, cands = evaluation()
    ev['evidence_class'] = 'backtest'
    for c in cands:
        c['evidence_class'] = 'backtest'
    S.check_candidate_evaluation(ev, cands)                       # a plain backtest evaluation is fine
    ev['sealed'] = True
    with pytest.raises(InvalidRecord):
        S.check_candidate_evaluation(ev, cands)
    ev, cands = evaluation()
    with pytest.raises(InvalidRecord):
        S.check_candidate_evaluation(ev, cands + [copy.deepcopy(cands[0])])     # same record twice
    with pytest.raises(InvalidRecord):
        S.check_candidate_evaluation(ev, {c['candidate_id']: c for c in cands})  # not a list


# ---------------------------------------------------------------- self-attack: redaction bypass
@pytest.mark.parametrize('detail', [
    f'AUTHORIZATION {SECRET}', f'auth: {SECRET}', f'pwd {SECRET}', f'key={SECRET}', f'sig:{SECRET}',
    f'https://api.example/x?k={SECRET}', f'credential {SECRET}', f'jwt {SECRET}', f'x-mbx-apikey {SECRET}',
    f'B-e-a-r-e-r {SECRET}x' * 1, f'{SECRET}&{SECRET}',
])
def test_self_redaction_bypass_attempts_are_refused(detail):
    p = mutate(result('rejected'), lambda q: q.update(detail=detail))
    with pytest.raises(InvalidRecord) as ei:
        S.encode_result(p)
    assert SECRET not in str(ei.value)


@pytest.mark.parametrize('detail', ['signal expired 1200 ms before intake', 'stop 1.5 atr below entry, BTCUSDT LONG',
                                    'hold: reconciliation pending (2 lots)'])
def test_self_ordinary_detail_still_passes(detail):
    S.encode_result(mutate(result('rejected'), lambda q: q.update(detail=detail)))


def test_self_schema_and_semantic_agree_on_the_new_fixtures():
    ev, cands = sealed()
    assert schema_errors('candidate_evaluation', ev) == []
    assert all(schema_errors('candidate_score', c) == [] for c in cands)
    S.check_candidate_evaluation(ev, cands)


# ---------------------------------------------------------------- self-attack round 2: echo, commitment scope, bounds
def test_self_unknown_field_names_and_container_values_are_never_echoed():
    p = result('rejected')
    p[SECRET] = 1
    with pytest.raises(InvalidRecord) as ei:
        S.encode_result(p)
    assert SECRET not in str(ei.value)
    c = mutate(candidate(), lambda q: q.update(reason_codes=[[SECRET]], evidence_class={'k': SECRET}))
    for bad in (c, mutate(candidate(), lambda q: q.update(reason_codes=[[SECRET]]))):
        with pytest.raises(InvalidRecord) as ei:
            S.check_candidate_score(bad, now_ms=NOW)
        assert SECRET not in str(ei.value)


def test_self_commitment_covers_exclusions_and_candidate_records():
    ev, cands = sealed()
    ev['exclusions'][0]['reason_code'] = 'exclusion.liquidity_floor'
    ev['exclusion_counts'] = {'exclusion.liquidity_floor': 1, 'exclusion.delisted': 1}
    with pytest.raises(InvalidRecord, match='commitment_sha256'):
        S.check_candidate_evaluation(ev, cands)
    ev, cands = sealed()
    cands[1]['entry_state'] = 'SETUP'
    with pytest.raises(InvalidRecord, match='commitment_sha256'):
        S.check_candidate_evaluation(ev, cands)


def test_self_committed_window_is_bounded():
    ev, cands = sealed()
    ev['prior_commitment']['window_end_ms'] = ev['prior_commitment']['window_start_ms'] + S.MAX_WINDOW_MS + 1
    with pytest.raises(InvalidRecord, match='window longer'):
        S.check_candidate_evaluation(*reseal(ev, cands))
    ev, cands = sealed()
    ev['prior_commitment']['window_end_ms'] = ev['prior_commitment']['window_start_ms'] + S.MAX_WINDOW_MS
    S.check_candidate_evaluation(*reseal(ev, cands))


@pytest.mark.parametrize('veto', ['stand_down.regime_veto', 'risk_gateway.btc_breaker'])
def test_self_ready_cannot_carry_a_veto_reason(veto):
    with pytest.raises(InvalidRecord, match='READY'):
        S.check_candidate_score(mutate(candidate(), lambda q: q.update(reason_codes=[veto])), now_ms=NOW)
    S.check_candidate_score(mutate(candidate('WAIT'), lambda q: q.update(reason_codes=[veto])), now_ms=NOW)


def test_self_symbols_with_digits_pass_the_detail_boundary():
    S.encode_result(mutate(result('rejected'), lambda q: q.update(detail='1000PEPEUSDT stale for 90 s')))


def test_self_deeply_nested_payload_is_refused_by_the_parser():
    with pytest.raises(InvalidRecord):
        S.parse_strict(b'{"a":' + b'[' * 100_000 + b']' * 100_000 + b'}')
