"""FACTORY-F01-PARITY-01 (Codex 6512403017, DRAFT, contract/preflight only): pins the JSON twin to the engine at master
2204e35 (candidate constants, C-F01 card, CAND_FIXED18 order, source sha256 of every cited function, signed contract file
hashes), checks the parity-field / failure-injection / canary blocks are complete and that the canary stays unauthorized,
and adds the offline failure injections the existing suites lack (restart mid-position, pending cost with C-F02 OFF, lost
protection with the lot still managed) plus runtime anchors for the MUST-MATCH sizing / stop fields.
Synthetic data only (tests/test_safety.FakeX); never reads market data, settings or state of a real bot."""
import ast
import hashlib
import json
import os
import sys
from fractions import Fraction

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import mk_engine                       # noqa: E402  (FakeX engine, PAPER)
import engine as E                                       # noqa: E402
import feasibility as F                                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDIR = os.path.join(ROOT, 'contracts', 'factory_f01_parity_01')
JS = os.path.join(CDIR, 'factory_f01_parity_01_contract.v1.json')
MD = os.path.join(CDIR, 'FACTORY_F01_PARITY_01_CONTRACT.md')
REPRO = os.path.join(ROOT, 'contracts', 'factory_f01f02_repro', 'factory_f01f02_repro_contract.v1.json')
F01, F02 = 'C-F01', 'C-F02'
SG = dict(close=100.0, atr=2.0, time='2026-10-04 00:00:00', le=True, se=False, lx=False, sx=False)
RULING = '6512712537'
CLASSES = {'MUST-MATCH', 'MEASURED-DIFF', 'NOT-COMPARABLE'}


@pytest.fixture(scope='module')
def c():
    with open(JS, encoding='utf-8') as fh:
        return json.load(fh)


@pytest.fixture(scope='module')
def repro():
    with open(REPRO, encoding='utf-8') as fh:
        return json.load(fh)


def _src(path):
    with open(os.path.join(ROOT, path), 'rb') as fh:
        return fh.read().decode('utf-8').replace('\r\n', '\n')


def _fn_node(src, name):
    tree = ast.parse(src)
    if '.' in name:
        cls, meth = name.split('.')
        k = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
        return next(n for n in k.body if isinstance(n, ast.FunctionDef) and n.name == meth)
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def slot(e, sid):
    return next(x for x in e.S['SLEEVES'] if x['id'] == sid)


def lots(e, sid, sym=None):
    return [l for l in e.state['lots'].values() if l['sleeve'] == sid and (sym is None or l['symbol'] == sym)]


def cycle4(e, sigs):
    e.compute_signals = lambda tf, syms, extra=(): ({k: dict(v) for k, v in sigs.items()} if tf == '4h' else {}, {})
    e.cycle('4h')


@pytest.fixture
def f01(tmp_path):
    """The canary policy: C-F01 enabled, C-F02 OFF (family OQ4)."""
    e, tmp = mk_engine(str(tmp_path)); e.apply_preset(E.ROTATION_PRESET)
    assert e.sync_candidate_slots() == [F01, F02]
    slot(e, F01)['enabled'] = True
    assert slot(e, F02)['enabled'] is False
    return e, tmp


# ------------------------------------------------------------------ status, binding, pins
def test_status_is_draft_unsigned_and_md_present(c):
    assert c['contract'] == 'FACTORY-F01-PARITY-01' and c['version'] == 1
    assert c['status'] == 'DRAFT' and c['signed_by'] is None and '6512403017' in c['assignment']
    md = open(MD, encoding='utf-8').read()
    assert 'DRAFT' in md and 'unsigned' in md and '6512403017' in md and 'NOT AUTHORIZED' in md
    assert c['code']['citation_base_short'] == '2204e35' and c['code']['citation_base'].startswith('master 2204e35')


