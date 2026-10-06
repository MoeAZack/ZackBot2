"""T04d: faster CI. The parallel slices must be an exact partition of verify.FULL_PLAN, the merge must reject every
deviation, required checks must never be skipped or reused, and push-only docs reuse must be fail-closed."""
import io, json, os, re, subprocess, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import verify as V                                                         # noqa: E402
import verify_ci as C                                                      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = open(os.path.join(ROOT, '.github', 'workflows', 'verify.yml'), encoding='utf-8').read()
WP = open(os.path.join(ROOT, '.github', 'workflows', 'verify-push.yml'), encoding='utf-8').read()
A, B = 'a' * 40, 'b' * 40


# ---------------------------------------------------------------- one canonical plan
def test_the_slices_are_an_exact_partition_of_the_canonical_full_plan():
    assert C.partition_problems() == []
    flat = [s for steps in C.PARTS.values() for s in steps]
    assert sorted(flat) == sorted(C.plan_names()) and len(flat) == len(set(flat))
    src = open(os.path.join(ROOT, 'verify.py'), encoding='utf-8').read()
    main = src[src.index('def main():'):]
    assert 'run_full_plan(rep)' in main and 'replay_step(' not in main and 'ui_step(' not in main   # sequential = the plan


def test_a_new_full_gate_missing_from_the_slices_fails_the_merge(monkeypatch, tmp_path):
    monkeypatch.setattr(V, 'FULL_PLAN', V.FULL_PLAN + (('new safety gate', lambda rep: rep.step('new safety gate', True)),))
    assert any('new safety gate' in p for p in C.partition_problems())
    rc, s = _merge(monkeypatch, tmp_path)
    assert rc == 1 and 'slice plan covers verify.py full exactly' in s['failed_steps'] and 'new safety gate' in s['failed_steps']
    assert C.main(['part', 'tests', '--out', str(tmp_path / 'p')]) == 1        # a slice refuses to run on a broken plan


