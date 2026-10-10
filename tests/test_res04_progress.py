"""R4 sanitized progress record (tools/research/progress.py, Codex 6094685201): whitelist refusal, no outcome
leak, determinism, append-only chain, and the run_r4 m3 wiring. Synthetic stubs only: no data is read."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import m3_repro as X                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import report as R                                                                          # noqa: E402
import run_r4 as G                                                                          # noqa: E402

COMMIT = 'a' * 40
DS = {'manifest': 'research_evidence/manifests/legacy-unverified-v1.json', 'digest': 'b' * 64}
CFG = {'rows': ['base', '2x'], 'perturb': True}


def new(tmp, **kw):
    return PG.Progress(str(tmp), commit=COMMIT, dataset=DS, config=CFG, clock=kw.pop('clock', None), **kw)


def run_all(tmp):
    p = new(tmp)
    art = tmp / 'm3_repro_base.json'
    art.write_text('{"pnl": 12.5, "sharpe": 1.3}\n')
    p.stage_start('m3.open')
    p.stage_done('m3.open')
    p.stage_start('walk_forward', fold=2)
    p.integrity_failure(['DataGap'], stage='walk_forward', fold=2)
    p.stage_done('walk_forward', fold=2, artifacts=[str(art)])
    p.sealed([str(art)], ['m3_repro_base.json'])
    return p.path


def rehash(rec):
    rec = dict(rec)
    rec['digest'] = PG.record_digest(rec)
    return rec


def base_rec(**over):
    rec = dict(format=PG.FORMAT, seq=1, prev='0' * 64, event='stage_start', run='0' * 16,
               stage={'name': 'm3.base'})
    rec.update(over)
    return rehash({k: v for k, v in rec.items() if v is not None})


# ------------------------------------------------------------------ whitelist refusal
@pytest.mark.parametrize('extra', [{'note': 'x'}, {'token': 'abc'}, {'api_key': 'k'}, {'account': 'acct'},
                                   {'commit': COMMIT}])          # commit only on run_start
def test_unknown_or_misplaced_top_level_keys_are_refused(extra):
    with pytest.raises(PG.ProgressError, match='whitelist'):
        PG.check_record(base_rec(**extra))


@pytest.mark.parametrize('stage', [{'name': 'm3.base', 'extra': 'x'}, {'name': 'm3.base', 'fold': 1, 'n': 2}])
def test_nested_unknown_keys_are_refused(stage):
    with pytest.raises(PG.ProgressError, match='whitelist'):
        PG.check_record(base_rec(stage=stage))


def test_unknown_event_and_bad_shapes_are_refused():
    for rec in (base_rec(event='metrics'), base_rec(run='xyz'), base_rec(prev='z' * 64),
                base_rec(stage={'name': '../etc'}), base_rec(wall='yesterday')):
        with pytest.raises(PG.ProgressError):
            PG.check_record(rec)
    bad = base_rec()
    bad['stage'] = {'name': 'm3.2x'}                  # edited after hashing
    with pytest.raises(PG.ProgressError, match='digest'):
        PG.check_record(bad)


# ------------------------------------------------------------------ no outcome leak
@pytest.mark.parametrize('key', ['pnl', 'score', 'return', 'sharpe', 'equity', 'drawdown', 'win_rate',
                                 'mean_r', 'expectancy', 'profit'])
def test_outcome_keys_are_refused_at_every_depth(key):
    for rec in (base_rec(**{key: 1.5}), base_rec(stage={'name': 'm3.base', key: 2})):
        with pytest.raises(PG.ProgressError):
            PG.check_record(rec)


@pytest.mark.parametrize('name', ['pnl_ok', 'score.high', 'NET_R', 'sharpe:2', 'equity_peak', 'DRAWDOWN_8',
                                  'WIN_RATE', 'verdict.PROMOTE', 'return'])
def test_outcome_words_are_refused_in_codes_and_stage_names(name):
    with pytest.raises(PG.ProgressError, match='outcome-free'):
        PG.check_record(base_rec(stage={'name': name}))
    with pytest.raises(PG.ProgressError, match='outcome-free'):
        PG.check_record(base_rec(event='integrity_failure', stage=None, integrity=[name]))


@pytest.mark.parametrize('fold', [1.5, True, -1, '2'])
def test_only_seq_and_fold_may_be_numbers(fold):
    with pytest.raises(PG.ProgressError, match='fold'):
        PG.check_record(base_rec(stage={'name': 'walk_forward', 'fold': fold}))


def test_operational_names_are_accepted():
    for n in ('m3.open', 'm3.base', 'm3.2x', 'walk_forward', 'calibration', 'pit.window', 'add_rows'):
        PG.check_record(base_rec(stage={'name': n}))
    PG.check_record(base_rec(event='integrity_failure', stage={'name': 'm3.base'},
                             integrity=['ManifestError', 'WINDOW_GAP', 'GateError']))


def test_a_full_run_file_carries_no_outcome(tmp_path):
    path = run_all(tmp_path)
    text = open(path, encoding='ascii').read()
    assert 'pnl' not in text and 'sharpe' not in text and '12.5' not in text
    recs = PG.verify(path)
    assert [r['event'] for r in recs] == ['run_start', 'stage_start', 'stage_done', 'stage_start',
                                         'integrity_failure', 'stage_done', 'run_sealed']
    assert recs[-1]['artifacts'] == [{'path': 'm3_repro_base.json',
                                      'sha256': PG.file_sha256(str(tmp_path / 'm3_repro_base.json'))}]


# ------------------------------------------------------------------ determinism + append-only
def test_two_identical_runs_are_byte_identical_without_wall(tmp_path):
    a, b = run_all(tmp_path / 'a'), run_all(tmp_path / 'b')
    assert os.path.basename(a) == os.path.basename(b)
    assert open(a, 'rb').read() == open(b, 'rb').read()


def test_wall_time_is_the_only_difference_and_is_outside_the_chain(tmp_path):
    ticks = iter(['2026-10-10T14:00:00+03:00', '2026-10-10T14:00:01+03:00', '2026-10-10T15:00:00+03:00'])
    p = new(tmp_path / 'w', clock=lambda: next(ticks))
    p.stage_start('m3.open')
    p.aborted(['ReproError'], stage='m3.open')
    q = new(tmp_path / 'n')
    q.stage_start('m3.open')
    q.aborted(['ReproError'], stage='m3.open')
    rw, rn = PG.verify(p.path), PG.verify(q.path)
    assert [r.pop('wall') for r in rw][0].endswith('+03:00')
    assert rw == rn


def test_run_id_depends_on_commit_dataset_and_config_only(tmp_path):
    assert PG.run_id(COMMIT, DS, CFG) == PG.run_id(COMMIT, dict(DS), dict(CFG))
    assert PG.run_id(COMMIT, DS, CFG) != PG.run_id('c' * 40, DS, CFG)
    assert PG.run_id(COMMIT, DS, CFG) != PG.run_id(COMMIT, DS, dict(CFG, perturb=False))


def test_terminal_run_refuses_more_records_and_tampering_is_caught(tmp_path):
    path = run_all(tmp_path)
    with pytest.raises(PG.ProgressError, match='already run_sealed'):
        new(tmp_path)
    lines = open(path, 'rb').read().splitlines(keepends=True)
    with open(path, 'wb') as f:                      # drop a middle record: the chain breaks
        f.writelines(lines[:2] + lines[3:])
    with pytest.raises(PG.ProgressError, match='chain'):
        PG.verify(path)


def test_an_unfinished_run_resumes_on_the_same_chain(tmp_path):
    p = new(tmp_path)
    p.stage_start('m3.open')
    q = new(tmp_path)                                # e.g. after a crash: no second run_start
    q.stage_done('m3.open')
    q.sealed([], [])
    assert [r['seq'] for r in PG.verify(q.path)] == [0, 1, 2, 3]


# ------------------------------------------------------------------ run_r4 m3 wiring (stubs; gate bypassed)
def _stub_m3(monkeypatch, *, fail_open=False):
    monkeypatch.setattr(G, 'refuse_unless_open', lambda repo, **kw: None)
    monkeypatch.setattr(R, 'eval_identity', lambda repo, paths, config: {'files': sorted(paths)})

    def open_legacy(*a, **k):
        if fail_open:
            raise X.ReproError('manifest SHA-256 mismatch')
        return SimpleNamespace(digest=X.LEGACY_DIGEST, labels=('survivor-only',)), object(), 0, 4 * 3_600_000

    monkeypatch.setattr(X, 'open_legacy', open_legacy)
    monkeypatch.setattr(X, 'load_rules', lambda *a, **k: {})
    monkeypatch.setattr(X, 'replay', lambda *a, **k: None)
    monkeypatch.setattr(X, 'read_reference', lambda repo, name: '')
    monkeypatch.setattr(X, 'report', lambda **k: {'row': k['name'], 'pnl': '123.45', 'sharpe': '9.9'})


def test_m3_writes_a_sealed_outcome_free_progress_record(monkeypatch, tmp_path):
    _stub_m3(monkeypatch)
    outs = []
    for d in ('a', 'b'):
        G.run_m3(ROOT, store=str(tmp_path), rows=('base', '2x'), out_dir=str(tmp_path / d), author='t',
                 cairo_date='2026-10-10', clock=None)
        (prog,) = [f for f in os.listdir(tmp_path / d) if f.startswith('progress-')]
        outs.append(open(tmp_path / d / prog, 'rb').read())
    assert outs[0] == outs[1]
    recs = [json.loads(x) for x in outs[0].splitlines()]
    assert recs[0]['commit'] == R.code_identity(ROOT)['git_head']
    assert recs[0]['dataset'] == {'manifest': X.LEGACY_MANIFEST, 'digest': X.LEGACY_DIGEST}
    assert [r['stage']['name'] for r in recs if r['event'] == 'stage_done'] == ['m3.open', 'm3.base', 'm3.2x']
    assert recs[-1]['event'] == 'run_sealed'
    assert recs[-1]['evidence'] == ['m3_repro_2x.json', 'm3_repro_base.json']
    assert b'123.45' not in outs[0] and b'pnl' not in outs[0]


def test_m3_failure_is_recorded_as_an_abort_with_a_code_only(monkeypatch, tmp_path):
    _stub_m3(monkeypatch, fail_open=True)
    with pytest.raises(X.ReproError):
        G.run_m3(ROOT, store=str(tmp_path), rows=('base',), out_dir=str(tmp_path), author='t',
                 cairo_date='2026-10-10', clock=None)
    (prog,) = [f for f in os.listdir(tmp_path) if f.startswith('progress-')]
    last = PG.verify(str(tmp_path / prog))[-1]
    assert last['event'] == 'run_aborted' and last['integrity'] == ['ReproError']
    assert last['stage'] == {'name': 'm3.open'} and 'mismatch' not in open(tmp_path / prog).read()