def test_binding_equals_the_engine_card(c):
    b = c['binding']
    assert E.CANDIDATE_SLOTS[F01] == (b['strategy_key'], b['side']) and b['cell'] == F01
    card = next(x for x in E.candidate_sleeves() if x['id'] == F01)
    assert card['key'] == b['strategy_key'] and card['sides'] == b['side'] and card['tf'] == b['tf']
    assert card['risk'] == b['risk'] == E.CANDIDATE_RISK and card['max_pos'] == b['max_pos'] == E.CANDIDATE_MAX_POS
    assert Fraction(b['share']) == Fraction(E.CANDIDATE_SHARE).limit_denominator(100) == Fraction(card['share']).limit_denominator(100)
    assert card['mgmt'] == b['mgmt_card'] == {} and E.S.merge_mgmt('ema_mom', card['mgmt']) == b['mgmt_effective']
    assert tuple(b['symbols']) == E.CAND_FIXED18 and card['symbols'] == b['symbols']        # exact order (OQ3)
    assert tuple(b['card_fields']) == E.CANDIDATE_CARD
    assert E.CANDIDATE_FAMILY[F01] == b['family'] and b['family_cap'] == E.CANDIDATE_MAX_POS


def test_c_f02_default_off_and_the_check_is_stated(c):
    assert E.CANDIDATE_DEFAULT_ENABLED is False
    assert next(x for x in E.candidate_sleeves() if x['id'] == F02)['enabled'] is False
    chk = c['binding']['c_f02_must_stay_off']
    assert 'OQ4' in chk['rule'] and 'enabled == false' in chk['check'] and 'P13' in chk['check'] and 'S2' in chk['on_violation']
    p13 = next(p for p in c['parity_fields'] if p['id'] == 'P13')
    assert p13['class'] == 'MUST-MATCH' and p13['tolerance'] == {'kind': 'exact'}


def test_signed_contract_files_match_their_pins(c):
    pins = {k: v for k, v in c['binding']['signed_contracts'].items() if not k.startswith('_')}
    assert len(pins) == 4
    for path, sha in pins.items():
        with open(os.path.join(ROOT, path), 'rb') as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == sha, path
        if path.endswith('.json'):
            assert json.load(open(os.path.join(ROOT, path), encoding='utf-8'))['status'] == 'SIGNED'


def test_every_pinned_function_source_matches(c):
    pins = {k: v for k, v in c['code']['pinned_function_sha256'].items() if not k.startswith('_')}
    assert len(pins) == 34
    for cite, sha in pins.items():
        path, name = cite.split(':')
        src = _src(path)
        assert hashlib.sha256(ast.get_source_segment(src, _fn_node(src, name)).encode('utf-8')).hexdigest() == sha, cite


def test_candidate_pins_equal_the_signed_family_pins(c):
    fam = json.load(open(os.path.join(ROOT, 'contracts', 'factory_f01f02_family', 'factory_f01f02_family_contract.v1.json'),
                         encoding='utf-8'))['code']['pinned_function_sha256']
    mine = c['code']['pinned_function_sha256']
    for k, v in fam.items():
        if not k.startswith('_'): assert mine[k] == v, k


# ------------------------------------------------------------------ parity fields
def test_every_parity_field_is_complete(c, repro):
    ps = c['parity_fields']
    assert [p['id'] for p in ps] == [f'P{i:02d}' for i in range(1, len(ps) + 1)]
    kinds = set(c['parity_class_rules']['tolerance_kinds'])
    groups = {'signals', 'entry_refusal', 'cap_isolation', 'sizing', 'stop', 'fills', 'exit', 'costs'}
    assert {p['group'] for p in ps} == groups
    for p in ps:
        assert p['class'] in CLASSES, p['id']
        for k in ('definition', 'runtime_source', 'replay_source', 'comparison_rule'): assert p[k].strip(), (p['id'], k)
        t = p['tolerance']
        assert t['kind'] in kinds, p['id']
        if p['class'] == 'MUST-MATCH': assert t['kind'] in ('exact', 'abs', 'rel', 'rel+exact'), p['id']
        else:
            assert t['kind'] in ('report', 'none'), p['id']
            assert p.get('legitimate_difference'), p['id']               # every non-matched field says why
        if t['kind'] in ('abs', 'rel'): assert t['value'] > 0
    sem = set(repro['semantics'])
    for p in ps:                                                        # replay keys exist in the signed semantics block
        for key in [w.split('.')[1].rstrip(',') for w in p['replay_source'].split() if w.startswith('semantics.')]:
            assert key in sem, (p['id'], key)


def test_parity_class_counts(c):
    from collections import Counter
    n = Counter(p['class'] for p in c['parity_fields'])
    assert n == {'MUST-MATCH': 18, 'MEASURED-DIFF': 8, 'NOT-COMPARABLE': 4}


