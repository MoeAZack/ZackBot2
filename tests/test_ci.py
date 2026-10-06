"""T04d: faster CI. Parallel full slices must add up to exactly `verify.py full`, the merge must fail on anything missing,
failed or from another commit, and docs-only reuse must be fail-closed (any doubt -> the full gate runs)."""
import io, json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import verify as V                                                         # noqa: E402
import verify_ci as C                                                      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = open(os.path.join(ROOT, '.github', 'workflows', 'verify.yml'), encoding='utf-8').read()
A, B = 'a' * 40, 'b' * 40


# ---------------------------------------------------------------- slices == verify.py full
def test_the_slices_cover_exactly_the_full_level():
    flat = [s for steps in C.PARTS.values() for s in steps]
    assert sorted(flat) == sorted(C.FULL_STEPS) and len(flat) == len(set(flat)) == 4
    src = open(os.path.join(ROOT, 'verify.py'), encoding='utf-8').read()
    assert "pytest_step(rep, 'tests (all)', ['tests'], 3600)" in src and C.UI_STEP in src
    assert [n for n, _ in V.REPLAYS] == [C.FULL_STEPS[1], C.FULL_STEPS[2]]
    assert re.search(r"for name, args in REPLAYS: replay_step\(rep, name, args\)\s*\n\s*ui_step\(rep\)", src), 'verify.py full changed: update PARTS'


