"""T04d: faster GitHub CI, built on verify.py (no gate is weakened, verify.py itself is unchanged).

    python verify_ci.py plan --event E --before SHA --head SHA        decides docs-only reuse for fast and full (GITHUB_OUTPUT)
    python verify_ci.py part tests|replay2|replay1-ui --out DIR        one slice of `verify.py full`, run as a parallel job
    python verify_ci.py merge --parts DIR --out DIR                    the required "verify full" check: every slice present,
                                                                       same commit, every step passed -> one full summary
    python verify_ci.py reused fast|full --event E --before SHA --head SHA --out DIR
                                                                       docs-only change on top of a commit whose same check
                                                                       already passed: re-proves that, runs the cheap static
                                                                       checks, and records the heavy steps as SKIPPED (reused)

Docs-only reuse (owner, 2026-10-06): the heavy steps may be reused only when ALL hold, otherwise everything runs:
  - push or pull_request event (never workflow_dispatch), with a real previous commit (`before`, not all zeros);
  - `before` is an ancestor of the new head (no force-push), and git can diff the two;
  - every changed file is a document under docs/ (no code/config extension; nothing executed reads docs/: enforced by
    tests/test_ci.py; the secret scan and compile check still run on the whole tree);
  - the SAME check ("verify fast" / "verify full") from GitHub Actions concluded `success` on `before`.
Any error or unknown answer means "no reuse" (fail-closed: the slow path runs). The summary names the commit and run reused.
"""
import argparse, glob, json, os, re, sys, time, urllib.request
import verify as V

