"""SIGNAL-01 / SCORE-01 contract hardening (PR #50, Codex triage items 1-7): adversarial payloads, not prose.

Every negative case names the layer that must refuse it: 'schema' (the JSON Schema in contracts/) or 'semantic' (the
schema accepts it and newcore.contracts.signal_v1 refuses it). Pinning the layer proves each rule lives where the
contract says it does. Positive cases pass both layers. When jsonschema happens to be installed, every payload is
cross-checked against it too.
"""
import ast
import copy
import glob
import os

import pytest

from contract_schema import ROOT, errors, load
from newcore.contracts import signal_v1 as S
from newcore.domain.errors import InvalidRecord

SCHEMAS = {k: load(f'{k}_v1.schema.json') for k in ('signal_intent', 'signal_result', 'candidate_score',
                                                    'candidate_evaluation')}
T0 = 1_791_000_000_000                   # 2026-10 UTC ms
SEMANTIC = {'signal_intent': lambda p, now_ms=T0 + 1_000: S.check_signal_intent(p, now_ms=now_ms),
            'signal_result': S.check_signal_result,
            'candidate_score': lambda p, now_ms=T0 + 1_000: S.check_candidate_score(p, now_ms=now_ms)}
H = lambda c: c * 64                     # noqa: E731  a non-zero digest
ID = lambda p, c='a': f'{p}_' + c * 32   # noqa: E731


def schema_errors(kind, payload):
    errs = errors(SCHEMAS[kind], payload)
    try:
        import jsonschema
    except ImportError:
        return errs
    ok = jsonschema.Draft202012Validator(SCHEMAS[kind]).is_valid(payload)
    assert ok == (not errs), (kind, errs)
    return errs


def accepts(kind, payload, **kw):
    assert schema_errors(kind, payload) == []
    if kind in SEMANTIC:
        SEMANTIC[kind](payload, **kw)


def refuses(kind, payload, layer, **kw):
    errs = schema_errors(kind, payload)
    if layer == 'schema':
        assert errs, 'the schema accepted a payload it must refuse'
        return
    assert errs == [], f'expected a semantic refusal but the schema already refuses: {errs}'
    with pytest.raises(InvalidRecord):
        SEMANTIC[kind](payload, **kw)


def mutate(base, fn):
    p = copy.deepcopy(base)
    fn(p)
    return p


# ----------------------------------------------------------------------------------------------------------- payloads
def level(method='fixed', unit='percent', value='1.5', lookback=None):
    d = {'method': method, 'distance': {'unit': unit, 'value': value}}
    if lookback is not None:
        d['lookback_bars'] = lookback
    return d


def signal(action='enter'):
    p = {'contract_version': 1, 'signal_id': ID('sig'), 'source': 'tradingview', 'account': 'main', 'action': action,
         'symbol': 'BTCUSDT', 'strategy_id': 'ny_pullback', 'strategy_version': '1.0.0',
         'generated_at_ms': T0, 'expires_at_ms': T0 + 60_000, 'dry_run': False}
    if action == 'enter':
        p.update(side='LONG', order={'type': 'market'}, risk={'risk_percent': '0.5'}, stop=level(),
                 trade_family_id=ID('fam'))
    elif action == 'reduce':
        p.update(target_ref=ID('pos'), reduce_percent='50')
    elif action == 'close':
        p.update(target_ref=ID('pos'))
    else:
        p.update(target_ref=ID('pos'), stop=level('atr', 'atr', '2', 14))
    return p


def result(status='accepted'):
    p = {'contract_version': 1, 'result_id': ID('res'), 'signal_id': ID('sig'), 'status': status,
         'retryable': False, 'recorded_at_ms': T0}
    p['reason_code'] = {'accepted': 'ingress.accepted', 'validated': 'ingress.dry_run_validated',
                        'rejected': 'ingress.expired', 'held': 'ingress.ownership_unresolved',
                        'duplicate': 'ingress.duplicate_replay'}[status]
    if status in ('accepted', 'validated'):
        p['decision_id'] = ID('dec')
    if status == 'duplicate':
        p.update(original_result_id=ID('res', 'b'), original_status='accepted', decision_id=ID('dec'))
    return p