def test_every_cited_runtime_function_exists(c):
    src = {f: _src(f) for f in ('engine.py', 'strategies.py', 'feasibility.py')}
    cites = set()
    for p in c['parity_fields']:
        for w in p['runtime_source'].replace('(', ' ').replace(')', ' ').replace(',', ' ').split():
            if ':' in w and w.split(':')[0] in src: cites.add(w)
    for fi in c['failure_injections']: cites |= set(fi['code'])
    assert len(cites) >= 15
    for cite in cites:
        path, name = cite.split(':')
        _fn_node(src[path], name.rstrip('.'))                           # raises StopIteration when missing


# ------------------------------------------------------------------ failure injections map to real tests
def _test_names(path):
    tree = ast.parse(_src(path))
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')}


def test_every_failure_injection_maps_to_existing_tests(c):
    fis = c['failure_injections']
    assert [f['id'] for f in fis] == ['FI1', 'FI2', 'FI3', 'FI4', 'FI5']
    here = os.path.relpath(__file__, ROOT).replace('\\', '/')
    for fi in fis:
        assert fi['inject'] and fi['expected'] and fi['tests'] and fi['code'], fi['id']
        assert fi['new'] == any(t.startswith(here + '::') for t in fi['tests']), fi['id']
        for t in fi['tests']:
            path, name = t.split('::')
            assert name in _test_names(path), t


# ------------------------------------------------------------------ the canary is specified, never authorized
def test_canary_is_unauthorized_and_bounded(c):
    k = c['canary']
    assert k['status'] == 'SPECIFIED, NOT AUTHORIZED' and k['authorization'] is None and k['authorization_comment_id'] is None
    assert [s['id'] for s in k['stop_conditions']] == [f'S{i}' for i in range(1, 8)]
    assert all(s['rule'].strip() for s in k['stop_conditions'])
    assert [p['id'] for p in k['preconditions']] == ['C1', 'C1b'] + [f'C{i}' for i in range(2, 11)]
    assert any('Integration (only)' in r for r in k['rollback']) and any('NOT closed' in r for r in k['rollback'])
    ev = k['evidence_outputs']
    assert set(ev['files']) == {'preflight.json', 'frames_manifest.json', 'decisions.csv', 'trades.csv', 'verdict.json', 'MANIFEST.sha256'}
    for f in repro_forbidden():
        assert f in ev['forbidden_output_fields']
    for col in ev['files']['trades.csv'].split(', '):
        assert col not in ev['forbidden_output_fields'] and 'order' not in col.lower()
    assert c['frozen'] and set(c['frozen']) >= {'function_pins', 'signed_contracts', 'inputs', 'tolerances', 'stop_conditions', 'evidence_output_schema'}
    assert any('authoriz' in f for f in c['forbidden']) and any('2026-09-28' in f for f in c['forbidden'])


def repro_forbidden():
    with open(REPRO, encoding='utf-8') as fh:
        return json.load(fh)['verdict_schema']['forbidden_output_fields']


def test_open_questions_all_resolved(c):
    assert c['open_questions'] == []
    assert [q['id'] for q in c['resolved_questions']] == [f'OQ{i}' for i in range(1, 9)]
    assert all(q['ruling'] == RULING and q['resolution'].strip() and q['encoded_in'].strip() for q in c['resolved_questions'])


# ------------------------------------------------------------------ runtime anchors for MUST-MATCH sizing / stop fields
def test_p14_p16_p19_p20_runtime_sizing_and_stop(f01):
    e, _ = f01
    eq = e.equity()
    assert e.kelly_mult(slot(e, F01)) == 1.0 and e.governor_mult() == 1.0
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    assert l['risk_usd'] == pytest.approx(eq * E.CANDIDATE_SHARE * E.CANDIDATE_RISK, abs=0.005)        # P14
    assert l['mgmt'] == {'stop_atr': 2.5}                                                             # P20
    r_dist = 2.5 * SG['atr']                                                                          # P16
    tick, step = e.rules['BTCUSDT']['tick'], e.rules['BTCUSDT']['step']
    assert quant_ok(l['avg'] - r_dist, l['avg'] - r_dist, l['stop'], tick)                          # P19: exact on the tick
    qty_raw = (eq * E.CANDIDATE_SHARE * E.CANDIDATE_RISK) / r_dist                                    # P17: exact on the step
    assert l['qty'] == F.round_step(qty_raw, step) and l['stop'] == F.round_step(l['avg'] - r_dist, tick)
    assert not lots(e, F02)                                                                           # P13


