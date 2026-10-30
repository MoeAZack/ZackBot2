"""FACTORY-F01-PARITY-01 (Codex 6512403017, DRAFT, contract/preflight only): pins the JSON twin to the engine at master
2204e35 (candidate constants, C-F01 card, CAND_FIXED18 order, source sha256 of every cited function, signed contract file
hashes), checks the parity-field / failure-injection / canary blocks are complete and that the canary stays unauthorized,
and adds the offline failure injections the existing suites lack (restart mid-position, pending cost with C-F02 OFF, lost
protection with the lot still managed) plus runtime anchors for the MUST-MATCH sizing / stop fields.
Synthetic data only (tests/test_safety.FakeX); never reads market data, settings or state of a real bot."""
import ast
import math
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
def _bar(b):
    return int(str(b)[1:])                                  # opaque bar labels b0, b1, ... in bar order


def _real(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def num_close(a, b, rel=1e-9):
    """Cowork 6513562875 M-B: ONE numeric predicate for pre_gate P04 and quant_ok - both finite real numbers (not bool,
    NaN, +-inf, None, str) and |a - b| <= rel x |b| written as a positive test; b == 0 -> exact equality."""
    if not (_real(a) and _real(b)): return False
    return a == b if b == 0 else abs(a - b) <= rel * abs(b)


PRE_TYPES = {'P01': (str,), 'P02': (bool,), 'P03': (bool,), 'P05': (int,)}


def pre_gate_equal(pg):
    """6513165844: pre-gate match is COMPUTED from P01..P05 (P04 at rel 1e-9), never a free boolean."""
    if not isinstance(pg, dict): return False
    for pid in ('P01', 'P02', 'P03', 'P04', 'P05'):
        f = pg.get(pid)
        if not isinstance(f, dict) or 'rt' not in f or 'rp' not in f: return False
        if pid == 'P04':
            if not num_close(f['rt'], f['rp']): return False
            continue
        rt, rp, ty = f['rt'], f['rp'], PRE_TYPES[pid]
        if not (isinstance(rt, ty) and isinstance(rp, ty)) or type(rt) is not type(rp) or rt != rp: return False
        if pid == 'P05' and isinstance(rt, bool): return False
    return True


def mirror_ok(ev, window_id, explained, cap_reasons, step_bar=None):
    """N1 mirror capacity, evidence-derived (6512978555 + 6513165844 + Cowork 6513562875 M-A / M-B / M-C).
    cap_reasons: {frozen reason text: the cap count it names}."""
    pr, rs = ev.get('predecessor') or {}, ev.get('rp_state') or {}
    key, chk = pr.get('key'), ev.get('rp_check_bar')
    if window_id is None or pr.get('window_id') != window_id: return False                 # (1)
    if not key or list(key) not in explained: return False                                  # (2)
    pg = ev.get('pre_gate')
    if not pre_gate_equal(pg): return False                                                  # (3)
    if step_bar is None or chk != step_bar or pg['P01']['rt'] != step_bar: return False     # (3b) M-A: no stale evidence
    reason = ev.get('rp_refusal_reason')
    if reason not in cap_reasons: return False                                               # (4) exact frozen text
    if pr.get('t_in') is None or _bar(pr['t_in']) > _bar(chk): return False                 # (5) entered by the check bar
    if pr.get('t_out') is not None and _bar(pr['t_out']) < _bar(chk): return False           #     not exited before it
    op = [list(x) for x in rs.get('open') or []]                                              # (6) counted in the cap
    if list(key) not in op or len({tuple(x) for x in op}) != len(op): return False           #     M-C: unique keys
    n = cap_reasons[reason]
    if not (rs.get('cap_count') == len(op) == n): return False                               #     M-C: exactly the cited count
    return ev.get('symbol') not in {x[1] for x in op}                                         # (7) M-C: no same-coin exemption


def causal_verdict(steps, prefixes, cap_reasons=None):
    """Reference implementation of causal_window.algorithm - test-module only, never runtime code."""
    window, windows, excluded, explained, wid = None, [], [], [], None
    for st in steps:
        for ev in st['events']:
            t = ev['type']
            if t == 'pre_gate_mismatch':
                return dict(verdict='PARITY-FAIL', fail_at=st['bar'])
            if t == 'rt_only_entry':                   # N1 (6512978555): mirror capacity, bounded to the open window
                if window is None or not mirror_ok(ev, wid, explained, cap_reasons or {}, st['bar']):
                    return dict(verdict='PARITY-FAIL', fail_at=st['bar'])
                k_ = ev['predecessor']['key']
                excluded.append([st['bar'], ev['symbol'], 'mirror_cap', wid, k_[0], k_[1]])
            elif t == 'rt_refused_rp_entered':
                if not (ev.get('logged_at_decision') is True and any(ev['reason'].startswith(p) for p in prefixes)):
                    return dict(verdict='PARITY-FAIL', fail_at=st['bar'])
                if window is None:
                    window = [st['bar'], None]; windows.append(window); explained = []; wid = f'W{len(windows)}'
                explained.append([st['bar'], ev['symbol']])
            elif t == 'rp_only_entry':
                if window is None: return dict(verdict='PARITY-FAIL', fail_at=st['bar'])
                excluded.append([st['bar'], ev['symbol']])
            elif t != 'match':
                raise ValueError(t)
        if window is not None and st['rt_flat_after'] and st['rp_flat_after']:
            window[1] = st['bar']; window = None; explained = []; wid = None
    if window is not None:                             # 6512978555: not resynchronized at canary end -> never PASS
        return dict(verdict='INCONCLUSIVE', open_windows=[w for w in windows if w[1] is None], windows=windows, excluded=excluded)
    return dict(verdict='PASS', windows=windows, excluded=excluded)


def decode(x):
    """causal_window.value_encoding: {"float": "nan" | "inf" | "-inf"} -> IEEE float."""
    if isinstance(x, dict):
        if set(x) == {'float'}: return float(x['float'])
        return {k: decode(v) for k, v in x.items()}
    if isinstance(x, list): return [decode(v) for v in x]
    return x


def test_causal_window_test_vectors(c):
    cw = c['causal_window']
    assert cw['ruling'] == RULING and 'never in engine/runtime code' in cw['status']
    vs = cw['test_vectors']
    assert len(vs) >= 12 and {v['expect']['verdict'] for v in vs} == {'PASS', 'PARITY-FAIL', 'INCONCLUSIVE'}
    for v in vs:
        got = causal_verdict(decode(v['steps']), cw['registered_refusal_prefixes'], cw['mirror_cap_reason_counts'])
        assert {k_: got.get(k_) for k_ in v['expect']} == v['expect'], v['id']


def test_causal_window_is_bounded_and_has_no_same_symbol_exemption(c):
    pre = c['causal_window']['registered_refusal_prefixes']
    ref = dict(type='rt_refused_rp_entered', symbol='SOLUSDT', reason='daily loss halt', logged_at_decision=True)
    rp = dict(type='rp_only_entry', symbol='SOLUSDT')                                # replay-only path unchanged
    inside = [dict(bar='b1', events=[ref], rt_flat_after=True, rp_flat_after=False),
              dict(bar='b2', events=[rp], rt_flat_after=True, rp_flat_after=False)]
    got = causal_verdict(inside, pre)                                              # still open at the end: INCONCLUSIVE
    assert got['verdict'] == 'INCONCLUSIVE' and got['excluded'] == [['b2', 'SOLUSDT']] and got['open_windows'] == [['b1', None]]
    assert causal_verdict(inside + [dict(bar='b3', events=[], rt_flat_after=True, rp_flat_after=True)], pre)['verdict'] == 'PASS'
    after = inside + [dict(bar='b3', events=[], rt_flat_after=True, rp_flat_after=True),
                      dict(bar='b9', events=[rp], rt_flat_after=True, rp_flat_after=False)]
    assert causal_verdict(after, pre) == dict(verdict='PARITY-FAIL', fail_at='b9')      # same symbol after the reset fails
    assert causal_verdict([dict(bar='b1', events=[rp], rt_flat_after=True, rp_flat_after=False)], pre)['verdict'] == 'PARITY-FAIL'


def test_n1_mirror_capacity_and_open_window_at_end_rulings(c):
    cw = c['causal_window']
    assert cw['n1_ruling'] == '6512978555' and '6512978555' in cw['end_of_canary_rule']
    assert 'INCONCLUSIVE' in cw['end_of_canary_rule'] and 'never PARITY-PASS' in cw['end_of_canary_rule']
    assert 'force-close' in cw['end_of_canary_rule'] and 'extend risk or duration' in cw['end_of_canary_rule']
    assert '6512978555' in c['canary']['evidence_outputs']['overall_rule'] and 'still open at canary end' in c['canary']['evidence_outputs']['overall_rule']
    assert '6512978555' in c['replay_definition']['trade_set_rule'] and '6512978555' in c['amended_at']
    vs = {v['id']: v for v in cw['test_vectors']}
    assert vs['V9']['expect']['verdict'] == 'PASS' and vs['V9']['expect']['excluded'][0][2] == 'mirror_cap'
    assert all(vs[i]['expect']['verdict'] == 'PARITY-FAIL' for i in ('V6', 'V10', 'V11'))
    assert vs['V12']['expect'] == {'verdict': 'INCONCLUSIVE', 'open_windows': [['b1', None]]}
    md = open(MD, encoding='utf-8').read()
    assert '6512978555' in md and 'INCONCLUSIVE' in md and 'mirror' in md
    assert vs['V9']['expect']['excluded'] == [['b2', 'ADAUSDT', 'mirror_cap', 'W1', 'b1', 'SOLUSDT']]


def test_mirror_exception_is_evidence_derived(c):
    """6513165844: no free booleans; exact frozen cap reason; predecessor proven open and counted at the check bar."""
    cw = c['causal_window']
    assert cw['mirror_evidence_ruling'] == '6513165844' and '6513165844' in c['amended_at']
    ev9 = next(v for v in cw['test_vectors'] if v['id'] == 'V9')['steps'][1]['events'][0]
    assert {'rp_cap_refusal_logged', 'pre_gate_match', 'rp_slot_trace'}.isdisjoint(ev9)
    assert set(ev9['pre_gate']) == {'P01', 'P02', 'P03', 'P04', 'P05'}
    assert set(ev9['predecessor']) == {'key', 'window_id', 't_in', 't_out'} and set(ev9['rp_state']) == {'open', 'cap_count'}
    src = _src('engine.py')                                   # the frozen reasons are the engine's own cap texts
    assert "f'candidate family {fam}: {n} open (one shared cap of {CANDIDATE_MAX_POS})'" in src
    assert 'f"max positions reached ({sl[\'max_pos\']})"' in src
    want = ['candidate family {}: {} open (one shared cap of {})'.format(E.CANDIDATE_FAMILY['C-F01'], E.CANDIDATE_MAX_POS, E.CANDIDATE_MAX_POS),
            'max positions reached ({})'.format(E.CANDIDATE_MAX_POS)]
    assert cw['mirror_cap_reasons'] == want
    muts = [v for v in cw['test_vectors'] if v['note'].startswith('mutation:')]
    assert len(muts) >= 13 and all(v['expect']['verdict'] == 'PARITY-FAIL' for v in muts)
    for pid in ('P01', 'P02', 'P03', 'P04', 'P05'):
        assert any(v['note'] == f'mutation: {pid} mismatch at the mirror decision' for v in muts), pid
    for frag in ('non-cap replay refusal reason', 'exited before the check bar', 'not counted in the replay cap state'):
        assert any(frag in v['note'] for v in muts), frag


@pytest.mark.parametrize('pid,bad', [('P01', {'rt': 'b2', 'rp': 'b1'}), ('P02', {'rt': True, 'rp': False}),
                                     ('P03', {'rt': False, 'rp': True}), ('P04', {'rt': 1.0, 'rp': 1.0 + 1e-6}),
                                     ('P05', {'rt': 3, 'rp': 2}), ('P05', None)])
def test_mirror_fails_on_each_pre_gate_mismatch(c, pid, bad):
    cw = c['causal_window']
    v9 = json.loads(json.dumps(next(v for v in cw['test_vectors'] if v['id'] == 'V9')))
    pre, reasons = cw['registered_refusal_prefixes'], cw['mirror_cap_reason_counts']
    assert causal_verdict(v9['steps'], pre, reasons)['verdict'] == 'PASS'
    v9['steps'][1]['events'][0]['pre_gate'][pid] = bad
    assert causal_verdict(v9['steps'], pre, reasons) == dict(verdict='PARITY-FAIL', fail_at='b2')


@pytest.mark.parametrize('mut', ['reason', 'closed', 'late', 'uncounted', 'cap', 'window', 'key'])
def test_mirror_fails_on_each_occupancy_mutation(c, mut):
    cw = c['causal_window']
    v9 = json.loads(json.dumps(next(v for v in cw['test_vectors'] if v['id'] == 'V9')))
    ev = v9['steps'][1]['events'][0]
    if mut == 'reason': ev['rp_refusal_reason'] = 'max positions reached (5)'
    elif mut == 'closed': ev['predecessor']['t_out'] = 'b1'
    elif mut == 'late': ev['predecessor']['t_in'] = 'b3'
    elif mut == 'uncounted': ev['rp_state']['open'] = [x for x in ev['rp_state']['open'] if x != ev['predecessor']['key']] + [['b0', 'ADAUSDT']]
    elif mut == 'cap': ev['rp_state']['cap_count'] = 5
    elif mut == 'window': ev['predecessor']['window_id'] = 'W0'
    elif mut == 'key': ev['predecessor']['key'] = ['b1', 'ETHUSDT']
    got = causal_verdict(v9['steps'], cw['registered_refusal_prefixes'], cw['mirror_cap_reason_counts'])
    assert got == dict(verdict='PARITY-FAIL', fail_at='b2'), mut


def test_registered_refusal_prefixes_are_literal_engine_text(c):
    src = _src('engine.py')
    for p in c['causal_window']['registered_refusal_prefixes']:
        assert p in src, p


# ------------------------------------------------------------------ OQ6: exact post-quantization equality
def quant_ok(raw_rt, raw_rule, q_rt, step, raw_rel=1e-9):
    """P17 / P19: raw within rel AND the exchange-quantized value (floor to step / tick) equal EXACTLY. Same numeric
    predicate as pre_gate P04 (num_close; Cowork 6513562875 M-B)."""
    return num_close(raw_rt, raw_rule, raw_rel) and _real(q_rt) and q_rt == F.round_step(raw_rule, step)


def test_quantization_vectors_raw_rel_passes_but_one_step_fails(c):
    q = c['quantization_rule']
    assert q['ruling'] == RULING and q['fields'] == ['P17', 'P19']
    for v in q['test_vectors']:
        assert quant_ok(v['raw_rt'], v['raw_rule'], v['q_rt'], v['step']) == (v['expect'] == 'PASS'), v['id']
    q2 = next(v for v in q['test_vectors'] if v['id'] == 'Q2')
    assert abs(q2['raw_rt'] - q2['raw_rule']) <= 1e-9 * q2['raw_rule']                               # raw passes ...
    assert not quant_ok(q2['raw_rt'], q2['raw_rule'], q2['q_rt'], q2['step'])                        # ... one step fails
    assert not quant_ok(0.5, 0.5, 0.499, 0.001) and not quant_ok(95.0, 95.0, 95.01, 0.01)


# ------------------------------------------------------------------ Cowork BLOCK 6513562875: M-A / M-B / M-C
def _v9(c):
    return json.loads(json.dumps(next(v for v in c['causal_window']['test_vectors'] if v['id'] == 'V9')))


def _run(c, steps):
    cw = c['causal_window']
    return causal_verdict(decode(steps), cw['registered_refusal_prefixes'], cw['mirror_cap_reason_counts'])


def test_cowork_block_fix_is_recorded(c):
    cw = c['causal_window']
    assert '6513562875' in cw['mirror_integrity_fix'] and '6513562875' in c['amended_at']
    assert 'startswith' in cw['registered_prefix_match_note'] and '6513562875' in cw['registered_prefix_match_note']
    assert cw['mirror_cap_reason_counts'] == {r: E.CANDIDATE_MAX_POS for r in cw['mirror_cap_reasons']}
    alg = ' '.join(cw['algorithm'])
    for w in ('M-A', 'M-B', 'M-C', 'step bar', 'finite', 'unique', 'same-coin'):
        assert w in alg, w
    notes = {v['note'] for v in cw['test_vectors']}
    for frag in ('M-A stale rp_check_bar', 'M-A P01 decision bar', 'M-B P04 rt NaN', 'M-B P04 NaN / NaN', 'M-B P04 inf / inf',
                 'M-B P04 bool True / True', 'M-C runtime-only entry on the same coin', 'M-C four duplicate', 'M-C 5 open'):
        assert any(frag in n_ for n_ in notes), frag
    md = open(MD, encoding='utf-8').read()
    assert '6513562875' in md and 'startswith' in md


def test_m_a_stale_check_bar_reproduces_cowork_and_fails(c):
    stale = next(v for v in c['causal_window']['test_vectors'] if v['note'].startswith('mutation: M-A stale'))
    assert _run(c, stale['steps']) == dict(verdict='PARITY-FAIL', fail_at='b4')
    v9 = _v9(c)
    v9['steps'][1]['events'][0]['pre_gate']['P01'] = {'rt': 'b9', 'rp': 'b9'}
    assert _run(c, v9['steps']) == dict(verdict='PARITY-FAIL', fail_at='b2')


@pytest.mark.parametrize('rt,rp', [(float('nan'), 0.4321), (float('nan'), float('nan')), (float('inf'), float('inf')),
                                   (float('-inf'), float('-inf')), (True, True), (None, None), ('0.4321', '0.4321'),
                                   (0.4321, float('nan'))])
def test_m_b_non_finite_or_non_real_p04_fails_closed(c, rt, rp):
    v9 = _v9(c)
    v9['steps'][1]['events'][0]['pre_gate']['P04'] = {'rt': rt, 'rp': rp}
    assert _run(c, v9['steps']) == dict(verdict='PARITY-FAIL', fail_at='b2')
    assert not num_close(rt, rp)


def test_m_b_num_close_and_quant_ok_agree():
    for a, b in ((float('nan'), 1.0), (float('inf'), float('inf')), (True, True), (None, 1.0)):
        assert not num_close(a, b) and not quant_ok(a, b, 1.0, 0.001)
    assert num_close(0.0, 0.0) and not num_close(1e-300, 0.0)                     # rp == 0: exact equality
    assert num_close(1.0, 1.0 + 1e-10) and not num_close(1.0, 1.0 + 1e-6)
    assert quant_ok(0.5, 0.5, 0.5, 0.001) and not quant_ok(0.5, 0.5, float('nan'), 0.001)


@pytest.mark.parametrize('mut', ['same_coin', 'dup_keys', 'five_open', 'cap_count_mismatch'])
def test_m_c_coin_and_open_set_integrity(c, mut):
    v9 = _v9(c)
    ev = v9['steps'][1]['events'][0]
    if mut == 'same_coin': ev['symbol'] = ev['predecessor']['key'][1]
    elif mut == 'dup_keys': ev['rp_state'] = {'open': [ev['predecessor']['key']] * 4, 'cap_count': 4}
    elif mut == 'five_open':
        ev['rp_state']['open'] = ev['rp_state']['open'] + [['b0', 'DOGEUSDT']]; ev['rp_state']['cap_count'] = 5
    elif mut == 'cap_count_mismatch': ev['rp_state']['cap_count'] = 3
    assert _run(c, v9['steps']) == dict(verdict='PARITY-FAIL', fail_at='b2'), mut
    assert _run(c, _v9(c)['steps'])['verdict'] == 'PASS'                           # the unmutated V9 still passes