WEIGHTS = {'opportunity': '0.3', 'entry_quality': '0.2', 'hold_quality': '0.1', 'regime_fit': '0.2',
           'evidence_readiness': '0.1', 'operational_readiness': '0.1'}
UNIVERSE = {'universe_id': 'binance_usdm_top', 'as_of_ms': T0 - 3_600_000,
            'members': [{'symbol': 'BTCUSDT', 'status': 'listed'}, {'symbol': 'ETHUSDT', 'status': 'listed'},
                        {'symbol': 'LUNAUSDT', 'status': 'delisted'}]}
SCORING = {'scoring_version': 'score-1', 'weights': WEIGHTS}
BENCH = [{'benchmark_id': 'cash', 'kind': 'flat_cash'}, {'benchmark_id': 'rnd', 'kind': 'random_entry_same_exits'}]


def candidate(state='READY', symbol='BTCUSDT', cid='a', evidence='testnet'):
    return {'contract_version': 1, 'candidate_id': ID('cand', cid), 'evidence_class': evidence, 'as_of_ms': T0,
            'expires_at_ms': T0 + 900_000, 'universe_id': UNIVERSE['universe_id'],
            'universe_snapshot_sha256': S.sha256_of(UNIVERSE), 'universe_as_of_ms': UNIVERSE['as_of_ms'],
            'scoring_version': 'score-1', 'weights_sha256': S.sha256_of(SCORING), 'profile_id': 'balanced',
            'profile_version': '3', 'automation_ceiling': 'testnet', 'strategy_id': 'ny_pullback',
            'strategy_version': '1.0.0', 'symbol': symbol, 'side': 'LONG', 'entry_state': state,
            'scores': {k: 50 for k in S.SCORE_DIMENSIONS}, 'reason_codes': ['candidate.profile_ready'],
            'source_manifest_sha256': H('c'), 'structural_stop': {'unit': 'atr', 'value': '1.5'}}


def evaluation(evidence='testnet', sealed=False):
    cands = [candidate(cid='a', evidence=evidence), candidate('WAIT', cid='b', evidence=evidence)]
    cands[1]['reason_codes'] = ['candidate.no_entry_trigger']
    cands[1]['side'] = 'SHORT'                                            # one candidate per evaluation cell
    ev = {'contract_version': 1, 'evaluation_id': ID('eval'), 'evidence_class': evidence, 'as_of_ms': T0,
          'universe': copy.deepcopy(UNIVERSE), 'universe_snapshot_sha256': S.sha256_of(UNIVERSE),
          'scoring': copy.deepcopy(SCORING), 'weights_sha256': S.sha256_of(SCORING), 'profile_id': 'balanced',
          'profile_version': '3', 'candidate_ids': [c['candidate_id'] for c in cands],
          'exclusions': [{'symbol': 'ETHUSDT', 'reason_code': 'exclusion.stale_data'},
                         {'symbol': 'LUNAUSDT', 'reason_code': 'exclusion.delisted'}],
          'exclusion_counts': {'exclusion.stale_data': 1, 'exclusion.delisted': 1},
          'benchmarks': copy.deepcopy(BENCH), 'benchmark_set_sha256': S.sha256_of(BENCH), 'sealed': sealed,
          'prior_commitment': None}
    if sealed:
        ev['prior_commitment'] = {'commitment_sha256': H('f'), 'anchor_kind': 'rfc3161_timestamp',
                                  'anchor_ref': 'tsa:freetsa.org/abc123', 'anchored_at_ms': T0 + 1_000,
                                  'window_start_ms': T0 + 60_000, 'window_end_ms': T0 + 86_400_000,
                                  'benchmark_set_sha256': S.sha256_of(BENCH)}
        ev['prior_commitment']['commitment_sha256'] = S.commitment_digest(ev, cands)
    return ev, cands


def accepts_eval(ev, cands):
    assert schema_errors('candidate_evaluation', ev) == []
    for c in cands:
        assert schema_errors('candidate_score', c) == []
    S.check_candidate_evaluation(ev, cands)


def refuses_eval(ev, cands, layer):
    errs = schema_errors('candidate_evaluation', ev)
    if layer == 'schema':
        assert errs
        return
    assert errs == [], errs
    with pytest.raises(InvalidRecord):
        S.check_candidate_evaluation(ev, cands)


