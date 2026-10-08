"""Round-3 hardening of the golden gate (Codex r2 review on b01d439 + Cowork r2 attack). Each test names the finding it pins;
the mutation that must make it fail is in the docstring."""
import copy, json, math, os, subprocess, sys, tempfile, textwrap, time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from goldenlib import REPO_ROOT, adapters, compare, deadline, schema  # noqa: E402
from goldenlib.adapters import Trace  # noqa: E402

import test_golden as TG  # noqa: E402
import test_ledger as TL  # noqa: E402


def _case(R='-1.0', pnl='-10', tol=None):
    t = dict(sym='S', side='LONG', i_in=1, i_out=2, exit='STOP_HIT', R=R, pnl=pnl)
    if tol:
        t['tol'] = tol
    return {'id': 'X', 'expect': {'trades': [t], 'final': {'lots': 0}}}


def _act(R=-1.0, pnl=-10.0, res=None):
    a = dict(sym='S', side='LONG', i_in=1, i_out=2, exit='STOP_HIT', R=R, pnl=pnl)
    if res is not None:
        a['_res'] = res
    return Trace(trades=[a], final={'lots': 0})


# ------------------------------------------------------------------ P1: non-finite numbers never pass a comparison
@pytest.mark.parametrize('bad', ['NaN', 'nan', 'Infinity', 'inf', '-inf', '1e999', ' 1', '1_0', True, None, float('nan'), float('inf')])
def test_expected_must_be_finite(bad):
    """Mutation: expected R = NaN (Codex repro) -> must raise, never return []."""
    with pytest.raises(schema.NumberError):
        compare.compare(_case(R=bad), _act(), 'legacy_engine')


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), 'x', '-1.0', True])
def test_actual_must_be_finite(bad):
    """Mutation: actual R = NaN against expected -1.0 (Codex repro: zero mismatches) -> must raise."""
    with pytest.raises(schema.NumberError):
        compare.compare(_case(), _act(R=bad), 'legacy_engine')
    with pytest.raises(schema.NumberError):
        compare.compare(_case(), _act(pnl=bad), 'legacy_engine')


@pytest.mark.parametrize('res', [dict(R=float('nan')), dict(R=float('inf')), dict(pnl=float('nan')), dict(pnl=float('inf')),
                                 dict(R=-1e-4), dict(R=compare.RES_MAX['R'] * 2), dict(pnl=compare.RES_MAX['pnl'] * 2),
                                 dict(R=True), dict(R='0.0001'), [1]])
def test_res_is_finite_nonnegative_and_bounded(res):
    """Mutation: _res = NaN / Inf (a NaN resolution made every R pass), negative, or a tolerance in disguise."""
    with pytest.raises(schema.NumberError):
        compare.compare(_case(), _act(R=-5.0, pnl=-50.0, res=res), 'legacy_engine')


def test_res_bound_still_admits_the_engine_journal_resolution():
    assert compare.RES_MAX == {'R': 0.005, 'pnl': 1e-4}
    assert compare.compare(_case(), _act(R=-1.0004, res=dict(R=0.0005, pnl=1e-4)), 'legacy_engine') == []
    assert [m['path'] for m in compare.compare(_case(), _act(R=-1.0006, res=dict(R=0.0005)), 'legacy_engine')] == ['trades[0].R']


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', float('nan'), '-0.0001', '0.002', True])
def test_every_declared_tolerance_is_finite(bad):
    """Also for a tolerance that names ANOTHER adapter: a NaN tolerance is malformed wherever it applies."""
    for ads in (['legacy_engine'], ['legacy_backtest']):
        with pytest.raises(schema.NumberError):
            compare.compare(_case(tol=dict(R=bad, adapters=ads, why='x')), _act(), 'legacy_engine')


def test_bool_never_equals_a_number():
    assert compare.compare(_case(), Trace(trades=_act().trades, final={'lots': False}), 'legacy_engine') == \
        [dict(path='final.lots', expected=0, actual=False)]


@pytest.mark.parametrize('text', ['{"R": NaN}', '{"R": Infinity}', '{"R": -Infinity}', '{"R": 1e999}'])
def test_json_loading_rejects_non_finite(text):
    with pytest.raises(ValueError):
        schema.strict_loads(text)