def test_each_slice_runs_only_its_own_steps(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(V, 'pytest_step', lambda rep, name, args, t: calls.append(name) or rep.step(name, True))
    monkeypatch.setattr(V, 'replay_step', lambda rep, name, args: calls.append(name) or rep.step(name, True))
    monkeypatch.setattr(V, 'ui_step', lambda rep: calls.append(C.UI_STEP) or rep.step(C.UI_STEP, True))
    for part, steps in C.PARTS.items():
        calls.clear()
        assert C.main(['part', part, '--out', str(tmp_path / part)]) == 0
        assert calls == steps, part
        s = json.load(open(tmp_path / part / f'latest_full-{part}.json'))
        assert [x['name'] for x in s['steps']] == steps and s['passed']


def test_replay1_and_ui_share_one_output_folder_so_the_harness_gets_this_runs_seeds(monkeypatch, tmp_path):
    outs = []
    monkeypatch.setattr(V, 'replay_step', lambda rep, name, args: outs.append(rep.out) or rep.step(name, True))
    monkeypatch.setattr(V, 'ui_step', lambda rep: outs.append(rep.out) or rep.step(C.UI_STEP, True))
    C.main(['part', 'replay1-ui', '--out', str(tmp_path / 'x')])
    assert len(outs) == 2 and outs[0] == outs[1]


# ---------------------------------------------------------------- merge = the required "verify full" check
def _slices(d, commit=A, drop=None, fail=None, extra=None, missing_step=None):
    for part, steps in C.PARTS.items():
        if part == drop: continue
        st = [dict(name=s, status='FAILED' if s == fail else 'passed', seconds=1.0) for s in steps if s != missing_step]
        os.makedirs(d / f'verify-full-{part}-{commit}', exist_ok=True)
        json.dump(dict(git=dict(commit=commit), steps=st, passed=fail not in steps, tests={'tests (all)': dict(passed=9)} if part == 'tests' else {}),
                  open(d / f'verify-full-{part}-{commit}' / f'latest_full-{part}.json', 'w'))
    if extra:
        os.makedirs(d / 'dup', exist_ok=True)
        json.dump(dict(git=dict(commit=commit), steps=[], passed=True), open(d / 'dup' / f'latest_full-{extra}.json', 'w'))


def _merge(monkeypatch, tmp_path, **kw):
    monkeypatch.setattr(V, 'git_info', lambda: dict(commit=A))
    monkeypatch.setattr(V, 'dependency_versions', lambda: {})
    parts = tmp_path / 'parts'; parts.mkdir()
    _slices(parts, **kw)
    rc = C.main(['merge', '--parts', str(parts), '--out', str(tmp_path / 'out')])
    return rc, json.load(open(tmp_path / 'out' / 'latest_full.json'))


def test_merge_passes_only_with_every_slice_on_this_commit(monkeypatch, tmp_path):
    rc, s = _merge(monkeypatch, tmp_path)
    names = [x['name'] for x in s['steps']]
    assert rc == 0 and s['passed'] and all(n in names for n in C.FULL_STEPS)
    assert s['level'] == 'full' and s['tests']['tests (all)']['passed'] == 9
    assert 'installer buildcheck' in names                         # same shape as verify.py full (skipped off Windows)


def test_merge_fails_on_a_missing_slice(monkeypatch, tmp_path):
    rc, s = _merge(monkeypatch, tmp_path, drop='replay2')
    assert rc == 1 and s['failed_steps'] == [V.REPLAYS[1][0]]


def test_merge_fails_on_a_failed_step(monkeypatch, tmp_path):
    rc, s = _merge(monkeypatch, tmp_path, fail=C.UI_STEP)
    assert rc == 1 and s['failed_steps'] == [C.UI_STEP]


def test_merge_fails_on_a_slice_from_another_commit(monkeypatch, tmp_path):
    rc, s = _merge(monkeypatch, tmp_path, commit=B)
    assert rc == 1 and set(s['failed_steps']) == set(C.FULL_STEPS)


def test_merge_fails_on_a_duplicate_slice_summary(monkeypatch, tmp_path):
    rc, s = _merge(monkeypatch, tmp_path, extra='tests')
    assert rc == 1 and s['failed_steps'] == ['tests (all)']


def test_merge_fails_on_a_missing_step(monkeypatch, tmp_path):
    rc, s = _merge(monkeypatch, tmp_path, missing_step=C.UI_STEP)
    assert rc == 1 and s['failed_steps'] == [C.UI_STEP]


# ---------------------------------------------------------------- docs-only reuse (fail-closed)
def test_docs_only_means_documents_under_docs():
    assert C.docs_only(['docs/PROJECT_STATUS.md', 'docs/reviews/T05_review_request.md'])
    for bad in ([], ['README.md'], ['docs/a.md', 'engine.py'], ['docsx/a.md'], ['docs/../engine.py'], ['docs/x.py'],
                ['docs/ci.yml'], ['docs/data.json'], ['docs/run.ps1'], ['.github/workflows/verify.yml']):
        assert not C.docs_only(bad), bad


def _repo(tmp_path):
    g = lambda *a: subprocess.run(['git', '-C', str(tmp_path)] + list(a), capture_output=True, text=True, check=True).stdout.strip()
    g('init', '-q'); g('config', 'user.email', 't@t'); g('config', 'user.name', 't')
    (tmp_path / 'engine.py').write_text('x=1\n'); os.makedirs(tmp_path / 'docs'); (tmp_path / 'docs' / 's.md').write_text('a\n')
    g('add', '-A'); g('commit', '-qm', 'base'); base = g('rev-parse', 'HEAD')
    (tmp_path / 'docs' / 's.md').write_text('b\n'); g('commit', '-qam', 'docs'); docs = g('rev-parse', 'HEAD')
    (tmp_path / 'engine.py').write_text('x=2\n'); g('commit', '-qam', 'code'); code = g('rev-parse', 'HEAD')
    g('checkout', '-q', '-b', 'side', base); (tmp_path / 'docs' / 's.md').write_text('c\n'); g('commit', '-qam', 'side docs')
    _repo.side = g('rev-parse', 'HEAD'); g('checkout', '-q', '-')
    return base, docs, code


def test_reuse_decision_is_fail_closed(monkeypatch, tmp_path):
    base, docs, code = _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(V, 'ROOT', str(tmp_path))
    real_run = V.run
    monkeypatch.setattr(V, 'run', lambda cmd, timeout, env=None, cwd=None: real_run(cmd, timeout, env=env, cwd=str(tmp_path)))
    ok = lambda sha, name: dict(url='u', check_run_id=1)
    assert C.decide('full', 'pull_request', base, docs, lookup=ok)['reuse'] is True
    assert C.decide('fast', 'push', base, docs, lookup=ok)['reuse'] is True
    assert not C.decide('full', 'pull_request', docs, code, lookup=ok)['reuse']          # code changed
    assert not C.decide('full', 'pull_request', base, code, lookup=ok)['reuse']          # docs + code since before
    assert not C.decide('full', 'workflow_dispatch', base, docs, lookup=ok)['reuse']     # on demand: always the full gate
    assert not C.decide('full', 'pull_request', C.ZERO, docs, lookup=ok)['reuse']        # new branch: no previous commit
    assert not C.decide('full', 'pull_request', '', docs, lookup=ok)['reuse']            # PR opened: no before
    assert not C.decide('full', 'pull_request', code, docs, lookup=ok)['reuse']          # not an ancestor (force-push)
    diff = subprocess.run(['git', '-C', str(tmp_path), 'diff', '--name-only', f'{_repo.side}..{docs}'], capture_output=True, text=True).stdout.split()
    assert diff == ['docs/s.md']                                                           # the sibling differs only in docs/...
    assert not C.decide('full', 'pull_request', _repo.side, docs, lookup=ok)['reuse']    # docs-only diff, but not an ancestor
    assert not C.decide('full', 'pull_request', 'c' * 40, docs, lookup=ok)['reuse']      # unknown commit
    assert not C.decide('full', 'pull_request', base, docs, lookup=lambda s, n: None)['reuse']   # the check did not pass there
    seen = []
    C.decide('fast', 'push', base, docs, lookup=lambda s, n: seen.append((s, n)))
    assert seen == [(base, 'verify fast')]                                                # the SAME check, on before


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _opener(runs, boom=False):
    def op(req, timeout):
        if boom: raise OSError('network down')
        assert req.headers['Authorization'] == 'Bearer tok' and 'check_name=verify%20full' in req.full_url
        return _Resp(json.dumps(dict(check_runs=runs)).encode())
    return op


def test_prior_success_requires_a_completed_successful_github_actions_run_on_that_commit():
    good = dict(name='verify full', head_sha=A, status='completed', conclusion='success', app=dict(slug='github-actions'), id=7, html_url='h')
    call = lambda runs, **k: C.prior_success(A, 'verify full', repo='o/r', token='tok', opener=_opener(runs, **k))
    assert call([good])['check_run_id'] == 7
    for bad in (dict(good, conclusion='failure'), dict(good, conclusion='skipped'), dict(good, status='in_progress'),
                dict(good, head_sha=B), dict(good, name='verify fast'), dict(good, app=dict(slug='someone-else')), dict(good, app=None)):
        assert call([bad]) is None, bad
    assert call([good], boom=True) is None
    assert C.prior_success(A, 'verify full', repo='o/r', token=None, opener=_opener([good])) is None
    assert C.prior_success('nope', 'verify full', repo='o/r', token='tok', opener=_opener([good])) is None


def test_reused_rechecks_and_labels_every_skipped_step(monkeypatch, tmp_path):
    monkeypatch.setattr(V, 'git_info', lambda: dict(commit=B))
    monkeypatch.setattr(V, 'dependency_versions', lambda: {})
    yes = dict(reuse=True, why='docs', reused_from=A, prior=dict(url='https://run/1'), files=['docs/s.md'])
    monkeypatch.setattr(C, 'decide', lambda *a, **k: yes)
    assert C.main(['reused', 'full', '--event', 'pull_request', '--before', A, '--head', B, '--out', str(tmp_path / 'y')]) == 0
    s = json.load(open(tmp_path / 'y' / 'latest_full.json'))
    skipped = {x['name']: x for x in s['steps'] if x['status'] == 'skipped'}
    assert set(skipped) == set(C.FULL_STEPS) | {'installer buildcheck'}
    assert all(x['detail'].startswith('REUSED') and A[:12] in x['detail'] and x['reused_from'] == A for x in skipped.values())
    assert any(x['name'].startswith('static:') and x['status'] == 'passed' for x in s['steps'])      # cheap checks really ran
    monkeypatch.setattr(C, 'decide', lambda *a, **k: dict(reuse=False, why='code or config changed'))
    assert C.main(['reused', 'full', '--event', 'pull_request', '--before', A, '--head', B, '--out', str(tmp_path / 'n')]) == 1


def test_plan_writes_both_decisions_for_the_workflow(monkeypatch, tmp_path):
    out = tmp_path / 'gh_out'
    monkeypatch.setenv('GITHUB_OUTPUT', str(out))
    monkeypatch.setattr(C, 'decide', lambda lvl, *a, **k: dict(reuse=lvl == 'fast', why='x'))
    assert C.main(['plan', '--event', 'push', '--before', A, '--head', B]) == 0
    assert out.read_text().splitlines() == ['reuse_fast=true', 'reuse_full=false']


def test_nothing_executed_reads_docs():
    """The reuse rule rests on this: no code, script, page or workflow outside docs/ refers to docs/ (verify_ci + tests aside)."""
    allowed = {os.path.join(ROOT, 'verify_ci.py'), os.path.join(ROOT, 'tests', 'test_ci.py')}
    hits = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in ('.git', 'docs', 'data', 'data1h', 'data_long', 'research', 'dev_out', 'verify_out', '__pycache__')]
        for fn in fns:
            p = os.path.join(dp, fn)
            if p in allowed or not fn.lower().endswith(('.py', '.ps1', '.bat', '.html', '.js', '.spec')): continue
            txt = open(p, encoding='utf-8', errors='ignore').read()
            if re.search(r"""['"/\\]docs['"/\\]""", txt): hits.append(os.path.relpath(p, ROOT))
    assert hits == []