# ------------------------------------------------- item 1: candidate evidence class, universe snapshot, scoring version
def test_item1_valid_candidate_passes_both_layers():
    accepts('candidate_score', candidate())


@pytest.mark.parametrize('field', ['evidence_class', 'universe_snapshot_sha256', 'universe_as_of_ms', 'scoring_version',
                                   'weights_sha256', 'profile_id', 'profile_version', 'automation_ceiling'])
def test_item1_new_identity_fields_are_required(field):
    refuses('candidate_score', mutate(candidate(), lambda p: p.pop(field)), 'schema')


@pytest.mark.parametrize('name,fn,layer', [
    ('evidence class free text', lambda p: p.update(evidence_class='live'), 'schema'),
    ('universe id only, hash free text', lambda p: p.update(universe_snapshot_sha256='current_top100'), 'schema'),
    ('all-zero universe hash', lambda p: p.update(universe_snapshot_sha256='0' * 64), 'schema'),
    ('all-zero manifest hash', lambda p: p.update(source_manifest_sha256='0' * 64), 'schema'),
    ('universe snapshot after as-of', lambda p: p.update(universe_as_of_ms=T0 + 1), 'semantic'),
    ('expiry before as-of', lambda p: p.update(expires_at_ms=T0 - 1), 'semantic'),
    ('expiry equal to as-of', lambda p: p.update(expires_at_ms=T0), 'semantic'),
    ('TTL above 24 h', lambda p: p.update(expires_at_ms=T0 + S.MAX_CANDIDATE_TTL_MS + 1), 'semantic'),
    ('year-2099 expiry', lambda p: p.update(expires_at_ms=4102444799999), 'semantic'),
    ('backtest evidence feeding auto mainnet', lambda p: p.update(evidence_class='backtest',
                                                                 automation_ceiling='auto_mainnet'), 'semantic'),
    ('paper evidence feeding auto mainnet', lambda p: p.update(evidence_class='paper',
                                                              automation_ceiling='auto_mainnet'), 'semantic'),
    ('unregistered reason zz.zz', lambda p: p.update(reason_codes=['zz.zz']), 'semantic'),
    ('exclusion code as candidate reason', lambda p: p.update(reason_codes=['exclusion.delisted']), 'semantic'),
    ('structural stop 99999 percent', lambda p: p.update(structural_stop={'unit': 'percent', 'value': '99999'}),
     'semantic'),
    ('score as float token', lambda p: p['scores'].update(opportunity=50.5), 'schema'),
])
def test_item1_candidate_negative_payloads(name, fn, layer):
    refuses('candidate_score', mutate(candidate(), fn), layer)


def test_ruling_no_fixed_ready_floor_and_immature_evidence_is_not_a_no_trade_gate():
    """READY with zero evidence / operational readiness is valid (READY is profile-computed server-side); backtest
    evidence only lowers the automation ceiling - observe, dry-run and testnet stay open."""
    for ceiling in ('observe', 'dry_run', 'testnet'):
        p = candidate()
        p['scores'].update(evidence_readiness=0, operational_readiness=0)
        p.update(evidence_class='backtest', automation_ceiling=ceiling)
        accepts('candidate_score', p)
    live = candidate()
    live.update(evidence_class='live_forward', automation_ceiling='auto_mainnet')
    accepts('candidate_score', live)


def test_ruling_stand_down_with_high_score_is_valid_only_with_a_veto_reason():
    p = candidate('STAND_DOWN')
    p['scores'].update(opportunity=100, entry_quality=100)
    for veto in ('stand_down.regime_veto', 'stand_down.event_window', 'risk_gateway.btc_breaker'):
        accepts('candidate_score', mutate(p, lambda q: q.update(reason_codes=[veto])))
    refuses('candidate_score', mutate(p, lambda q: q.update(reason_codes=[])), 'schema')
    refuses('candidate_score', mutate(p, lambda q: q.update(reason_codes=['candidate.extended'])), 'semantic')


# ---------------------------------------------------------------- item 2: evaluation / exclusion / benchmark artifact
def test_item2_valid_evaluations_pass():
    accepts_eval(*evaluation())
    accepts_eval(*evaluation('live_forward', sealed=True))
    accepts_eval(*evaluation('backtest'))


