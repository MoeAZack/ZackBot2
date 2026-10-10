"""R4 sanitized progress record (tools/research/progress.py, zb-run-progress/2; Codex 6094685201, 6094780810 Q2/Q3):
one atomically replaced live snapshot + one immutable sealed final, identity digests, whitelist refusal, no outcome
leak, wall time outside every digest, the run_r4 m3 wiring and the R4-0 readiness report. Synthetic stubs only: no
data is read."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import m3_repro as X                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import r4_readiness as RR                                                                   # noqa: E402
import report as R                                                                          # noqa: E402
import run_r4 as G                                                                          # noqa: E402

IDENT = {'commit': 'a' * 40, 'evaluator': 'e' * 64, 'manifest': 'b' * 64, 'universe': None, 'classification': None,
         'prereg': 'f' * 64}
CFG = {'rows': ['base', '2x'], 'perturb': True}


def new(tmp, **kw):
    return PG.Progress(str(tmp), identity=kw.pop('identity', IDENT), config=CFG, clock=kw.pop('clock', None))


def run_all(tmp):
    p = new(tmp)
    art = tmp / 'm3_repro_base.json'
    art.write_text('{"pnl": 12.5, "sharpe": 1.3}\n')
    p.stage_start('m3.open')
    p.stage_done('m3.open')
    p.stage_start('pit.walk_forward', fold=2)
    p.integrity_failure(['DataGap'], stage='pit.walk_forward', fold=2)
    p.stage_done('pit.walk_forward', fold=2, artifacts=[str(art)])
    p.sealed([str(art)], ['m3_repro_base.json'])
    return p


def live(**over):
    doc = dict(format=PG.FORMAT, kind='live', run='0' * 16, seq=1, state='running', identity=dict(IDENT),
               identity_digest=PG.identity_digest(IDENT), stages=[{'name': 'm3.base', 'status': 'started'}],
               integrity=[])
    doc.update(over)
    doc = {k: v for k, v in doc.items() if v is not None}
    doc['digest'] = PG.doc_digest(doc)
    return doc


# ------------------------------------------------------------------ whitelist refusal + identity
@pytest.mark.parametrize('extra', [{'note': 'x'}, {'token': 'abc'}, {'api_key': 'k'}, {'account': 'acct'},
                                   {'artifacts': []}])         # artifacts only on the final
def test_unknown_or_misplaced_top_level_keys_are_refused(extra):
    with pytest.raises(PG.ProgressError, match='whitelist'):
        PG.check_doc(live(**extra))


@pytest.mark.parametrize('stage', [{'name': 'm3.base', 'status': 'done', 'extra': 'x'},
                                   {'name': 'm3.base', 'status': 'done', 'fold': 1, 'n': 2}])
def test_nested_unknown_keys_are_refused(stage):
    with pytest.raises(PG.ProgressError, match='whitelist'):
        PG.check_doc(live(stages=[stage]))


@pytest.mark.parametrize('bad', [dict(IDENT, commit='abc'), dict(IDENT, evaluator=None), dict(IDENT, prereg='x'),
                                 dict(IDENT, token='t'), {k: v for k, v in IDENT.items() if k != 'classification'}])
def test_identity_shape_is_strict(bad):
    with pytest.raises(PG.ProgressError):
        PG.check_doc(live(identity=bad, identity_digest=PG.identity_digest(bad)))
    with pytest.raises(PG.ProgressError):
        PG.Progress('unused', identity=bad, config=CFG, clock=None)


def test_bad_shapes_and_tampering_are_refused():
    for doc in (live(kind='log'), live(run='xyz'), live(state='done'), live(stages=[{'name': '../etc',
                                                                                     'status': 'done'}]),
                live(wall='yesterday'), live(identity_digest='0' * 64)):
        with pytest.raises(PG.ProgressError):
            PG.check_doc(doc)
    bad = live()
    bad['stages'] = [{'name': 'm3.2x', 'status': 'started'}]          # edited after hashing
    with pytest.raises(PG.ProgressError, match='digest'):
        PG.check_doc(bad)


# ------------------------------------------------------------------ no outcome leak
@pytest.mark.parametrize('key', ['pnl', 'score', 'return', 'sharpe', 'equity', 'drawdown', 'win_rate',
                                 'mean_r', 'expectancy', 'profit'])
def test_outcome_keys_are_refused_at_every_depth(key):
    for doc in (live(**{key: 1.5}), live(stages=[{'name': 'm3.base', 'status': 'done', key: 2}]),
                live(integrity=[{'code': 'X', key: 1}])):
        with pytest.raises(PG.ProgressError):
            PG.check_doc(doc)


@pytest.mark.parametrize('name', ['pnl_ok', 'score.high', 'NET_R', 'sharpe:2', 'equity_peak', 'DRAWDOWN_8',
                                  'WIN_RATE', 'verdict.PROMOTE', 'return'])
def test_outcome_words_are_refused_in_codes_and_stage_names(name):
    with pytest.raises(PG.ProgressError, match='allowlisted'):
        PG.check_doc(live(stages=[{'name': name, 'status': 'started'}]))
    with pytest.raises(PG.ProgressError, match='allowlisted'):
        PG.check_doc(live(integrity=[{'code': name}]))


# Cowork 6095679832 F2: interim-outcome codes a denylist let through; the fixed allowlists refuse every one.
COWORK_F2 = ['gate_cost_failed', 'pass_cost_gate', 'beats_b0', 'best_fold', 'positive', 'wallet', 'nav', 'roi',
             'p_value', 'significant']


@pytest.mark.parametrize('name', COWORK_F2 + ['m3.open2', 'M3.OPEN', 'walk_forward', 'Custom', 'GateErrorX',
                                              'ReproError ', ''])
def test_unknown_stage_and_integrity_codes_refuse(name, tmp_path):
    with pytest.raises(PG.ProgressError, match='allowlisted'):
        PG.check_doc(live(stages=[{'name': name, 'status': 'started'}]))
    with pytest.raises(PG.ProgressError, match='allowlisted'):
        PG.check_doc(live(integrity=[{'code': name}]))
    with pytest.raises(PG.ProgressError, match='allowlisted'):
        PG.check_doc(live(integrity=[{'code': 'DataGap', 'stage': name}]))
    p = new(tmp_path)
    with pytest.raises(PG.ProgressError, match='allowlisted'):
        p.stage_start(name)
    with pytest.raises(PG.ProgressError, match='allowlisted'):
        p.integrity_failure([name])
    assert PG.read(p.path)['stages'] == [] and PG.read(p.path)['integrity'] == []     # nothing written


def test_allowlists_are_outcome_free_and_exceptions_map_onto_them():
    for c in PG.STAGE_NAMES + PG.INTEGRITY_CODES:
        assert PG.CODE.match(c) and not PG.OUTCOME.search(c)
        assert not any(w in c.lower() for w in COWORK_F2)
    PG.check_doc(live(stages=[{'name': n, 'status': 'done'} for n in PG.STAGE_NAMES],
                      integrity=[{'code': c} for c in PG.INTEGRITY_CODES]))
    assert PG.integrity_code('ReproError') == 'ReproError'
    assert PG.integrity_code('LedgerError') == 'FamilyLogRefused'
    assert PG.integrity_code('GapUnderPositionError') == 'GapUnderPositionError'
    for other in ('KeyError', 'ZeroDivisionError', 'beats_b0', 'pnl'):
        assert PG.integrity_code(other) == 'Unexpected'


@pytest.mark.parametrize('fold', [1.5, True, -1, '2'])
def test_only_seq_and_fold_may_be_numbers(fold):
    with pytest.raises(PG.ProgressError, match='fold'):
        PG.check_doc(live(stages=[{'name': 'pit.walk_forward', 'status': 'started', 'fold': fold}]))


def test_a_full_run_carries_no_outcome_and_seals_once(tmp_path):
    p = run_all(tmp_path)
    for path in (p.path, p.final_path):
        text = open(path, encoding='ascii').read()
        assert 'pnl' not in text and 'sharpe' not in text and '12.5' not in text
    snap, final = PG.read(p.path), PG.read(p.final_path)
    assert snap['state'] == final['state'] == 'sealed' and final['kind'] == 'final'
    assert [(s['name'], s['status']) for s in final['stages']] == [('m3.open', 'done'), ('pit.walk_forward', 'done')]
    assert final['integrity'] == [{'code': 'DataGap', 'stage': 'pit.walk_forward', 'fold': 2}]
    assert final['artifacts'] == [{'path': 'm3_repro_base.json',
                                   'sha256': PG.file_sha256(str(tmp_path / 'm3_repro_base.json'))}]
    assert final['identity'] == IDENT and final['identity_digest'] == PG.identity_digest(IDENT)
    assert sorted(os.listdir(tmp_path)) == sorted(['m3_repro_base.json', os.path.basename(p.path),
                                                   os.path.basename(p.final_path)])     # no temp file left


# ------------------------------------------------------------------ atomic snapshot, immutable final, determinism
def test_two_identical_runs_are_byte_identical_without_wall(tmp_path):
    a, b = run_all(tmp_path / 'a'), run_all(tmp_path / 'b')
    assert os.path.basename(a.path) == os.path.basename(b.path)
    for x, y in ((a.path, b.path), (a.final_path, b.final_path)):
        assert open(x, 'rb').read() == open(y, 'rb').read()


def test_wall_time_is_excluded_from_identity_and_digests(tmp_path):
    ticks = iter(f'2026-10-10T14:00:0{i}+03:00' for i in range(9))
    p = new(tmp_path / 'w', clock=lambda: next(ticks))
    p.stage_start('m3.open')
    p.aborted(['ReproError'], stage='m3.open')
    q = new(tmp_path / 'n')
    q.stage_start('m3.open')
    q.aborted(['ReproError'], stage='m3.open')
    fw, fn = PG.read(p.final_path), PG.read(q.final_path)
    assert fw.pop('wall').endswith('+03:00')
    assert fw == fn and fn['state'] == 'aborted'


def test_run_id_depends_on_identity_and_config_only():
    assert PG.run_id(IDENT, CFG) == PG.run_id(dict(IDENT), dict(CFG))
    for k in ('commit', 'evaluator', 'manifest', 'prereg'):
        assert PG.run_id(IDENT, CFG) != PG.run_id(dict(IDENT, **{k: 'c' * (40 if k == 'commit' else 64)}), CFG)
    assert PG.run_id(IDENT, CFG) != PG.run_id(dict(IDENT, classification='d' * 64), CFG)
    assert PG.run_id(IDENT, CFG) != PG.run_id(IDENT, dict(CFG, perturb=False))


def test_sealed_final_is_immutable_and_tampering_is_caught(tmp_path):
    p = run_all(tmp_path)
    with pytest.raises(PG.ProgressError, match='already sealed'):
        new(tmp_path)
    with pytest.raises(PG.ProgressError, match='already exists'):
        PG.write_once(p.final_path, b'{}\n')
    raw = open(p.final_path, 'rb').read()
    with open(p.final_path, 'wb') as f:
        f.write(raw.replace(b'"DataGap"', b'"OSError"'))
    with pytest.raises(PG.ProgressError, match='digest'):
        PG.read(p.final_path)


def test_snapshot_replace_is_atomic_on_failure(tmp_path, monkeypatch):
    p = new(tmp_path)
    p.stage_start('m3.open')
    before = open(p.path, 'rb').read()

    def boom(src, dst):
        raise OSError('disk full')
    monkeypatch.setattr(PG.os, 'replace', boom)
    with pytest.raises(OSError):
        p.stage_done('m3.open')
    monkeypatch.undo()
    assert open(p.path, 'rb').read() == before                       # the previous snapshot, never a torn one
    assert [f for f in os.listdir(tmp_path) if f.endswith('.tmp')] == []


def test_an_unfinished_run_resumes_from_the_snapshot(tmp_path):
    p = new(tmp_path)
    p.stage_start('m3.open')
    q = new(tmp_path)                                # e.g. after a crash
    q.stage_done('m3.open')
    q.sealed([], [])
    final = PG.read(q.final_path)
    assert final['seq'] == 4 and final['stages'] == [{'name': 'm3.open', 'status': 'done'}]
    r = new(tmp_path / 'x')
    r.stage_start('m3.open')
    other = dict(IDENT, prereg='9' * 64)
    os.replace(r.path, str(tmp_path / 'x' / f'progress-{PG.run_id(other, CFG)}.json'))   # a planted snapshot
    with pytest.raises(PG.ProgressError, match='another identity'):
        new(tmp_path / 'x', identity=other)


# ------------------------------------------------------------------ run_r4 m3 wiring (stubs; gate bypassed)
def _stub_m3(monkeypatch, *, fail_open=False):
    monkeypatch.setattr(G, 'refuse_unless_open', lambda repo, **kw: None)
    monkeypatch.setattr(G, 'm3_identity', lambda repo, rows, times, config, pre: dict(IDENT, commit=R.code_identity(
        repo)['git_head'], manifest=X.REPRO_DIGEST, prereg=pre))
    lo, hi = X.span(X.P.Dataset(X.M.load(os.path.join(ROOT, X.REPRO_MANIFEST)), ROOT, None))

    def open_repro(*a, **k):
        if fail_open:
            raise X.ReproError('manifest SHA-256 mismatch')
        return SimpleNamespace(digest=X.REPRO_DIGEST, labels=('SURVIVOR-ONLY', 'REPRO-ONLY')), object(), lo, hi

    monkeypatch.setattr(X, 'open_repro', open_repro)
    monkeypatch.setattr(X, 'replay', lambda *a, **k: None)
    monkeypatch.setattr(X, 'read_reference', lambda repo, name: '')
    monkeypatch.setattr(X, 'report', lambda **k: {'row': k['name'], 'pnl': '123.45', 'sharpe': '9.9'})


def test_m3_writes_a_sealed_outcome_free_progress_record(monkeypatch, tmp_path):
    _stub_m3(monkeypatch)
    outs = []
    for d in ('a', 'b'):
        G.run_m3(ROOT, store=ROOT, rows=('base', '2x'), out_dir=str(tmp_path / d), author='t',
                 cairo_date='2026-10-10', clock=None)
        (fin,) = [f for f in os.listdir(tmp_path / d) if f.startswith('final-')]
        outs.append(open(tmp_path / d / fin, 'rb').read())
        assert json.loads(open(tmp_path / d / 'm3_repro_base.json').read())['row'] == 'base'
    assert outs[0] == outs[1]
    final = json.loads(outs[0])
    assert final['identity']['commit'] == R.code_identity(ROOT)['git_head']
    assert final['identity']['manifest'] == X.REPRO_DIGEST and final['identity']['universe'] is None
    assert [s['name'] for s in final['stages'] if s['status'] == 'done'] == ['m3.open', 'm3.base', 'm3.2x']
    assert final['state'] == 'sealed' and final['evidence'] == ['m3_repro_2x.json', 'm3_repro_base.json']
    assert b'123.45' not in outs[0] and b'pnl' not in outs[0]


def test_m3_failure_is_recorded_as_an_abort_with_a_code_only(monkeypatch, tmp_path):
    _stub_m3(monkeypatch, fail_open=True)
    with pytest.raises(X.ReproError):
        G.run_m3(ROOT, store=ROOT, rows=('base',), out_dir=str(tmp_path), author='t', cairo_date='2026-10-10',
                 clock=None)
    (fin,) = [f for f in os.listdir(tmp_path) if f.startswith('final-')]
    final = PG.read(str(tmp_path / fin))
    assert final['state'] == 'aborted' and final['integrity'] == [{'code': 'ReproError', 'stage': 'm3.open'}]
    assert 'mismatch' not in open(tmp_path / fin).read()


def test_m3_identity_binds_the_sandboxed_evaluator_and_digests():
    lo, hi = X.span(X.P.Dataset(X.M.load(os.path.join(ROOT, X.REPRO_MANIFEST)), ROOT, None))
    times = X.schedule(lo, hi)
    a = G.m3_identity(ROOT, ('base', '2x'), times, {'k': 1}, 'f' * 64)
    PG.check_identity(a)
    assert a['manifest'] == X.REPRO_DIGEST and a['universe'] is None and a['prereg'] == 'f' * 64
    assert a['evaluator'] != G.m3_identity(ROOT, ('base',), times, {'k': 1}, 'f' * 64)['evaluator']
    assert a['evaluator'] != G.m3_identity(ROOT, ('base', '2x'), times[:-1], {'k': 1}, 'f' * 64)['evaluator']


# ------------------------------------------------------------------ R4-0 readiness
FULL = {'head': 'a' * 40,
        'digests': {'dataset': 'b' * 64, 'universe': 'c' * 64, 'classification': 'd' * 64, 'prereg': 'f' * 64},
        'tests': {'passed': 10, 'failed': 0}, 'm3_parity': {'status': 'PASS'},
        'accounting_sample': {'status': 'PASS'}, 'pilot': {'status': 'PASS'},
        'items': {str(n): {'status': 'PASS'} for n in RR.ITEMS}}


def test_readiness_defaults_to_not_ready_with_every_item_missing():
    doc = RR.build({})
    assert doc['verdict'] == 'NOT READY' and {i['status'] for i in doc['items'].values()} == {'MISSING'}
    assert {'head', 'digest.classification', 'tests', 'm3_parity', 'pilot', 'item.6'} <= set(doc['not_passing'])
    with pytest.raises(RR.ReadinessError):
        RR.build({'items': {'8': {'status': 'PASS'}}})
    with pytest.raises(RR.ReadinessError):
        RR.build({'pilot': {'status': 'GOOD'}})
    assert RR.build(dict(FULL, digests=dict(FULL['digests'], classification='PENDING')))['verdict'] == 'NOT READY'


@pytest.mark.parametrize('drop', ['head', 'digests', 'tests', 'm3_parity', 'accounting_sample', 'pilot', 'items'])
def test_any_missing_input_is_not_ready(tmp_path, drop):
    a, b = run_all(tmp_path / 'a'), run_all(tmp_path / 'b')
    ev = dict(FULL, final_files=[a.final_path, b.final_path])
    assert RR.build(ev)['verdict'] == 'READY'
    ev.pop(drop)
    assert RR.build(ev)['verdict'] == 'NOT READY'
    assert RR.build(dict(FULL, final_files=[a.final_path, b.final_path],
                         tests={'passed': 10, 'failed': 1}))['verdict'] == 'NOT READY'


def test_readiness_determinism_from_sealed_finals(tmp_path):
    a, b = run_all(tmp_path / 'a'), run_all(tmp_path / 'b')
    doc = RR.build(dict(FULL, final_files=[a.final_path, b.final_path]))
    assert doc['verdict'] == 'READY' and doc['determinism_sha256']['m3_repro_base.json']
    (tmp_path / 'c').mkdir()
    art = tmp_path / 'c' / 'm3_repro_base.json'
    art.write_text('{"changed": 1}' + chr(10))
    c = new(tmp_path / 'c')
    c.sealed([str(art)], ['m3_repro_base.json'])
    doc = RR.build(dict(FULL, final_files=[a.final_path, c.final_path]))
    assert doc['items']['6']['status'] == 'FAIL' and doc['verdict'] == 'NOT READY'
    assert RR.build(dict(FULL, final_files=[a.final_path]))['verdict'] == 'NOT READY'
    with pytest.raises(RR.ReadinessError, match='sealed final'):
        RR.build(dict(FULL, final_files=[a.path, b.final_path]))


def test_offline_readiness_on_this_tree_is_not_ready(tmp_path):
    """The real committed inputs: digests recomputed, holdout unread, classification PENDING -> NOT READY."""
    ev = RR.offline(ROOT)
    assert ev['digests']['classification'] == 'PENDING'
    assert ev['digests']['universe'] == G.json.loads(G._load(ROOT, G.UNIVERSE))['digest']
    off = ev['offline']
    assert off['repro_manifest']['pinned'] and not off['repro_manifest']['promotion_eligible']
    assert off['universe']['recomputed_ok'] and off['universe']['matches_prereg']
    assert off['archive_manifest']['files'] == 103659 and off['archive_manifest']['matches_prereg']
    assert ev['items']['3']['holdout_unread'] is True
    cov = off['m3_coverage']                                     # F1: metadata proof offline, bytes = Windows step
    assert cov['mode'] == 'metadata' and cov['zero_missing'] and cov['expected_per_series'] == 10499
    assert sorted(cov['series']) == sorted(X.CORE8) and ev['items']['2']['status'] == 'MISSING'
    assert '--store' in ev['items']['2']['remaining']
    doc = RR.build(ev)
    assert doc['verdict'] == 'NOT READY' and 'digest.classification' in doc['not_passing']
    assert RR.main(['--out', str(tmp_path / 'r.json'), '--repo', ROOT]) == 1
    assert json.load(open(tmp_path / 'r.json'))['verdict'] == 'NOT READY'
