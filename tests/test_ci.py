"""T04d: faster CI. The parallel slices must be an exact partition of verify.FULL_PLAN, the merge must reject every
deviation, required checks must never be skipped or reused, and push-only docs reuse must be fail-closed."""
import hashlib, io, json, os, re, subprocess, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import verify as V                                                         # noqa: E402
import verify_ci as C                                                      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = open(os.path.join(ROOT, '.github', 'workflows', 'verify.yml'), encoding='utf-8').read()
WP = open(os.path.join(ROOT, '.github', 'workflows', 'verify-push.yml'), encoding='utf-8').read()
WQ = open(os.path.join(ROOT, '.github', 'workflows', 'verify-fast.yml'), encoding='utf-8').read()
WORKFLOWS = {'verify.yml': WF, 'verify-fast.yml': WQ, 'verify-push.yml': WP}


def _on(wf):
    return wf.split('\non:\n')[1].split('\npermissions:')[0]


def _jobs(wf):
    return re.findall(r'^    name: (.+)$', wf, re.M)
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
REQUIRED = ('verify fast', 'verify full')
_NAME_EXPR = re.compile(r"\$\{\{ github\.event\.label\.name == '([^']+)' && '([^']+)' \|\| '([^']+)' \}\}$")


def _job_blocks(wf):
    """[(job id, job body)] by plain line parsing (no backtracking regex: CodeQL py/redos, Codex round 4 P2)."""
    out = []
    for line in wf.split('\njobs:\n')[1].splitlines():
        if line.startswith('  ') and not line.startswith('   ') and line.rstrip().endswith(':'):
            out.append([line.strip()[:-1], ''])
        elif out and (line.startswith('    ') or not line.strip()):
            out[-1][1] += line + '\n'
    return [tuple(x) for x in out]


def _resolve(expr, label):
    """The job name / job `if` exactly as used in these workflows, for a given event label (anything else -> error)."""
    m = _NAME_EXPR.fullmatch(expr)
    if m: return m.group(2) if label == m.group(1) else m.group(3)
    m = re.fullmatch(r"github\.event\.label\.name == '([^']+)'", expr)
    if m: return label == m.group(1)
    if expr == 'always()': return True
    if '${{' not in expr.replace('${{ matrix.part }}', ''): return expr   # a matrix name is static per event (never required)
    raise AssertionError(f'unmodelled expression {expr!r}')


def _triggered(wf, event, action):
    on = _on(wf)
    if event == 'push': return on.lstrip().startswith('push:')
    if not on.lstrip().startswith('pull_request:'): return False
    m = re.search(r'types: \[([^\]]+)\]', on)
    types = [t.strip() for t in m.group(1).split(',')] if m else ['opened', 'synchronize', 'reopened']   # GitHub default
    return action in types


def _published(event, action=None, label=None):
    """Every check name each workflow publishes for one event: [(file, name, 'run'|'skipped')]."""
    out = []
    for f, wf in WORKFLOWS.items():
        if not _triggered(wf, event, action): continue
        for _, block in _job_blocks(wf):
            name = _resolve(re.search(r'^    name: (.+)$', block, re.M).group(1), label)
            cond = re.search(r'^    if: (.+)$', block, re.M)
            out.append((f, name, 'run' if cond is None or _resolve(cond.group(1), label) else 'skipped'))
    return out


def test_each_required_name_has_exactly_one_producer():
    """verify fast: only verify-fast.yml (PR). verify full: only verify.yml (full-ready label). push fast: only
    verify-push.yml."""
    owners = {n: sorted({f for ev in (('pull_request', a, l) for a in ('opened', 'synchronize', 'reopened', 'labeled')
                                       for l in (None, 'full-ready', 'bug')) for f, nm, _ in _published(*ev) if nm == n}
                        | {f for f, nm, _ in _published('push') if nm == n}) for n in ('verify fast', 'verify full', 'push fast')}
    assert owners == {'verify fast': ['verify-fast.yml'], 'verify full': ['verify.yml'], 'push fast': ['verify-push.yml']}
    assert _jobs(WQ) == ['verify fast'] and _jobs(WP) == ['push fast']


def test_triggers_one_fast_run_per_feature_commit_and_no_full_on_pr():
    """Owner speed decision + Codex rounds 2/4: an ordinary PR event (opened/synchronize/reopened) gets ONE "verify fast"
    and NO "verify full" in any state; push verification is master-only; the full gate is the labelled event only."""
    assert _on(WQ).strip() == 'pull_request:\n    branches: [master]'
    assert _on(WP).strip() == 'push:\n    branches: [master]'
    assert _on(WF).strip() == 'pull_request:\n    types: [labeled]\n    branches: [master]'
    for action in ('opened', 'synchronize', 'reopened'):
        got = _published('pull_request', action)
        assert got == [('verify-fast.yml', 'verify fast', 'run')], (action, got)
    assert _published('push') == [('verify-push.yml', 'push fast', 'run')]
    for f, wf in WORKFLOWS.items():
        assert 'workflow_dispatch' not in _on(wf) and 'schedule' not in _on(wf) and 'pull_request_target' not in _on(wf), f