def _resealed(fn, evidence='testnet'):
    ev, cands = evaluation(evidence)
    fn(ev, cands)
    return ev, cands


@pytest.mark.parametrize('name,fn,layer', [
    ('member silently dropped from coverage',
     lambda e, c: (e['exclusions'].pop(0), e['exclusion_counts'].pop('exclusion.stale_data')), 'semantic'),
    ('exclusion counts understate', lambda e, c: e['exclusion_counts'].update({'exclusion.stale_data': 2}), 'semantic'),
    ('exclusion count for a code never used', lambda e, c: e['exclusion_counts'].update({'exclusion.halted': 1}),
     'semantic'),
    ('exclusion with unregistered reason', lambda e, c: (e['exclusions'][0].update(reason_code='zz.zz'),
                                                         e['exclusion_counts'].pop('exclusion.stale_data'),
                                                         e['exclusion_counts'].update({'zz.zz': 1})), 'semantic'),
    ('exclusion of a non-member', lambda e, c: (e['exclusions'].append({'symbol': 'XRPUSDT',
                                                                         'reason_code': 'exclusion.stale_data'}),
                                                e['exclusion_counts'].update({'exclusion.stale_data': 2})), 'semantic'),
    ('delisted member not excluded as delisted',
     lambda e, c: (e['exclusions'][1].update(reason_code='exclusion.stale_data'),
                   e['exclusion_counts'].update({'exclusion.stale_data': 2}),
                   e['exclusion_counts'].pop('exclusion.delisted')), 'semantic'),
    ('universe tampered after hashing', lambda e, c: e['universe']['members'].pop(), 'semantic'),
    ('unsorted / duplicated members', lambda e, c: e['universe']['members'].reverse(), 'semantic'),
    ('weights changed after hashing', lambda e, c: e['scoring']['weights'].update(opportunity='0.4',
                                                                                 entry_quality='0.1'), 'semantic'),
    ('weights do not sum to 1', lambda e, c: e['scoring']['weights'].update(opportunity='0.9'), 'semantic'),
    ('benchmark swapped after hashing', lambda e, c: e['benchmarks'][1].update(kind='buy_and_hold'), 'semantic'),
    ('duplicate benchmark id', lambda e, c: e['benchmarks'].append(dict(e['benchmarks'][0])), 'semantic'),
    ('candidate missing from the id list', lambda e, c: e['candidate_ids'].pop(), 'semantic'),
    ('candidate from another weights version', lambda e, c: c[0].update(weights_sha256=H('e')), 'semantic'),
    ('candidate from another universe snapshot', lambda e, c: c[0].update(universe_snapshot_sha256=H('e')), 'semantic'),
    ('candidate of another evidence class', lambda e, c: c[0].update(evidence_class='paper'), 'semantic'),
    ('candidate on a delisted member', lambda e, c: c[0].update(symbol='LUNAUSDT'), 'semantic'),
    ('backtest marked sealed', lambda e, c: e.update(evidence_class='backtest', sealed=True), 'schema'),
    ('sealed without commitment', lambda e, c: e.update(sealed=True), 'schema'),
    ('universe hash all zero', lambda e, c: e.update(universe_snapshot_sha256='0' * 64), 'schema'),
])
def test_item2_evaluation_negative_payloads(name, fn, layer):
    refuses_eval(*_resealed(fn), layer)


# ---------------------------------------------------------------------------------- item 7: proof-of-prior anchoring
def _sealed(fn):
    ev, cands = evaluation('live_forward', sealed=True)
    fn(ev, cands)
    return ev, cands


@pytest.mark.parametrize('name,fn,layer', [
    ('anchored after the window opened', lambda e, c: e['prior_commitment'].update(anchored_at_ms=T0 + 60_000),
     'semantic'),
    ('anchored after the window closed', lambda e, c: e['prior_commitment'].update(anchored_at_ms=T0 + 90_000_000),
     'semantic'),
    ('anchored before the candidates existed', lambda e, c: e['prior_commitment'].update(anchored_at_ms=T0 - 1),
     'semantic'),
    ('empty window', lambda e, c: e['prior_commitment'].update(window_end_ms=T0 + 60_000), 'semantic'),
    ('benchmarks chosen after the commitment', lambda e, c: e['prior_commitment'].update(benchmark_set_sha256=H('d')),
     'semantic'),
    ('candidate edited after commitment', lambda e, c: c[0]['scores'].update(opportunity=99), 'semantic'),
    ('commitment digest arbitrary', lambda e, c: e['prior_commitment'].update(commitment_sha256=H('9')), 'semantic'),
    ('self-hosted editable anchor kind', lambda e, c: e['prior_commitment'].update(anchor_kind='local_file'), 'schema'),
    ('commitment on a backtest', lambda e, c: e.update(evidence_class='backtest', sealed=False), 'schema'),
])
def test_item7_proof_of_prior_negative_payloads(name, fn, layer):
    refuses_eval(*_sealed(fn), layer)