def test_schema_rejects_non_finite_expectations(tmp_path):
    base = schema.load(schema.case_paths()[0])
    for k in ('R', 'pnl'):
        for bad in ('NaN', 'Infinity', '1e999', True):
            c = copy.deepcopy(base)
            c['expect']['trades'][0][k] = bad
            with pytest.raises(schema.CaseError, match=k):
                schema.validate(c)
    c = copy.deepcopy(base)
    c['expect']['trades'][0]['tol'] = dict(R='NaN', adapters=['legacy_engine'], why='x')
    with pytest.raises(schema.CaseError, match='tol'):
        schema.validate(c)
    p = tmp_path / f"{base['id']}.json"                    # a NaN token in the FILE fails at load, before any canonicalising
    p.write_text(json.dumps(base).replace(json.dumps(base['expect']['trades'][0]['R']), 'NaN', 1), encoding='utf-8')
    with pytest.raises(schema.CaseError, match='NaN'):
        schema.load(str(p))


# ------------------------------------------------------------------ P2: a real hard stop with process-tree cleanup
def test_deadline_kills_the_tree_at_the_deadline(tmp_path):
    """Mutation (Codex): HARD_STOP_S = .1 with two 0.3 s calls -> the run must stop at about 0.1 s, grandchild included."""
    flag = tmp_path / 'grandchild_survived'
    grand = f"import time; time.sleep(1.0); open({str(flag)!r}, 'w').write('x')"
    child = textwrap.dedent(f'''
        import subprocess, sys, time
        subprocess.Popen([sys.executable, '-c', {grand!r}])
        time.sleep(0.3)
        time.sleep(0.3)
        print('finished')
    ''')
    r = deadline.run([sys.executable, '-c', child], 0.1)
    assert r.timed_out and r.returncode is None and 'finished' not in r.stdout
    assert r.elapsed < 0.55, f'stopped after {r.elapsed:.2f} s, not at the 0.1 s deadline'
    time.sleep(1.4)
    assert not flag.exists(), 'the grandchild outlived the deadline (tree not killed)'


def test_deadline_passes_a_normal_child_through():
    r = deadline.run([sys.executable, '-c', 'print(42)'], 30)
    assert not r.timed_out and r.returncode == 0 and r.stdout.strip() == '42'


def test_pack_hard_stop_is_enforced_on_the_real_worker(tmp_path):
    """The pack runner itself (not only the helper): a 0.1 s hard stop kills the real worker and reports it; a worker that
    makes two 0.3 s calls is stopped at about 0.1 s."""
    p = TG.run_pack(0.1)
    assert p['timed_out'] and 'HARD STOP' in p['error'] and p['runs'] == {} and p['elapsed'] < 1.0
    slow = tmp_path / 'slow_worker.py'
    slow.write_text('import time\ntime.sleep(0.3)\ntime.sleep(0.3)\n', encoding='utf-8')
    p = TG.run_pack(0.1, worker=str(slow))
    assert p['timed_out'] and p['elapsed'] < 0.55, p


def test_hard_stop_constants_are_pinned():
    assert (TG.CORE_TARGET_S, TG.EXTENDED_TARGET_S, TG.HARD_STOP_S) == (30.0, 90.0, 120.0)


# ------------------------------------------------------------------ P2: the runner rejects unknown / empty selections
def _runner(*args):
    return subprocess.run([sys.executable, os.path.join(HERE, 'runner.py'), *args], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=120)


@pytest.mark.parametrize('args', [('DOES-NOT-EXIST',), ('G-STOP-L-01', 'DOES-NOT-EXIST'), ('g-stop-l-01',)])
def test_runner_rejects_unknown_ids(args):
    t0 = time.perf_counter()
    r = _runner(*args)
    assert r.returncode == 2, (r.returncode, r.stdout[-500:], r.stderr[-500:])
    assert 'unknown golden case id' in r.stderr and 'DOES-NOT-EXIST' not in r.stdout
    assert time.perf_counter() - t0 < 30, 'rejected only after running the pack'


# ------------------------------------------------------------------ Cowork (a): adapters never read the expectation
class _Echo:
    """A cheating adapter: returns the expectation as its trace."""
    def run(self, case):
        ex = case.get('expect') or {}
        tr = [dict(t, R=float(t['R']), pnl=float(t.get('pnl', 0))) for t in ex.get('trades') or []]
        for t in tr:
            t.pop('tol', None)
        return Trace(trades=tr, final=dict(ex.get('final') or {}))


def test_an_echoing_adapter_fails_on_the_blind_case():
    """Mutation: an adapter that echoes case['expect'] - it would pass on the full case, it must fail on what the pack feeds it."""
    for c in schema.load_all():
        assert compare.compare(c, _Echo().run(c), 'legacy_engine') == []           # the threat is real on the full case
        assert compare.compare(c, _Echo().run(adapters.blind(c)), 'legacy_engine') != []


