"""T04d: faster GitHub CI, built on verify.py's canonical full plan (no gate is weakened).

    python verify_ci.py part tests|replay2|replay1-ui --out DIR     one exact slice of verify.FULL_PLAN, as a parallel job
    python verify_ci.py merge --parts DIR --out DIR                 the required "verify full" check: the slices must be an
                                                                    exact partition of verify.FULL_PLAN, every slice must be
                                                                    present once, on this commit, passed, with exactly its
                                                                    steps in order and all passed -> one full summary
    python verify_ci.py plan --before SHA --head SHA                push workflow only: docs-only reuse decision
    python verify_ci.py reused --before SHA --head SHA --out DIR    push workflow only: re-proves reuse, runs the cheap
                                                                    static checks, records the heavy steps SKIPPED (reused)

Required checks ("verify fast", "verify full") come only from the pull-request/on-demand workflow (verify.yml), and every
run of it executes them: nothing there is skipped or reused (Codex T04d review). Docs-only reuse exists only in the
non-required push workflow (verify-push.yml, job "push fast") and only from the same context:
  - push event, with a real previous commit (`before`, not all zeros) that is an ancestor of the new head;
  - every changed file is a document under docs/ (no code/config extension; nothing executed reads docs/);
  - a successful run of the SAME push workflow, from a push event, on exactly `before`.
Any error or unknown answer means "no reuse" (the full push check runs). The summary records the run it reused.
"""
import argparse, glob, json, os, re, sys, urllib.parse, urllib.request
import verify as V

DOCS_PREFIXES = ('docs/',)
CODE_EXT = ('.py', '.pyw', '.ps1', '.psm1', '.bat', '.cmd', '.js', '.html', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.json',
            '.txt', '.spec')
ZERO = '0' * 40
SHA_RX = re.compile(r'[0-9a-f]{40}')
PUSH_WORKFLOW = '.github/workflows/verify-push.yml'
PARTS = {                                       # slice -> the FULL_PLAN steps it runs (plan order is kept)
    'tests': ['tests (all)'],
    'replay2': [V.REPLAYS[1][0]],
    'replay1-ui': [V.REPLAYS[0][0], V.UI_STEP],  # the UI harness seeds its closed trades from replay 1 of the same run
}


def plan_names():
    return [n for n, _ in V.FULL_PLAN]


def partition_problems():
    """Why the slices are not an exact, duplicate-free partition of verify.FULL_PLAN ([] = they are)."""
    flat = [s for steps in PARTS.values() for s in steps]
    plan = plan_names()
    out = []
    if len(flat) != len(set(flat)): out.append('a step is in more than one slice')
    if set(flat) - set(plan): out.append(f'slices run steps that are not in the plan: {sorted(set(flat) - set(plan))}')
    if set(plan) - set(flat): out.append(f'plan steps missing from every slice: {sorted(set(plan) - set(flat))}')
    return out


# ---------------------------------------------------------------- parallel slices of verify.py full
def _report(level, out):
    rep = V.Report(level, os.path.abspath(out))
    rep.data['git'] = V.git_info()
    rep.data['dependencies'] = V.dependency_versions()
    bi = os.path.join(V.ROOT, 'build_info.py')
    rep.data['build_id'] = re.search(r"'(.*)'", open(bi).read()).group(1) if os.path.exists(bi) else 'dev'
    rep.data['ci'] = dict(run_id=os.environ.get('GITHUB_RUN_ID'), run_number=os.environ.get('GITHUB_RUN_NUMBER'),
                          event=os.environ.get('GITHUB_EVENT_NAME'), workflow=os.environ.get('GITHUB_WORKFLOW'))
    print(f"VERIFY {level.upper()} - commit {str(rep.data['git'].get('commit'))[:12]} - {rep.data['started']} Cairo", flush=True)
    return rep


def cmd_part(a):
    rep = _report(f'full-{a.name}', a.out)
    bad = partition_problems()
    if bad:
        rep.step('slice plan covers verify.py full exactly', False, why='; '.join(bad))
        return rep.finish()
    V.run_full_plan(rep, names=PARTS[a.name])
    return rep.finish()


def load_parts(parts_dir):
    """{slice: [summaries]} from latest_full-<slice>.json files anywhere under parts_dir (one artifact folder per slice)."""
    found = {}
    for p in glob.glob(os.path.join(parts_dir, '**', 'latest_full-*.json'), recursive=True):
        name = os.path.basename(p)[len('latest_full-'):-len('.json')]
        try: found.setdefault(name, []).append(json.load(open(p, encoding='utf-8')))
        except Exception as e: found.setdefault(name, []).append(dict(error=f'unreadable summary: {e}'))
    return found


def slice_problem(name, s, commit):
    """Why one slice summary cannot be merged (None = it can): exact level, commit, passed, ordered step list, statuses."""
    if not isinstance(s, dict) or s.get('error'): return (s or {}).get('error') or 'malformed summary'
    if s.get('level') != f'full-{name}': return f"wrong level {s.get('level')!r}"
    if not commit or (s.get('git') or {}).get('commit') != commit: return 'slice ran on another commit'
    if s.get('passed') is not True: return f"slice passed={s.get('passed')!r}"
    steps = s.get('steps')
    if not isinstance(steps, list) or not all(isinstance(x, dict) for x in steps): return 'malformed step list'
    names = [x.get('name') for x in steps]
    if names != PARTS[name]: return f'step list {names} is not exactly {PARTS[name]}'
    bad = [x.get('name') for x in steps if x.get('status') != 'passed']
    if bad: return f'steps not passed: {bad}'
    return None