def test_item7_backtest_commitment_refused_even_if_schema_branch_is_bypassed():
    ev, cands = evaluation('live_forward', sealed=True)
    ev['evidence_class'] = 'backtest'
    for c in cands:
        c.update(evidence_class='backtest')
    with pytest.raises(InvalidRecord, match='never evidence for a backtest'):
        S.check_candidate_evaluation(ev, cands)


# ------------------------------------------------------------------- item 3: signal discriminators, targets, numerics
@pytest.mark.parametrize('action', ['enter', 'reduce', 'close', 'modify'])
def test_item3_each_action_has_a_valid_form(action):
    accepts('signal_intent', signal(action))


@pytest.mark.parametrize('otype', ['limit_post_only', 'stop_market'])
def test_item3_priced_order_types_are_valid_with_a_price(otype):
    p = signal()
    p['order'] = {'type': otype, 'price': {'unit': 'price', 'value': '64250.5'}, 'expires_at_ms': T0 + 30_000}
    accepts('signal_intent', p)


def _o(**kw):
    return lambda p: p.update(order=kw)


@pytest.mark.parametrize('name,action,fn,layer', [
    ('limit without price', 'enter', _o(type='limit_post_only'), 'schema'),
    ('stop_market without price', 'enter', _o(type='stop_market'), 'schema'),
    ('market with price', 'enter', _o(type='market', price={'unit': 'price', 'value': '1'}), 'schema'),
    ('market with expiry', 'enter', _o(type='market', expires_at_ms=T0 + 1), 'schema'),
    ('null price smuggled', 'enter', _o(type='limit_post_only', price=None), 'schema'),
    ('enter with target_ref', 'enter', lambda p: p.update(target_ref=ID('pos')), 'schema'),
    ('enter without stop', 'enter', lambda p: p.pop('stop'), 'schema'),
    ('close with side', 'close', lambda p: p.update(side='LONG'), 'schema'),
    ('close with order', 'close', lambda p: p.update(order={'type': 'market'}), 'schema'),
    ('close with risk', 'close', lambda p: p.update(risk={'risk_percent': '1'}), 'schema'),
    ('close with stop', 'close', lambda p: p.update(stop=level()), 'schema'),
    ('close with trade family', 'close', lambda p: p.update(trade_family_id=ID('fam')), 'schema'),
    ('close targeting an intent', 'close', lambda p: p.update(target_ref=ID('int')), 'schema'),
    ('reduce without amount', 'reduce', lambda p: p.pop('reduce_percent'), 'schema'),
    ('reduce with stop', 'reduce', lambda p: p.update(stop=level()), 'schema'),
    ('modify with nothing to modify', 'modify', lambda p: p.pop('stop'), 'schema'),
    ('modify with order', 'modify', lambda p: p.update(order={'type': 'market'}), 'schema'),
    ('target_ref all', 'close', lambda p: p.update(target_ref='all'), 'schema'),
    ('target_ref free path', 'close', lambda p: p.update(target_ref='pos/../all'), 'schema'),
    ('target_ref short hex', 'close', lambda p: p.update(target_ref='pos_abc'), 'schema'),
    ('50-digit decimal', 'enter', lambda p: p['risk'].update(risk_percent='1' * 50), 'schema'),
    ('leading-zero decimal', 'enter', lambda p: p['risk'].update(risk_percent='00.1'), 'schema'),
    ('zero risk', 'enter', lambda p: p['risk'].update(risk_percent='0.000'), 'schema'),
    ('exponent decimal', 'enter', lambda p: p['risk'].update(risk_percent='1e2'), 'schema'),
    ('risk_percent 999999999', 'enter', lambda p: p['risk'].update(risk_percent='999999999'), 'semantic'),
    ('max_risk_usd 1e12', 'enter', lambda p: p['risk'].update(max_risk_usd='1000000000000'), 'semantic'),
    ('stop 99999 percent', 'enter', lambda p: p.update(stop=level(value='99999')), 'semantic'),
    ('fractional ticks', 'enter', lambda p: p.update(stop=level(unit='ticks', value='1.5')), 'semantic'),
    ('reduce 150 percent', 'reduce', lambda p: p.update(reduce_percent='150'), 'semantic'),
    ('unknown field', 'enter', lambda p: p.update(environment='live'), 'schema'),
])
def test_item3_signal_negative_payloads(name, action, fn, layer):
    refuses('signal_intent', mutate(signal(action), fn), layer)