def test_only_the_full_ready_label_publishes_verify_full():
    """Codex round 4: the full-ready label runs the slices and the real aggregator as "verify full"; any other label
    runs no slice and names the aggregator "full gate not requested" - never "verify full", neither run nor skipped -
    and does not start "verify fast" again."""
    got = _published('pull_request', 'labeled', 'full-ready')
    assert sorted(got) == sorted([('verify.yml', 'full slice ${{ matrix.part }}', 'run'), ('verify.yml', 'verify full', 'run')])
    for label in ('bug', 'Full-Ready', 'full-ready ', 'verify full', 'full', ''):
        got = _published('pull_request', 'labeled', label)
        assert ('verify.yml', 'full gate not requested', 'run') in got, label
        assert all(nm not in REQUIRED for _, nm, _ in got), (label, got)
        assert ('verify.yml', 'full slice ${{ matrix.part }}', 'skipped') in got
    assert _published('pull_request', 'unlabeled', 'full-ready') == []          # removing the label publishes nothing


def test_the_not_requested_aggregator_does_no_work():
    full = WF.split('\n  full:\n')[1]
    steps = full.split('    steps:\n')[1].split('\n      - ')
    assert "if: github.event.label.name != 'full-ready'" in steps[0] and 'run: echo' in steps[0]
    for st in steps[1:]:                                                    # everything else needs the label
        assert re.search(r"if: (always\(\) && )?github\.event\.label\.name == 'full-ready'", st), st


def test_full_gate_binds_to_the_exact_pr_head():
    """The event's PR head SHA (not the refs/pull/N/merge commit) is format-checked first, checked out explicitly,
    re-proved after checkout, used for the slice artifacts, and re-checked by the merge."""
    assert 'ZB_HEAD_SHA: ${{ github.event.pull_request.head.sha }}' in WF
    assert 'github.sha' not in WF and '${{ inputs.' not in WF
    for job in ('\n  full-part:\n', '\n  full:\n'):
        body = WF.split(job)[1].split('\n  full:\n')[0]
        steps = [st for st in body.split('    steps:\n')[1].split('\n      - ') if "!= 'full-ready'" not in st]
        assert 'Exact-head guard' in steps[0], 'the guard must be the FIRST working step of every full job'
        assert '[ "$ZB_LABEL" = "full-ready" ]' in steps[0] and '^[0-9a-f]{40}$' in steps[0]
        assert steps[1].startswith('uses: actions/checkout@') and 'ref: ${{ github.event.pull_request.head.sha }}' in steps[1]
        assert 'python verify_ci.py guard --expected "$ZB_HEAD_SHA"' in steps[2]
    assert 'name: verify-full-${{ matrix.part }}-${{ github.event.pull_request.head.sha }}' in WF
    assert 'pattern: verify-full-*-${{ github.event.pull_request.head.sha }}' in WF
    assert '--expected-sha "$ZB_HEAD_SHA"' in WF.split('\n  full:\n')[1]
    assert 'group: verify-full-${{ github.event.pull_request.number }}-${{ github.event.label.name }}' in WF  # other labels never cancel a full


def test_a_new_commit_invalidates_the_full_result():
    """A new head gets no "verify full": synchronize does not start verify.yml, so the old run's result stays on the old
    SHA and protection blocks until Codex removes and re-adds full-ready; a mismatched checkout is refused."""
    assert all(nm != 'verify full' for _, nm, _ in _published('pull_request', 'synchronize'))
    old, new = 'a' * 40, 'b' * 40
    assert C.head_problem(old, new) and 'stale' in C.head_problem(old, new)
    assert C.head_problem(new, new) is None


def test_guard_command(monkeypatch):
    """The guard compares the CHECKED-OUT commit (git rev-parse), never GITHUB_SHA (the PR merge ref on PR events)."""
    monkeypatch.setattr(V, 'git_info', lambda: dict(commit=A))
    monkeypatch.setenv('GITHUB_SHA', B)
    assert C.main(['guard', '--expected', A]) == 0
    assert C.main(['guard', '--expected', B]) == 1
    for bad in ('', 'abc', A[:12], A.upper(), A + 'f'):
        assert C.main(['guard', '--expected', bad]) == 1, bad