def test_p06_a_held_coin_is_refused_and_p13_c_f02_never_trades(f01):
    e, _ = f01
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    cycle4(e, {f'{F01}|BTCUSDT': SG, f'{F02}|ETHUSDT': SG})
    assert len(lots(e, F01, 'BTCUSDT')) == 1
    reasons = {(m['sleeve'], m['symbol']): m['reason'] for m in e.missed}
    assert reasons[(F01, 'BTCUSDT')] == 'already in a trade on this coin'
    assert reasons[(F02, 'ETHUSDT')] == 'strategy slot switched off' and not lots(e, F02)


# ------------------------------------------------------------------ new failure injections
def test_fi1_restart_mid_position_keeps_the_lot_protected_and_managed(f01):
    e, tmp = f01
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    stop, sid = l['stop'], l['stop_id']
    e.save_settings(); e.save_state()
    e2, _ = mk_engine(tmp)                                   # restart on the same data dir
    e2.trade.pos, e2.trade.stops = e.trade.pos, e.trade.stops    # the same exchange: position and resting stop survive
    (l2,) = lots(e2, F01, 'BTCUSDT')
    assert (l2['stop'], l2['stop_id'], l2['sleeve'], l2['mgmt']) == (stop, sid, F01, {'stop_atr': 2.5})
    assert slot(e2, F01)['enabled'] is True and slot(e2, F02)['enabled'] is False
    assert e2.candidate_guard() == []
    cycle4(e2, {f'{F01}|BTCUSDT': SG})                        # same-coin signal after the restart
    assert len(lots(e2, F01, 'BTCUSDT')) == 1 and e2.missed[-1]['reason'] == 'already in a trade on this coin'
    cycle4(e2, {f'{F01}|BTCUSDT': dict(SG, le=False, lx=True)})   # management continues: signal exit
    assert not lots(e2, F01) and e2.history[-1]['sleeve'] == F01 and e2.history[-1]['exit_reason'] == 'exit_signal'
    assert not lots(e2, F02) and slot(e2, F02)['enabled'] is False


def test_fi2_pending_cost_over_six_hours_with_c_f02_off(f01, monkeypatch):
    e, _ = f01
    monkeypatch.setattr(E.AC, 'row_view', lambda row: dict(status=row.get('_st', 'measured'), pnl=None))
    t0 = 1_800_000_000.0
    e.history.append(dict(sleeve=F01, symbol='ETHUSDT', closed=E.pd.Timestamp(t0, unit='s', tz='UTC').isoformat(), _st='pending'))
    assert e.candidate_guard(now=t0 + E.CANDIDATE_COST_PENDING_S - 1) == []
    assert e.candidate_guard(now=t0 + E.CANDIDATE_COST_PENDING_S) == [F01]       # C-F02 was already OFF: not reported
    assert e.state['candidate_disabled'][F01]['why'].startswith('accounting: ETHUSDT trade costs unmeasured 6.0 h')
    assert F02 not in e.state['candidate_disabled'] and slot(e, F02)['enabled'] is False


@pytest.mark.parametrize('why', ['restoring', 'owner_check'])
def test_fi4_lost_protection_disables_c_f01_and_the_lot_is_still_managed(f01, why):
    e, _ = f01
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    stop = e.trade.stops[l['stop_id']]
    l.update(stop_miss=True, stop_miss_why='checking')
    assert e.candidate_guard() == []
    l['stop_miss_why'] = why
    assert e.candidate_guard() == [F01] and slot(e, F01)['enabled'] is False and slot(e, F02)['enabled'] is False
    assert e.state['candidate_disabled'][F01]['why'] == f'protection: BTCUSDT stop {why}'
    assert lots(e, F01, 'BTCUSDT') == [l] and e.trade.stops[l['stop_id']] == stop     # never closed or unprotected
    l.update(stop_miss=False, stop_miss_why=None)
    cycle4(e, {f'{F01}|ETHUSDT': SG})                                                   # new entries stay off
    assert not lots(e, F01, 'ETHUSDT') and e.missed[-1]['reason'] == 'strategy slot switched off'
    cycle4(e, {f'{F01}|BTCUSDT': dict(SG, le=False, lx=True)})                         # the open lot is still managed
    assert not lots(e, F01) and e.history[-1]['exit_reason'] == 'exit_signal'