# ----------------------------------------------------- item 4: result per-status branches, duplicate reference, detail
@pytest.mark.parametrize('status', ['validated', 'accepted', 'rejected', 'held', 'duplicate'])
def test_item4_each_status_has_a_valid_form(status):
    accepts('signal_result', result(status))


@pytest.mark.parametrize('name,status,fn,layer', [
    ('accepted retryable', 'accepted', lambda p: p.update(retryable=True), 'schema'),
    ('accepted without decision', 'accepted', lambda p: p.pop('decision_id'), 'schema'),
    ('accepted with null decision', 'accepted', lambda p: p.update(decision_id=None), 'schema'),
    ('accepted citing an original', 'accepted', lambda p: p.update(original_result_id=ID('res', 'b')), 'schema'),
    ('validated retryable', 'validated', lambda p: p.update(retryable=True), 'schema'),
    ('held retryable', 'held', lambda p: p.update(retryable=True), 'schema'),
    ('rejected citing an original', 'rejected', lambda p: p.update(original_result_id=ID('res', 'b')), 'schema'),
    ('duplicate without original', 'duplicate', lambda p: p.pop('original_result_id'), 'schema'),
    ('duplicate without original status', 'duplicate', lambda p: p.pop('original_status'), 'schema'),
    ('duplicate of a duplicate', 'duplicate', lambda p: p.update(original_status='duplicate'), 'schema'),
    ('duplicate retryable', 'duplicate', lambda p: p.update(retryable=True), 'schema'),
    ('duplicate citing itself', 'duplicate', lambda p: p.update(original_result_id=p['result_id']), 'semantic'),
    ('missing result id', 'rejected', lambda p: p.pop('result_id'), 'schema'),
    ('unregistered reason', 'rejected', lambda p: p.update(reason_code='zz.zz'), 'semantic'),
    ('accept code on a rejection', 'rejected', lambda p: p.update(reason_code='ingress.accepted'), 'semantic'),
    ('gate code on an acceptance', 'accepted', lambda p: p.update(reason_code='filter.hours'), 'semantic'),
    ('deprecated gate code', 'rejected', lambda p: p.update(reason_code='filter.ai_veto'), 'semantic'),
    ('non-gate domain code', 'rejected', lambda p: p.update(reason_code='exit.stop'), 'semantic'),
    ('detail with newline', 'rejected', lambda p: p.update(detail='a\nb'), 'schema'),
    ('detail too long', 'rejected', lambda p: p.update(detail='x' * 161), 'schema'),
    ('detail auth header', 'rejected', lambda p: p.update(detail='Authorization: Bearer abc123'), 'semantic'),
    ('detail api key word', 'rejected', lambda p: p.update(detail='api_key=xyz'), 'semantic'),
    ('detail key-shaped token', 'rejected', lambda p: p.update(detail='key ' + 'Ab9_' * 8), 'semantic'),
    ('detail blank', 'rejected', lambda p: p.update(detail='   '), 'semantic'),
    ('timestamp as float', 'rejected', lambda p: p.update(recorded_at_ms=float(T0) + 0.5), 'schema'),
], ids=lambda v: v.replace(' ', '_') if isinstance(v, str) and ' ' in v else None)
def test_item4_result_negative_payloads(name, status, fn, layer):
    refuses('signal_result', mutate(result(status), fn), layer)