def test_blind_keeps_every_input_and_hides_every_expectation():
    for c in schema.load_all():
        b = adapters.blind(c)
        assert 'known_divergences' not in b and adapters.POISON in json.dumps(b['expect'])
        assert {k: v for k, v in b.items() if k != 'expect'} == {k: v for k, v in c.items() if k not in ('expect', 'known_divergences')}
        assert c['expect'] != b['expect']                                          # the original is untouched


def test_the_pack_worker_only_hands_adapters_the_blind_case():
    src = open(os.path.join(HERE, 'pack_worker.py'), encoding='utf-8').read()
    assert "rec['trace'] = _trace(ad, adapters.blind(c))" in src


# ------------------------------------------------------------------ Cowork (b) + (c): DRIFT and same_mismatches
def test_drift_is_pinned():
    """Mutation: loosen DRIFT (1e-5, 1e-3) -> fails."""
    assert compare.DRIFT == 1e-6
    assert compare._same(-1.049510, -1.0495109, 'R')
    assert not compare._same(-1.049510, -1.049512, 'R')                            # 2e-6: drift
    assert not compare._same(-10.4951, -10.4952, 'pnl')


OBS = [dict(path='trades[0].R', expected='-1.059307099', actual=-1.04951),
       dict(path='trades[0].pnl', expected='-10.59307099', actual=-10.4951)]


def test_same_mismatches_accepts_only_the_recorded_divergence():
    """Mutation: same_mismatches() always True -> the negative assertions fail."""
    sm = lambda m: compare.same_mismatches({}, OBS, m)
    assert sm(copy.deepcopy(OBS)) and sm(list(reversed(copy.deepcopy(OBS))))
    assert sm([dict(OBS[0], actual=-1.0495104), OBS[1]])                         # inside the 6 dp recording
    bad = [OBS[:1],                                                              # one mismatch gone
           OBS + [dict(path='trades[0].exit', expected='STOP_HIT', actual='TIME_EXIT')],   # one more
           [dict(OBS[0], actual=-1.04), OBS[1]],                                 # actual drifted
           [dict(OBS[0], expected='-1.0'), OBS[1]],                              # expectation differs
           [dict(OBS[0], path='trades[1].R'), OBS[1]],                           # other path
           [dict(OBS[0], actual=None), OBS[1]],                                  # value vanished
           [],
           [dict(path='trades', expected=[], actual=[])]]
    for m in bad:
        assert not sm(m), m
    with pytest.raises(schema.NumberError):
        sm([dict(OBS[0], actual=float('nan')), OBS[1]])


# ------------------------------------------------------------------ Codex P2: divergence -> contract-bearing record
A, B, C = 'a' * 64, 'b' * 64, 'c' * 64


def _kd_case(cid='X', corr='CORR-0005', observed=None):
    c = {'id': cid, 'schema': 'zb-golden/1', 'expect': {'trades': []},
         'known_divergences': [dict(adapter='legacy_engine', ticket='AUD-07', finding='F', reason='r', correction=corr,
                                    observed=observed or [dict(path='trades', expected=[], actual=[])])]}
    return c


def test_divergence_pointer_must_be_contract_bearing():
    c = _kd_case()
    cur = schema.contract_sha(c)
    E = lambda i, t, rec: dict(id=f'CORR-{i:04d}', type=t, cases={'X': rec})
    ok = [E(4, 'add', dict(prev_contract_sha=None, contract_sha=A)), E(5, 'correction', dict(prev_contract_sha=A, contract_sha=cur))]
    TL.check_divergence_pointers({'X': c}, ok)
    for ents, corr in (([*ok, E(6, 'retire', dict(prev_contract_sha=cur))], 'CORR-0006'),                    # retire target
                       ([dict(id='CORR-0001', type='add', cases={'X': {'new_expect_sha': A}}), *ok], 'CORR-0001'),  # expect-only
                       (ok, 'CORR-0009'),                                                                     # missing
                       ([*ok, dict(id='CORR-0006', type='correction', cases={'Y': {}})], 'CORR-0006'),          # other case
                       ([ok[0], E(5, 'correction', dict(prev_contract_sha=A, contract_sha=B))], 'CORR-0005')):  # latest, wrong contract
        with pytest.raises(AssertionError):
            TL.check_divergence_pointers({'X': _kd_case(corr=corr)}, ents)