# ------------------------------------------------------------------ amendment 1: rulings 6512712537 (OQ1-OQ8)
def test_every_ruling_is_encoded_with_its_values(c):
    assert RULING in c['amended_at'] and RULING in c['assignment'] and RULING in c['resolved_questions_source']
    assert c['status'] == 'DRAFT' and c['signed_by'] is None
    k = c['canary']
    b = k['bounds']                                                                                   # OQ1
    assert b['ruling'] == RULING
    assert (b['duration_days'], b['max_closed_trades'], b['max_concurrent'], b['drawdown_stop_r']) == (30, 8, 4, 4.0)
    assert 'first of 30 calendar days or 8 closed' in b['end_rule'] and 'canary peak' in b['drawdown_basis']
    assert 'not a profitability verdict' in k['purpose'] and 'bounds_proposed' not in k
    sc = c['replay_definition']['scoring_start']                                                       # OQ2
    for w in ('250', 'WARM-UP ONLY', 'frozen', 'BOTH the authorization and a successful preflight', 'never to retune'):
        assert w in sc, w
    assert k['scoring'] == sc
    pre = {p['id']: p['rule'] for p in k['preconditions']}                                            # OQ3
    assert 'candidate-flat' in pre['C1'] and 'never imported into scoring' in pre['C1']
    assert 'captured in preflight.json' in pre['C1b'] and 'P08 / P09 / P10' in pre['C1b']
    tsr = c['replay_definition']['trade_set_rule']                                                    # OQ4
    assert 'explicitly listed causal window' in tsr and 'No open-ended same-symbol exemption' in tsr and 'post-hoc' in tsr
    assert 'MUST-MATCH throughout' in tsr
    assert 'never switched off' in pre['C8'] and 'MAX_LEVERAGE 10' in pre['C8'] and 'ENTRY_ORDER market' in pre['C8']   # OQ5
    assert '1.0' in pre['C6'] and '1.0' in pre['C7'] and 'without changing any global PAPER setting' in pre['C6']
    ps = {p['id']: p for p in c['parity_fields']}
    assert 'LISTED' in ps['P10']['comparison_rule'] and 'ACTUAL cap effect' in ps['P18']['comparison_rule']
    assert 'frozen neutral multipliers' in ps['P14']['legitimate_difference']
    assert ps['P04']['tolerance'] == {'kind': 'rel', 'value': 1e-9} == ps['P16']['tolerance']          # OQ6
    assert ps['P14']['tolerance']['value'] == 0.005
    for pid in ('P17', 'P19'):
        t = ps[pid]['tolerance']
        assert t['kind'] == 'rel+exact' and t['raw_rel'] == 1e-9 and t['quantized'] == 'exact'
        assert 'feasibility.py:round_step' in t['quantizer']
    own = k['evidence_outputs']['ownership']                                                          # OQ7
    assert own['cowork'] == ['verdict.json'] and 'verdict.json' not in own['build_runtime_extracts']
    assert 'never self-verdicts' in c['replay_definition']['executor']
    assert any('NOT closed' in r and 'confirmed exchange protection' in r for r in k['rollback'])     # OQ8
    assert k['force_close_only_for'] == ['unknown account state', 'lost or unrestorable protection', 'environment uncertainty',
                                         'a separately recorded safety decision']
    md = open(MD, encoding='utf-8').read()
    assert RULING in md and 'DRAFT' in md
    for i in range(1, 9): assert f'OQ{i}' in md


def test_canary_execution_and_market_data_stay_forbidden(c):
    k = c['canary']
    assert k['authorization'] is None and k['authorization_comment_id'] is None and 'NOT AUTHORIZED' in k['status']
    f = ' | '.join(c['forbidden'])
    assert 'executing the canary' in f and 'any market-data read under this ticket' in f and 'global PAPER settings' in f