DOCS_PREFIXES = ('docs/',)
CODE_EXT = ('.py', '.pyw', '.ps1', '.psm1', '.bat', '.cmd', '.js', '.html', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.json', '.txt', '.spec')
ZERO = '0' * 40
SHA_RX = re.compile(r'[0-9a-f]{40}')
CHECK = {'fast': 'verify fast', 'full': 'verify full'}
UI_STEP = 'UI harness (all tabs x 3 widths, flows, API failures)'
PARTS = {                                       # slice -> the verify.py full steps it runs, in verify.py's order
    'tests': ['tests (all)'],
    'replay2': [V.REPLAYS[1][0]],
    'replay1-ui': [V.REPLAYS[0][0], UI_STEP],   # the UI harness seeds its closed trades from replay 1 of the same run
}
FULL_STEPS = ['tests (all)', V.REPLAYS[0][0], V.REPLAYS[1][0], UI_STEP]   # what `verify.py full` runs besides static/manifest


# ---------------------------------------------------------------- docs-only reuse decision
def changed_files(before, head):
    """Files changed from before to head, or None when git cannot tell (unknown commit, not an ancestor, git error)."""
    if not (SHA_RX.fullmatch(before or '') and SHA_RX.fullmatch(head or '')) or before == ZERO: return None
    rc, _, _ = V.run(['git', 'merge-base', '--is-ancestor', before, head], 60)
    if rc != 0: return None
    rc, out, _ = V.run(['git', 'diff', '--name-only', '--no-renames', f'{before}..{head}'], 120)
    if rc != 0: return None
    return [l.strip() for l in out.splitlines() if l.strip()]


def docs_only(files):
    """True only for a non-empty change set of documents under docs/ (code- or config-like files there do not count)."""
    return bool(files) and all(f.startswith(DOCS_PREFIXES) and '..' not in f and not f.lower().endswith(CODE_EXT) for f in files)


def prior_success(sha, check_name, repo=None, token=None, opener=urllib.request.urlopen):
    """The run of `check_name` (GitHub Actions) that concluded success on commit `sha`, or None. Fail-closed on any error."""
    repo = repo or os.environ.get('GITHUB_REPOSITORY'); token = token or os.environ.get('GITHUB_TOKEN')
    if not (repo and token and SHA_RX.fullmatch(sha or '')): return None
    url = f'https://api.github.com/repos/{repo}/commits/{sha}/check-runs?check_name={urllib.request.quote(check_name)}&filter=all&per_page=50'
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                                              'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with opener(req, timeout=30) as r: data = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print(f'reuse: check-run lookup failed ({e})', flush=True); return None
    for c in data.get('check_runs') or []:
        if (c.get('name') == check_name and c.get('head_sha') == sha and c.get('status') == 'completed'
                and c.get('conclusion') == 'success' and (c.get('app') or {}).get('slug') == 'github-actions'):
            return dict(check_run_id=c.get('id'), url=c.get('html_url'), completed_at=c.get('completed_at'))
    return None


def decide(level, event, before, head, lookup=prior_success):
    """dict(reuse=bool, why=str, ...) for one level. Every 'no' is explicit so the log shows why the slow path ran."""
    if event not in ('push', 'pull_request'): return dict(reuse=False, why=f'event {event}: always the full gate')
    files = changed_files(before, head)
    if files is None: return dict(reuse=False, why=f'no usable previous commit ({(before or "none")[:12]} -> {(head or "none")[:12]})')
    if not docs_only(files): return dict(reuse=False, why='code or config changed', files=files[:20])
    prior = lookup(before, CHECK[level])
    if not prior: return dict(reuse=False, why=f'"{CHECK[level]}" has no success on {before[:12]}', files=files)
    return dict(reuse=True, why=f'docs-only change on top of {before[:12]}, where "{CHECK[level]}" passed',
                reused_from=before, prior=prior, files=files)


def _gh_output(**kv):
    p = os.environ.get('GITHUB_OUTPUT')
    if p:
        with open(p, 'a', encoding='utf-8') as f:
            for k, v in kv.items(): f.write(f'{k}={v}\n')


def cmd_plan(a):
    out = {}
    for lvl in ('fast', 'full'):
        d = decide(lvl, a.event, a.before, a.head)
        out[lvl] = d
        print(f"plan {lvl}: {'REUSE' if d['reuse'] else 'run'} - {d['why']}", flush=True)
    _gh_output(reuse_fast=str(out['fast']['reuse']).lower(), reuse_full=str(out['full']['reuse']).lower())
    return 0


def _report(level, out):
    rep = V.Report(level, os.path.abspath(out))
    rep.data['git'] = V.git_info()
    rep.data['dependencies'] = V.dependency_versions()
    bi = os.path.join(V.ROOT, 'build_info.py')
    rep.data['build_id'] = re.search(r"'(.*)'", open(bi).read()).group(1) if os.path.exists(bi) else 'dev'
    rep.data['ci'] = dict(run_id=os.environ.get('GITHUB_RUN_ID'), run_number=os.environ.get('GITHUB_RUN_NUMBER'),
                          event=os.environ.get('GITHUB_EVENT_NAME'))
    print(f"VERIFY {level.upper()} - commit {str(rep.data['git'].get('commit'))[:12]} - {rep.data['started']} Cairo", flush=True)
    return rep


def cmd_reused(a):
    """Re-decides independently (does not trust the plan job), then runs static + manifest and records the reuse."""
    rep = _report(a.level, a.out)
    d = decide(a.level, a.event, a.before, a.head)
    rep.data['reuse'] = d
    V.static_checks(rep)
    V.manifest_check(rep)
    if not d['reuse']:
        rep.step('docs-only reuse re-check', False, why=f"the reuse conditions do not hold: {d['why']}")
        return rep.finish()
    heavy = ['tests (fast set: -m "not slow")', 'installer preflight'] if a.level == 'fast' else FULL_STEPS + ['installer buildcheck']
    for name in heavy:
        rep.step(name, True, skip=True, detail=f"REUSED: docs-only change; passed on {d['reused_from'][:12]} "
                                               f"({d['prior'].get('url')})", reused_from=d['reused_from'])
    return rep.finish()


# ---------------------------------------------------------------- parallel slices of verify.py full
def cmd_part(a):
    rep = _report(f'full-{a.name}', a.out)
    if a.name == 'tests':
        V.pytest_step(rep, 'tests (all)', ['tests'], 3600)
    elif a.name == 'replay2':
        V.replay_step(rep, *V.REPLAYS[1])
    else:
        V.replay_step(rep, *V.REPLAYS[0])
        V.ui_step(rep)
    return rep.finish()


def load_parts(parts_dir):
    """{slice: summary} from latest_full-<slice>.json files anywhere under parts_dir (one artifact folder per slice)."""
    found = {}
    for p in glob.glob(os.path.join(parts_dir, '**', 'latest_full-*.json'), recursive=True):
        name = os.path.basename(p)[len('latest_full-'):-len('.json')]
        try: found.setdefault(name, []).append(json.load(open(p, encoding='utf-8')))
        except Exception as e: found.setdefault(name, []).append(dict(error=str(e)))
    return found


def cmd_merge(a):
    rep = _report('full', a.out)
    commit = rep.data['git'].get('commit')
    V.static_checks(rep)
    V.manifest_check(rep)
    found = load_parts(a.parts)
    rep.data['parts'] = {}
    for name, expected in PARTS.items():
        got = found.get(name) or []
        if len(got) != 1:
            for step in expected: rep.step(step, False, why=f'slice "{name}": {len(got)} summaries found (expected exactly 1)')
            continue
        s = got[0]
        rep.data['parts'][name] = dict(commit=(s.get('git') or {}).get('commit'), passed=s.get('passed'), run=s.get('ci'))
        steps = {x.get('name'): x for x in s.get('steps') or []}
        same = bool(commit) and (s.get('git') or {}).get('commit') == commit
        for step in expected:
            x = steps.get(step)
            ok = same and x is not None and x.get('status') == 'passed'
            why = None if ok else ('slice ran on another commit' if not same else 'step missing' if x is None else f"step {x.get('status')}")
            info = {k: v for k, v in (x or {}).items() if k not in ('name', 'status', 'seconds', 'why')}
            rep.step(step, ok, (x or {}).get('seconds', 0.0), slice=name, why=why or (x or {}).get('why'), **info)
        for key in ('tests', 'replays'):
            rep.data.setdefault(key, {}).update(s.get(key) or {})
        if s.get('ui') is not None: rep.data['ui'] = s['ui']
    V.installer_mode(rep, 'buildcheck', 3600)                # Windows only, exactly as verify.py full (skipped on Linux)
    return rep.finish()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('plan', 'reused'):
        p = sub.add_parser(name)
        if name == 'reused': p.add_argument('level', choices=['fast', 'full']); p.add_argument('--out', required=True)
        p.add_argument('--event', default=''); p.add_argument('--before', default=''); p.add_argument('--head', default='')
    p = sub.add_parser('part'); p.add_argument('name', choices=list(PARTS)); p.add_argument('--out', required=True)
    p = sub.add_parser('merge'); p.add_argument('--parts', required=True); p.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    return dict(plan=cmd_plan, reused=cmd_reused, part=cmd_part, merge=cmd_merge)[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