def test_divergence_is_bound_to_the_record_that_introduced_it():
    c = _kd_case(corr='CORR-0005')
    cur = schema.contract_sha(c)
    base_led = [dict(id='CORR-0004', type='add', cases={'X': dict(prev_contract_sha=None, contract_sha=A)})]
    intro = dict(id='CORR-0005', type='correction', cases={'X': dict(prev_contract_sha=A, contract_sha=cur)})
    TL.check_divergence_binding({'X': c}, base_led + [intro], {'X': dict(c, known_divergences=[])}, 1)    # new, bound
    TL.check_divergence_binding({'X': c}, base_led + [intro], {}, 0)                                       # bootstrap
    # an unrelated OLDER record: the divergence is new against the base but points at CORR-0004
    with pytest.raises(AssertionError):
        TL.check_divergence_binding({'X': _kd_case(corr='CORR-0004')}, base_led + [intro], {'X': dict(c, known_divergences=[])}, 1)
    # appended record that does not carry the contract containing the divergence
    bad = dict(intro, cases={'X': dict(prev_contract_sha=A, contract_sha=B)})
    with pytest.raises(AssertionError):
        TL.check_divergence_binding({'X': c}, base_led + [bad], {'X': dict(c, known_divergences=[])}, 1)
    # a retire record appended after the base
    with pytest.raises(AssertionError):
        TL.check_divergence_binding({'X': _kd_case(corr='CORR-0006')}, base_led + [intro, dict(id='CORR-0006', type='retire',
                                    cases={'X': dict(prev_contract_sha=cur)})], {'X': dict(c, known_divergences=[])}, 2)
    # later UNRELATED correction of the case: the divergence is identical to the base's, its pointer stays valid
    later = dict(c, slot={'changed': 1})
    TL.check_divergence_binding({'X': later}, base_led + [intro, dict(id='CORR-0006', type='correction',
                                cases={'X': dict(prev_contract_sha=cur, contract_sha=schema.contract_sha(later))})], {'X': c}, 2)
    # an inherited divergence whose observed list or pointer changed is new again and must be re-bound
    with pytest.raises(AssertionError):
        TL.check_divergence_binding({'X': _kd_case(corr='CORR-0004')}, base_led + [intro], {'X': c}, 2)
    drift = _kd_case(observed=[dict(path='trades', expected=[], actual=[1])])
    with pytest.raises(AssertionError):
        TL.check_divergence_binding({'X': drift}, base_led + [intro], {'X': c}, 2)


# ------------------------------------------------------------------ Cowork (d): the base is never the commit under test
def test_self_base_is_refused_when_head_changes_the_pack():
    TL.refuse_self_base('origin/x', A, B, lambda: True)                          # different commits: fine
    TL.refuse_self_base('HEAD', A, A, lambda: False)                             # HEAD leaves the pack alone: fine
    for changes in (True, None):                                                 # changes it / cannot tell
        with pytest.raises(pytest.fail.Exception, match='resolves to HEAD'):
            TL.refuse_self_base('HEAD', A, A, lambda: changes)


def test_golden_base_applies_the_self_base_rule(monkeypatch):
    calls = []
    monkeypatch.setenv('ZB_GOLDEN_BASE', 'HEAD')
    monkeypatch.setattr(TL, '_head_changes_pack', lambda: calls.append(1) or True)
    with pytest.raises(pytest.fail.Exception, match='resolves to HEAD'):
        TL.golden_base()
    assert calls == [1]


# ------------------------------------------------------------------ Cowork (e): records after CORR-0003 explain themselves
def test_entry_quality_after_the_pinned_prefix():
    good = dict(id='CORR-0004', ticket='AUD-07', rationale='x' * TL.RATIONALE_MIN)
    TL.check_entry_quality(good, 4)
    for t in ('AUD-07', 'NC-01', 'BT-02', 'PR #32', 'PR-32', 'AUD#7', 'fixes PR 33'):
        TL.check_entry_quality(dict(good, ticket=t), 5)
    for bad in (dict(good, rationale=''), dict(good, rationale=' ' * 80), dict(good, rationale='x' * (TL.RATIONALE_MIN - 1)),
                dict(good, rationale=None), dict(good, ticket=''), dict(good, ticket='misc'), dict(good, ticket='AUDIT'),
                dict(good, ticket=None)):
        with pytest.raises(AssertionError):
            TL.check_entry_quality(bad, 4)
    TL.check_entry_quality(dict(good, rationale='', ticket=''), 3)              # the pinned prefix is untouched