def test_item4_gate_codes_are_valid_for_rejections_and_holds():
    accepts('signal_result', mutate(result('rejected'), lambda p: p.update(reason_code='filter.hours', retryable=True)))
    accepts('signal_result', mutate(result('held'), lambda p: p.update(reason_code='risk_gateway.state_untrusted')))


# ------------------------------------------------------ ruling: serialization goes through the redaction boundary
SECRET = 'Zq7RkP2mW9xL4vN8'


SECRET_DETAILS = [f'Authorization: Bearer {SECRET}', f'token={SECRET}', f'x {SECRET}{SECRET}', f'password {SECRET}']


@pytest.mark.parametrize('detail', SECRET_DETAILS, ids=[f'probe-{i}' for i in range(len(SECRET_DETAILS))])
def test_ruling_encode_result_refuses_secret_detail_without_echoing_it(detail):
    p = result('rejected')
    p['detail'] = detail
    with pytest.raises(InvalidRecord) as ei:
        S.encode_result(p)
    assert SECRET not in str(ei.value) and detail not in str(ei.value)


def test_ruling_encode_result_emits_canonical_bytes_after_validation():
    p = result('rejected')
    p['detail'] = 'signal expired 1200 ms before intake'
    out = S.encode_result(p)
    assert out == S.canonical_bytes(p) and out.startswith(b'{"contract_version":1,')
    assert S.parse_strict(out) == p
    with pytest.raises(InvalidRecord):
        S.encode_result(mutate(p, lambda q: q.update(reason_code='zz.zz')))       # nothing invalid is ever serialized


# ---------------------------------------------- item 5: semantic validator - tokens, time, hashes, units, registry
def test_item5_strict_integer_token_type():
    """JSON Schema "integer" accepts 1700000000000.0; parse_strict refuses every binary float token."""
    p = signal()
    raw = S.canonical_bytes(p).replace(f'"generated_at_ms":{T0}'.encode(), f'"generated_at_ms":{T0}.0'.encode())
    loose = __import__('json').loads(raw)
    assert schema_errors('signal_intent', loose) == []                         # the schema gap is real
    with pytest.raises(InvalidRecord, match='float'):
        S.parse_strict(raw)
    for bad in (b'{"a":1e3}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1,"a":2}', b'[1]', b'\xff', b'{"a":',
                b'{"a":"' + b'x' * S.MAX_PAYLOAD_BYTES + b'"}'):
        with pytest.raises(InvalidRecord):
            S.parse_strict(bad)
    assert S.parse_strict(S.canonical_bytes(p)) == p


def test_item5_semantic_rejects_bool_and_float_where_parse_was_skipped():
    for v in (True, float(T0)):
        with pytest.raises(InvalidRecord):
            S.check_signal_intent(mutate(signal(), lambda p: p.update(generated_at_ms=v)), now_ms=T0 + 1_000)


@pytest.mark.parametrize('name,action,fn', [
    ('expiry before generation', 'enter', lambda p: p.update(expires_at_ms=T0 - 1)),
    ('expiry equal to generation', 'close', lambda p: p.update(expires_at_ms=T0)),
    ('TTL above 1 h', 'enter', lambda p: p.update(expires_at_ms=T0 + S.MAX_SIGNAL_TTL_MS + 1)),
    ('year-2099 expiry', 'close', lambda p: p.update(expires_at_ms=4102444799999)),
    ('order expiry after the signal', 'enter', _o(type='limit_post_only', price={'unit': 'price', 'value': '1'},
                                                  expires_at_ms=T0 + 60_001)),
    ('order expiry in year 2000', 'enter', _o(type='limit_post_only', price={'unit': 'price', 'value': '1'},
                                              expires_at_ms=946684800000)),
    ('fixed level in ATR units', 'enter', lambda p: p.update(stop=level('fixed', 'atr', '2'))),
    ('atr method in percent', 'enter', lambda p: p.update(stop=level('atr', 'percent', '2'))),
    ('lookback on a fixed level', 'enter', lambda p: p.update(stop=level(lookback=20))),
    ('recent_high_low without lookback', 'enter', lambda p: p.update(stop=level('recent_high_low', 'ticks', '5'))),
    ('trail trigger price, distance ticks', 'modify',
     lambda p: p.update(trail={'trigger': {'unit': 'price', 'value': '100'}, 'distance': {'unit': 'ticks', 'value': '5'}})),
    ('trail step in another unit', 'modify',
     lambda p: p.update(trail={'trigger': {'unit': 'atr', 'value': '1'}, 'distance': {'unit': 'atr', 'value': '1'},
                               'update_step': {'unit': 'percent', 'value': '1'}})),
])
def test_item5_signal_semantic_rules(name, action, fn):
    refuses('signal_intent', mutate(signal(action), fn), 'semantic')