# ------------------------------------------------------------------ OQ4: bounded causal window (reference helper, test only)
def causal_verdict(steps, prefixes):
    """Reference implementation of causal_window.algorithm - test-module only, never runtime code."""
    window, windows, excluded = None, [], []
    for st in steps:
        for ev in st['events']:
            t = ev['type']
            if t in ('pre_gate_mismatch', 'rt_only_entry'):
                return dict(verdict='PARITY-FAIL', fail_at=st['bar'])
            if t == 'rt_refused_rp_entered':
                if not (ev.get('logged_at_decision') is True and any(ev['reason'].startswith(p) for p in prefixes)):
                    return dict(verdict='PARITY-FAIL', fail_at=st['bar'])
                if window is None:
                    window = [st['bar'], None]; windows.append(window)
            elif t == 'rp_only_entry':
                if window is None: return dict(verdict='PARITY-FAIL', fail_at=st['bar'])
                excluded.append([st['bar'], ev['symbol']])
            elif t != 'match':
                raise ValueError(t)
        if window is not None and st['rt_flat_after'] and st['rp_flat_after']:
            window[1] = st['bar']; window = None
    return dict(verdict='PASS', windows=windows, excluded=excluded)


def test_causal_window_test_vectors(c):
    cw = c['causal_window']
    assert cw['ruling'] == RULING and 'never in engine/runtime code' in cw['status']
    vs = cw['test_vectors']
    assert len(vs) >= 8 and {v['expect']['verdict'] for v in vs} == {'PASS', 'PARITY-FAIL'}
    for v in vs:
        got = causal_verdict(v['steps'], cw['registered_refusal_prefixes'])
        assert {k_: got.get(k_) for k_ in v['expect']} == v['expect'], v['id']


def test_causal_window_is_bounded_and_has_no_same_symbol_exemption(c):
    pre = c['causal_window']['registered_refusal_prefixes']
    ref = dict(type='rt_refused_rp_entered', symbol='SOLUSDT', reason='daily loss halt', logged_at_decision=True)
    rp = dict(type='rp_only_entry', symbol='SOLUSDT')
    inside = [dict(bar='b1', events=[ref], rt_flat_after=True, rp_flat_after=False),
              dict(bar='b2', events=[rp], rt_flat_after=True, rp_flat_after=False)]
    got = causal_verdict(inside, pre)
    assert got['verdict'] == 'PASS' and got['excluded'] == [['b2', 'SOLUSDT']] and got['windows'] == [['b1', None]]
    after = inside + [dict(bar='b3', events=[], rt_flat_after=True, rp_flat_after=True),
                      dict(bar='b9', events=[rp], rt_flat_after=True, rp_flat_after=False)]
    assert causal_verdict(after, pre) == dict(verdict='PARITY-FAIL', fail_at='b9')      # same symbol after the reset fails
    assert causal_verdict([dict(bar='b1', events=[rp], rt_flat_after=True, rp_flat_after=False)], pre)['verdict'] == 'PARITY-FAIL'


def test_registered_refusal_prefixes_are_literal_engine_text(c):
    src = _src('engine.py')
    for p in c['causal_window']['registered_refusal_prefixes']:
        assert p in src, p


# ------------------------------------------------------------------ OQ6: exact post-quantization equality
def quant_ok(raw_rt, raw_rule, q_rt, step, raw_rel=1e-9):
    """P17 / P19: raw within rel AND the exchange-quantized value (floor to step / tick) equal EXACTLY."""
    return abs(raw_rt - raw_rule) <= raw_rel * abs(raw_rule) and q_rt == F.round_step(raw_rule, step)


def test_quantization_vectors_raw_rel_passes_but_one_step_fails(c):
    q = c['quantization_rule']
    assert q['ruling'] == RULING and q['fields'] == ['P17', 'P19']
    for v in q['test_vectors']:
        assert quant_ok(v['raw_rt'], v['raw_rule'], v['q_rt'], v['step']) == (v['expect'] == 'PASS'), v['id']
    q2 = next(v for v in q['test_vectors'] if v['id'] == 'Q2')
    assert abs(q2['raw_rt'] - q2['raw_rule']) <= 1e-9 * q2['raw_rule']                               # raw passes ...
    assert not quant_ok(q2['raw_rt'], q2['raw_rule'], q2['q_rt'], q2['step'])                        # ... one step fails
    assert not quant_ok(0.5, 0.5, 0.499, 0.001) and not quant_ok(95.0, 95.0, 95.01, 0.01)