def test_merge_refuses_a_head_other_than_the_approved_one(monkeypatch, tmp_path):
    monkeypatch.setattr(V, 'git_info', lambda: dict(commit=A))
    monkeypatch.setattr(V, 'dependency_versions', lambda: {})
    monkeypatch.setattr(V, 'installer_mode', lambda rep, mode, t: rep.step(f'installer {mode}', True, skip=True))
    parts = tmp_path / 'parts'; parts.mkdir()
    for part in C.PARTS: _slice(parts, part)
    assert C.main(['merge', '--parts', str(parts), '--out', str(tmp_path / 'ok'), '--expected-sha', A]) == 0
    assert C.main(['merge', '--parts', str(parts), '--out', str(tmp_path / 'no'), '--expected-sha', B]) == 1
    s = json.load(open(tmp_path / 'no' / 'latest_full.json'))
    assert 'exact approved head' in s['failed_steps'] and s['approved_head_sha'] == B


def test_required_jobs_are_never_conditionally_skipped():
    part = WF.split('\n  full-part:\n')[1].split('\n  full:\n')[0]
    full = WF.split('\n  full:\n')[1]
    fast = WQ.split('\n  fast:\n')[1]
    assert '\n    if:' not in fast                                         # no job-level condition at all
    assert re.findall(r'\n    if: (.+)\n', part) == ["github.event.label.name == 'full-ready'"]   # non-required slices only
    assert re.search(r'\n    if: always\(\)\n', full) and 'needs: full-part' in full   # a failed/cancelled slice FAILS it
    assert full.count('\n    if:') == 1
    assert 'verify full' not in re.search(r'^    name: (.+)$', part, re.M).group(1)   # a skippable job never carries the name
    for wf in (WF, WQ):
        code = '\n'.join(l for l in wf.splitlines() if not l.lstrip().startswith('#'))
        assert 'reuse' not in code and 'verify_ci.py reused' not in code and 'verify_ci.py plan' not in code
    assert 'python verify.py fast --out verify_out' in fast and 'python verify_ci.py merge --parts parts --out verify_out' in full
    assert 'part: [tests, replay2, replay1-ui]' in part and set(C.PARTS) == {'tests', 'replay2', 'replay1-ui'} and 'fail-fast: false' in part
    assert 'permissions:\n  contents: read\n\n' in WF and 'permissions:\n  contents: read\n\n' in WQ
    assert 'write' not in WF and 'write' not in WQ


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
    assert {'.github/workflows/verify.yml', '.github/workflows/verify-push.yml', '.github/workflows/verify-fast.yml'} <= scanned
    assert hits == []


# ---------------------------------------------------------------- round 2: deterministic dataset bytes on every checkout
def _manifest():
    return json.load(open(os.path.join(ROOT, 'DATA_MANIFEST.json'), encoding='utf-8'))['files']


def _git(*a, cwd=ROOT):
    return subprocess.run(['git', '-C', cwd] + list(a), capture_output=True, text=True)


def test_every_manifest_file_is_checked_out_with_lf():
    """Codex round 2 P1: Git for Windows (core.autocrlf=true) converted the 64 hashed CSVs to CRLF. .gitattributes pins
    eol=lf for every manifest path and for DATA_MANIFEST.json itself."""
    paths = list(_manifest()) + ['DATA_MANIFEST.json']
    assert len(paths) == 65
    if _git('rev-parse', '--git-dir').returncode != 0: pytest.skip('not a git checkout')
    out = _git('check-attr', 'eol', '--', *paths).stdout.splitlines()
    eol = {l.split(': ')[0]: l.rsplit(': ', 1)[1] for l in out}
    assert {p: eol.get(p) for p in paths} == {p: 'lf' for p in paths}
    ga = open(os.path.join(ROOT, '.gitattributes'), encoding='utf-8').read()
    assert '*.bat' not in ga                                               # the CRLF launchers are left alone


@pytest.mark.slow
def test_a_fresh_autocrlf_clone_passes_the_manifest_check(tmp_path):
    """A brand-new clone with core.autocrlf=true (Git for Windows' default) must reproduce the hashed bytes exactly."""
    if _git('rev-parse', '--git-dir').returncode != 0: pytest.skip('not a git checkout')
    if _git('cat-file', '-e', 'HEAD:.gitattributes').returncode != 0: pytest.skip('.gitattributes not committed in this checkout')
    dst = tmp_path / 'clone'
    r = subprocess.run(['git', '-c', 'core.autocrlf=true', 'clone', '-q', '--no-hardlinks', ROOT, str(dst)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert _git('config', 'core.autocrlf', cwd=str(dst)).stdout.strip() in ('', 'true')
    bad = []
    for rel, meta in _manifest().items():
        b = open(dst / rel, 'rb').read()
        if b'\r\n' in b or hashlib.sha256(b).hexdigest() != meta['sha256']: bad.append(rel)
    assert bad == [], f'{len(bad)} manifest files differ in a fresh autocrlf=true clone, e.g. {bad[:3]}'
    rep = V.Report('full', str(tmp_path / 'rep'))
    old = V.ROOT
    try:
        V.ROOT = str(dst); assert V.manifest_check(rep)
    finally:
        V.ROOT = old