def cmd_merge(a):
    rep = _report('full', a.out)
    commit = rep.data['git'].get('commit')
    V.static_checks(rep)
    V.manifest_check(rep)
    bad = partition_problems()
    rep.step('slice plan covers verify.py full exactly', not bad, why='; '.join(bad) or None, plan=plan_names())
    found = load_parts(a.parts)
    rep.data['parts'] = {}
    extra = sorted(set(found) - set(PARTS))
    if extra: rep.step('no unexpected slice summaries', False, why=f'unexpected slices: {extra}')
    by_name = {}
    for name in PARTS:
        got = found.get(name) or []
        prob = f'{len(got)} summaries found (expected exactly 1)' if len(got) != 1 else slice_problem(name, got[0], commit)
        rep.data['parts'][name] = dict(problem=prob, run=(got[0].get('ci') if len(got) == 1 and isinstance(got[0], dict) else None))
        if prob is None:
            for x in got[0]['steps']: by_name[x['name']] = x
            for key in ('tests', 'replays'): rep.data.setdefault(key, {}).update(got[0].get(key) or {})
            if got[0].get('ui') is not None: rep.data['ui'] = got[0]['ui']
        else:
            for step in PARTS[name]: by_name[step] = dict(name=step, status='FAILED', why=f'slice "{name}": {prob}')
    for step in plan_names():                                    # the merged summary lists the gates in plan order
        x = by_name.get(step) or dict(why='no slice ran this step')
        info = {k: v for k, v in x.items() if k not in ('name', 'status', 'seconds', 'why')}
        rep.step(step, x.get('status') == 'passed', x.get('seconds', 0.0), why=x.get('why'), **info)
    V.installer_mode(rep, 'buildcheck', 3600)                    # Windows only, exactly as verify.py full (skipped on Linux)
    return rep.finish()


# ---------------------------------------------------------------- push workflow only: docs-only reuse
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


def prior_push_success(sha, repo=None, token=None, opener=urllib.request.urlopen):
    """The successful run of the push workflow, from a push event, on exactly `sha` - or None. Fail-closed."""
    repo = repo or os.environ.get('GITHUB_REPOSITORY'); token = token or os.environ.get('GITHUB_TOKEN')
    if not (repo and token and SHA_RX.fullmatch(sha or '')): return None
    q = urllib.parse.urlencode(dict(head_sha=sha, event='push', status='success', per_page=50))
    req = urllib.request.Request(f'https://api.github.com/repos/{repo}/actions/runs?{q}', headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with opener(req, timeout=30) as r: data = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print(f'reuse: run lookup failed ({e})', flush=True); return None
    for run in data.get('workflow_runs') or []:
        if (run.get('head_sha') == sha and run.get('event') == 'push' and run.get('status') == 'completed'
                and run.get('conclusion') == 'success' and run.get('path') == PUSH_WORKFLOW):
            return dict(run_id=run.get('id'), url=run.get('html_url'), event='push', workflow=PUSH_WORKFLOW, head_sha=sha)
    return None


def decide(event, before, head, lookup=prior_push_success):
    """dict(reuse=bool, why=str, ...). Every 'no' is explicit so the log shows why the full check ran."""
    if event != 'push': return dict(reuse=False, why=f'event {event}: reuse exists only for push')
    files = changed_files(before, head)
    if files is None: return dict(reuse=False, why=f'no usable previous commit ({(before or "none")[:12]} -> {(head or "none")[:12]})')
    if not docs_only(files): return dict(reuse=False, why='code or config changed', files=files[:20])
    prior = lookup(before)
    if not prior: return dict(reuse=False, why=f'no successful push run of {PUSH_WORKFLOW} on {before[:12]}', files=files)
    return dict(reuse=True, why=f'docs-only change on top of {before[:12]}, where the push check passed', reused_from=before,
                prior=prior, files=files)


def _gh_output(**kv):
    p = os.environ.get('GITHUB_OUTPUT')
    if p:
        with open(p, 'a', encoding='utf-8') as f:
            for k, v in kv.items(): f.write(f'{k}={v}\n')


def cmd_plan(a):
    d = decide(os.environ.get('GITHUB_EVENT_NAME', ''), a.before, a.head)
    print(f"plan push fast: {'REUSE' if d['reuse'] else 'run'} - {d['why']}", flush=True)
    _gh_output(reuse=str(d['reuse']).lower())
    return 0


def cmd_reused(a):
    """Re-decides independently (does not trust the plan step), then runs static + manifest and records the reuse."""
    rep = _report('push-fast', a.out)
    d = decide(os.environ.get('GITHUB_EVENT_NAME', ''), a.before, a.head)
    rep.data['reuse'] = d
    V.static_checks(rep)
    V.manifest_check(rep)
    if not d['reuse']:
        rep.step('docs-only reuse re-check', False, why=f"the reuse conditions do not hold: {d['why']}")
        return rep.finish()
    for name in ('tests (fast set: -m "not slow")', 'installer preflight'):
        rep.step(name, True, skip=True, detail=f"REUSED: docs-only change; passed on {d['reused_from'][:12]} in push run "
                                               f"{d['prior'].get('run_id')} ({d['prior'].get('url')})", reused_from=d['reused_from'])
    return rep.finish()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('plan', 'reused'):
        p = sub.add_parser(name)
        p.add_argument('--before', default=''); p.add_argument('--head', default='')
        if name == 'reused': p.add_argument('--out', required=True)
    p = sub.add_parser('part'); p.add_argument('name', choices=list(PARTS)); p.add_argument('--out', required=True)
    p = sub.add_parser('merge'); p.add_argument('--parts', required=True); p.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    return dict(plan=cmd_plan, reused=cmd_reused, part=cmd_part, merge=cmd_merge)[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