def test_item5_freshness_against_the_callers_clock():
    p = signal()
    S.check_signal_intent(p, now_ms=T0 + 1_000)
    S.check_signal_intent(p, now_ms=T0 - S.CLOCK_SKEW_MS)                     # small sender clock lead tolerated
    for now in (T0 + 60_000, T0 + 120_000, T0 - S.CLOCK_SKEW_MS - 1):
        with pytest.raises(InvalidRecord):
            S.check_signal_intent(p, now_ms=now)


def test_item5_valid_level_and_trail_combinations():
    p = signal('modify')
    p.update(stop=level('recent_high_low', 'ticks', '5', 20), target=level('fixed', 'price', '70000'),
             trail={'trigger': {'unit': 'atr', 'value': '1'}, 'distance': {'unit': 'atr', 'value': '0.5'},
                    'update_step': {'unit': 'atr', 'value': '0.1'}})
    accepts('signal_intent', p)


def test_item5_registry_membership_and_scopes():
    assert S.reason_scopes('ingress.accepted') == {'result:accepted'}
    assert 'stand_down' in S.reason_scopes('stand_down.regime_veto')
    assert S.reason_scopes('filter.hours') == S.GATE_SCOPES
    for bad in ('zz.zz', 'filter.ai_veto', 'exit.stop', 'entry.signal', None, 5):
        assert S.reason_scopes(bad) == frozenset()
    assert all(S.REASON_SCOPES >= set(scopes) for scopes, _ in S.CONTRACT_REASONS.values())
    assert all(meaning for _, meaning in S.CONTRACT_REASONS.values())


@pytest.mark.parametrize('check,payload', [
    (S.check_signal_shape, {'action': 'enter'}),
    (S.check_signal_result, {'status': 'accepted'}),
    (S.check_candidate_shape, {}),
    (S.check_signal_shape, ['not', 'an', 'object']),
])
def test_item5_unvalidated_payloads_fail_closed_as_invalid_record(check, payload):
    with pytest.raises(InvalidRecord):
        check(payload)


# ------------------------------------------------------------------------------ item 6 support: the evaluator itself
def test_schema_subset_evaluator_refuses_unknown_keywords_and_matches_spec_edges():
    with pytest.raises(ValueError):
        errors({'type': 'string', 'format': 'email'}, 'x')
    assert errors({'type': 'integer'}, 1.0) == [] and errors({'type': 'integer'}, True) != []
    assert errors({'const': 1}, True) != [] and errors({'enum': [False]}, 0) != []
    for kind, schema in SCHEMAS.items():
        assert schema['additionalProperties'] is False and schema['properties']['contract_version'] == {'const': 1}
        errors(schema, {})                                                     # every keyword is supported


def test_sender_cannot_choose_environment_or_authority():
    s = SCHEMAS['signal_intent']
    assert not ({'secret', 'environment', 'mode', 'live', 'authority', 'testnet'} & set(s['properties']))


# --------------------------------------------------------------------- boundary: the validator package stays pure
def test_contracts_package_imports_only_stdlib_allowlist_and_domain():
    allowed = {'__future__', 'collections', 'decimal', 'functools', 'hashlib', 'inspect', 'json', 're'}
    bad = []
    for path in glob.glob(os.path.join(ROOT, 'newcore', 'contracts', '*.py')):
        for node in ast.walk(ast.parse(open(path, encoding='utf-8').read())):
            if isinstance(node, ast.ImportFrom) and node.level:
                ok = (node.module or '').split('.')[0] == 'domain' or node.level == 1 and not node.module
                bad += [] if ok else [f'{path}: from {"." * node.level}{node.module}']
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                bad += [f'{path}: {n}' for n in names if n.split('.')[0] not in allowed]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('open', 'print'):
                bad.append(f'{path}: {node.func.id}()')
    assert bad == []