# ---------------------------------------------------------------- the workflow wiring
def test_workflow_keeps_the_required_check_names_and_wires_the_slices():
    jobs = re.findall(r'^    name: (.+)$', WF, re.M)                                    # job-level names (4-space indent)
    assert jobs.count('verify fast') == 1 and jobs.count('verify full') == 1, jobs          # branch protection names
    assert 'part: [tests, replay2, replay1-ui]' in WF and set(C.PARTS) == {'tests', 'replay2', 'replay1-ui'}
    assert 'fail-fast: false' in WF
    full = WF.split('\n  full:\n')[1]
    assert 'needs: [plan, full-part]' in full and 'always() &&' in full                    # runs (and fails) even if a slice failed
    assert 'python verify_ci.py merge --parts parts --out verify_out' in full
    assert "if: needs.plan.outputs.reuse_full != 'true'" in full and "if: needs.plan.outputs.reuse_full == 'true'" in full
    assert 'workflow_dispatch' in WF and "inputs.level == 'full'" in full
    fast = WF.split('\n  fast:\n')[1].split('\n  full-part:\n')[0]
    assert 'python verify.py fast --out verify_out' in fast and "reuse_fast != 'true'" in fast and 'always()' in fast
    assert 'needs: fast' not in WF                                                           # full no longer waits for fast
    assert 'permissions:\n  contents: read\n  checks: read\n' in WF and 'write' not in WF.split('jobs:')[0]
    assert re.search(r'uses:\s*actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c', WF)