def test_sequential_full_runs_the_plan_in_order(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(V, 'pytest_step', lambda rep, name, args, t: ran.append(name) or rep.step(name, True))
    monkeypatch.setattr(V, 'replay_step', lambda rep, name, args: ran.append(name) or rep.step(name, True))
    monkeypatch.setattr(V, 'ui_step', lambda rep: ran.append(V.UI_STEP) or rep.step(V.UI_STEP, True))
    V.run_full_plan(V.Report('full', str(tmp_path)))
    assert ran == C.plan_names()


def test_each_slice_runs_only_its_own_steps_in_plan_order(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(V, 'pytest_step', lambda rep, name, args, t: calls.append(name) or rep.step(name, True))
    monkeypatch.setattr(V, 'replay_step', lambda rep, name, args: calls.append((name, rep.out)) or rep.step(name, True))
    monkeypatch.setattr(V, 'ui_step', lambda rep: calls.append((V.UI_STEP, rep.out)) or rep.step(V.UI_STEP, True))
    for part, steps in C.PARTS.items():
        calls.clear()
        assert C.main(['part', part, '--out', str(tmp_path / part)]) == 0
        assert [c if isinstance(c, str) else c[0] for c in calls] == steps, part
        s = json.load(open(tmp_path / part / f'latest_full-{part}.json'))
        assert [x['name'] for x in s['steps']] == steps and s['passed'] and s['level'] == f'full-{part}'
    calls.clear(); C.main(['part', 'replay1-ui', '--out', str(tmp_path / 'r')])
    assert calls[0][1] == calls[1][1], 'replay 1 and the UI harness share one output folder (the harness needs its seeds)'


# ---------------------------------------------------------------- merge = the required "verify full" check
def _slice(d, part, commit=A, steps=None, passed=True, level=None):
    st = steps if steps is not None else [dict(name=s, status='passed', seconds=1.0) for s in C.PARTS[part]]
    os.makedirs(d / f'verify-full-{part}-{commit}', exist_ok=True)
    json.dump(dict(level=level or f'full-{part}', git=dict(commit=commit), steps=st, passed=passed,
                   tests={'tests (all)': dict(passed=9)} if part == 'tests' else {}),
              open(d / f'verify-full-{part}-{commit}' / f'latest_full-{part}.json', 'w'))


def _merge(monkeypatch, tmp_path, override=None, drop=None, extra_dir=None):
    monkeypatch.setattr(V, 'git_info', lambda: dict(commit=A))
    monkeypatch.setattr(V, 'dependency_versions', lambda: {})
    built = []
    monkeypatch.setattr(V, 'installer_mode', lambda rep, mode, t: built.append(mode) or rep.step(f'installer {mode}', True, skip=True,
                                                                                                    why='unit test: no real build'))
    parts = tmp_path / 'parts'; parts.mkdir(exist_ok=True)
    for part in C.PARTS:
        if part == drop: continue
        (override or {}).get(part, lambda d, p: _slice(d, p))(parts, part)
    if extra_dir: extra_dir(parts)
    out = tmp_path / 'out'
    rc = C.main(['merge', '--parts', str(parts), '--out', str(out)])
    assert built == ['buildcheck'], 'the merge asks for buildcheck exactly once (mocked: never a real build in unit tests)'
    return rc, json.load(open(out / 'latest_full.json'))


def test_merge_passes_only_with_every_slice_exact(monkeypatch, tmp_path):
    rc, s = _merge(monkeypatch, tmp_path)
    names = [x['name'] for x in s['steps']]
    assert rc == 0 and s['passed'] and s['level'] == 'full'
    plan_pos = [names.index(n) for n in C.plan_names()]
    assert plan_pos == sorted(plan_pos), 'gates listed in plan order'
    assert s['tests']['tests (all)']['passed'] == 9 and 'installer buildcheck' in names


@pytest.mark.parametrize('case', ['missing', 'failed', 'other_commit', 'duplicate_summary', 'missing_step', 'extra_failed_step',
                                  'duplicate_step', 'passed_false', 'passed_missing', 'wrong_level', 'reordered', 'malformed'])
def test_merge_rejects_every_deviation(monkeypatch, tmp_path, case):
    ok = [dict(name=s, status='passed') for s in C.PARTS['replay1-ui']]
    o = {
        'failed': {'replay1-ui': lambda d, p: _slice(d, p, steps=[ok[0], dict(ok[1], status='FAILED')], passed=False)},
        'other_commit': {'replay1-ui': lambda d, p: _slice(d, p, commit=B)},
        'missing_step': {'replay1-ui': lambda d, p: _slice(d, p, steps=ok[:1])},
        'extra_failed_step': {'replay1-ui': lambda d, p: _slice(d, p, steps=ok + [dict(name='surprise gate', status='FAILED')])},
        'duplicate_step': {'replay1-ui': lambda d, p: _slice(d, p, steps=ok + [ok[1]])},
        'passed_false': {'replay1-ui': lambda d, p: _slice(d, p, passed=False)},
        'passed_missing': {'replay1-ui': lambda d, p: _slice(d, p, passed=None)},
        'wrong_level': {'replay1-ui': lambda d, p: _slice(d, p, level='full-tests')},
        'reordered': {'replay1-ui': lambda d, p: _slice(d, p, steps=ok[::-1])},
        'malformed': {'replay1-ui': lambda d, p: _slice(d, p, steps=['x', 'y'])},
    }.get(case)
    kw = dict(override=o)
    if case == 'missing': kw = dict(drop='replay1-ui')
    if case == 'duplicate_summary':
        kw = dict(extra_dir=lambda d: (os.makedirs(d / 'dup', exist_ok=True),
                                       json.dump(dict(level='full-replay1-ui', git=dict(commit=A), steps=ok, passed=True),
                                                 open(d / 'dup' / 'latest_full-replay1-ui.json', 'w'))))
    rc, s = _merge(monkeypatch, tmp_path, **kw)
    assert rc == 1 and set(C.PARTS['replay1-ui']) <= set(s['failed_steps']), (case, s['failed_steps'])
    assert not set(C.PARTS['tests']) & set(s['failed_steps'])               # the good slices stay good


def test_merge_rejects_an_unexpected_slice(monkeypatch, tmp_path):
    rc, s = _merge(monkeypatch, tmp_path, extra_dir=lambda d: _slice(d, 'tests') or os.replace(
        d / f'verify-full-tests-{A}' / 'latest_full-tests.json', d / f'verify-full-tests-{A}' / 'latest_full-bonus.json') or _slice(d, 'tests'))
    assert rc == 1 and 'no unexpected slice summaries' in s['failed_steps']


# ---------------------------------------------------------------- required checks: never skipped, never reused
def test_only_the_pull_request_workflow_produces_the_required_check_names():
    jobs = re.findall(r'^    name: (.+)$', WF, re.M)
    assert jobs.count('verify fast') == 1 and jobs.count('verify full') == 1
    pjobs = re.findall(r'^    name: (.+)$', WP, re.M)
    assert pjobs == ['push fast'] and 'verify fast' not in WP.split('jobs:')[1] and 'verify full' not in WP.split('jobs:')[1]
    on = WF.split('\non:\n')[1].split('\npermissions:')[0]
    assert 'pull_request:' in on and 'workflow_dispatch:' in on and 'push:' not in on and 'inputs:' not in on
    assert 'push:' in WP.split('\non:\n')[1].split('\npermissions:')[0]


def test_required_jobs_are_never_conditionally_skipped():
    fast = WF.split('\n  fast:\n')[1].split('\n  full-part:\n')[0]
    part = WF.split('\n  full-part:\n')[1].split('\n  full:\n')[0]
    full = WF.split('\n  full:\n')[1]
    assert '\n    if:' not in fast and '\n    if:' not in part                 # no job-level condition at all
    assert re.search(r'\n    if: always\(\)\n', full) and 'needs: full-part' in full   # a failed/cancelled slice fails it
    code = '\n'.join(l for l in WF.splitlines() if not l.lstrip().startswith('#'))
    assert 'reuse' not in code and 'verify_ci.py reused' not in code and 'verify_ci.py plan' not in code
    assert 'python verify.py fast --out verify_out' in fast and 'python verify_ci.py merge --parts parts --out verify_out' in full
    assert 'part: [tests, replay2, replay1-ui]' in part and set(C.PARTS) == {'tests', 'replay2', 'replay1-ui'} and 'fail-fast: false' in part
    assert 'permissions:\n  contents: read\n\n' in WF


def test_push_workflow_permissions_and_wiring():
    assert 'permissions:\n  contents: read\n  actions: read\n' in WP and 'write' not in WP
    assert "if: steps.plan.outputs.reuse == 'true'" in WP and "if: steps.plan.outputs.reuse != 'true'" in WP
    assert 'python verify.py fast --out verify_out' in WP and 'fetch-depth: 0' in WP
    assert C.PUSH_WORKFLOW == '.github/workflows/verify-push.yml'


# ---------------------------------------------------------------- push-only docs reuse (fail-closed)
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
    side = g('rev-parse', 'HEAD'); g('checkout', '-q', '-')
    return base, docs, code, side


def test_reuse_decision_is_push_only_and_fail_closed(monkeypatch, tmp_path):
    base, docs, code, side = _repo(tmp_path)
    real_run = V.run
    monkeypatch.setattr(V, 'run', lambda cmd, timeout, env=None, cwd=None: real_run(cmd, timeout, env=env, cwd=str(tmp_path)))
    ok = lambda sha: dict(run_id=1, url='u')
    assert C.decide('push', base, docs, lookup=ok)['reuse'] is True
    for ev in ('pull_request', 'workflow_dispatch', '', 'pull_request_target'):
        assert not C.decide(ev, base, docs, lookup=ok)['reuse'], ev                 # required checks: never reused
    assert not C.decide('push', docs, code, lookup=ok)['reuse']                     # code changed
    assert not C.decide('push', base, code, lookup=ok)['reuse']                     # docs + code since before
    assert not C.decide('push', C.ZERO, docs, lookup=ok)['reuse']                   # new branch
    assert not C.decide('push', '', docs, lookup=ok)['reuse']
    assert not C.decide('push', code, docs, lookup=ok)['reuse']                     # not an ancestor
    assert not C.decide('push', side, docs, lookup=ok)['reuse']                     # docs-only diff, but not an ancestor
    assert not C.decide('push', 'c' * 40, docs, lookup=ok)['reuse']                 # unknown commit
    assert not C.decide('push', base, docs, lookup=lambda s: None)['reuse']         # no successful push run there
    seen = []
    C.decide('push', base, docs, lookup=lambda s: seen.append(s))
    assert seen == [base]


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _opener(runs, boom=False):
    def op(req, timeout):
        if boom: raise OSError('network down')
        assert req.headers['Authorization'] == 'Bearer tok' and 'event=push' in req.full_url and f'head_sha={A}' in req.full_url
        return _Resp(json.dumps(dict(workflow_runs=runs)).encode())
    return op


def test_prior_push_success_requires_the_same_push_workflow_on_that_commit():
    good = dict(id=7, html_url='h', head_sha=A, event='push', status='completed', conclusion='success', path=C.PUSH_WORKFLOW)
    call = lambda runs, **k: C.prior_push_success(A, repo='o/r', token='tok', opener=_opener(runs, **k))
    assert call([good])['run_id'] == 7 and call([good])['event'] == 'push'
    for bad in (dict(good, conclusion='failure'), dict(good, status='in_progress'), dict(good, head_sha=B),
                dict(good, event='pull_request'), dict(good, event='workflow_dispatch'), dict(good, path='.github/workflows/verify.yml')):
        assert call([bad]) is None, bad
    assert call([good], boom=True) is None
    assert C.prior_push_success(A, repo='o/r', token=None, opener=_opener([good])) is None
    assert C.prior_push_success('nope', repo='o/r', token='tok', opener=_opener([good])) is None


def test_reused_rechecks_and_labels_every_skipped_step(monkeypatch, tmp_path):
    monkeypatch.setattr(V, 'git_info', lambda: dict(commit=B))
    monkeypatch.setattr(V, 'dependency_versions', lambda: {})
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    yes = dict(reuse=True, why='docs', reused_from=A, prior=dict(run_id=5, url='https://run/5'), files=['docs/s.md'])
    monkeypatch.setattr(C, 'decide', lambda *a, **k: yes)
    assert C.main(['reused', '--before', A, '--head', B, '--out', str(tmp_path / 'y')]) == 0
    s = json.load(open(tmp_path / 'y' / 'latest_push-fast.json'))
    skipped = {x['name']: x for x in s['steps'] if x['status'] == 'skipped'}
    assert set(skipped) == {'tests (fast set: -m "not slow")', 'installer preflight'}
    assert all(x['detail'].startswith('REUSED') and A[:12] in x['detail'] and 'push run 5' in x['detail'] for x in skipped.values())
    assert any(x['name'].startswith('static:') and x['status'] == 'passed' for x in s['steps'])
    monkeypatch.setattr(C, 'decide', lambda *a, **k: dict(reuse=False, why='code or config changed'))
    assert C.main(['reused', '--before', A, '--head', B, '--out', str(tmp_path / 'n')]) == 1


def test_plan_writes_the_decision(monkeypatch, tmp_path):
    out = tmp_path / 'gh_out'
    monkeypatch.setenv('GITHUB_OUTPUT', str(out)); monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    monkeypatch.setattr(C, 'decide', lambda *a, **k: dict(reuse=True, why='x'))
    assert C.main(['plan', '--before', A, '--head', B]) == 0
    assert out.read_text().splitlines() == ['reuse=true']


def test_nothing_executed_reads_docs():
    """The reuse rule rests on this: no code, script, page, workflow or config outside docs/ refers to docs/."""
    allowed = {os.path.join(ROOT, 'verify_ci.py'), os.path.join(ROOT, 'tests', 'test_ci.py')}
    exts = ('.py', '.pyw', '.ps1', '.psm1', '.bat', '.cmd', '.html', '.js', '.spec', '.yml', '.yaml', '.toml', '.ini', '.cfg')
    hits, scanned = [], set()
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in ('.git', 'docs', 'data', 'data1h', 'data_long', 'research', 'dev_out', 'verify_out', '__pycache__')]
        for fn in fns:
            p = os.path.join(dp, fn)
            if p in allowed or not fn.lower().endswith(exts): continue
            scanned.add(os.path.relpath(p, ROOT).replace(os.sep, '/'))
            txt = open(p, encoding='utf-8', errors='ignore').read()
            if re.search(r"""['"/\\]docs['"/\\]""", txt): hits.append(os.path.relpath(p, ROOT))
    assert '.github/workflows/verify.yml' in scanned and '.github/workflows/verify-push.yml' in scanned
    assert hits == []
