"""ZackBot verification levels (T04). One command, one machine-readable summary per run.

    python verify.py fast      static checks + dataset manifest + fast tests (-m "not slow") + installer preflight (Windows)
    python verify.py full      fast's static checks + ALL tests + both strict replays (24 steps) + full UI harness
                               + on Windows: build_app.bat buildcheck (PyInstaller build + exe self-test, nothing installed)
    python verify.py release   Windows PC only: full (without buildcheck) + rollback drill (build, self-test, forced failed
                               launch, verified restore) + read-only testnet reconciliation of the running bot

Options: --out DIR (default dev_out/verify), --skip-ui, --skip-replays (both recorded in the summary as skipped).
Exit code 0 = every executed step passed. Summary: <out>/<level>_<cairo-time>.json and <out>/latest_<level>.json.
Never weakens a gate: replay thresholds come from replay.STRICT, tests run unmodified, nothing is retried.
"""
import argparse, glob, hashlib, json, os, platform, re, subprocess, sys, time, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
CAIRO = ZoneInfo('Africa/Cairo')
WIN = os.name == 'nt'
now_cairo = lambda: datetime.now(CAIRO).isoformat(timespec='seconds')
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(errors='replace')
    except Exception: pass


def run(cmd, timeout, env=None, cwd=ROOT):
    """Run a command, stream nothing, return (rc, combined output, seconds)."""
    t = time.time()
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace')
        return r.returncode, (r.stdout or '') + (r.stderr or ''), round(time.time() - t, 1)
    except subprocess.TimeoutExpired as e:
        return 124, f'TIMEOUT after {timeout}s\n{(e.stdout or "")[-2000:] if isinstance(e.stdout, str) else ""}', round(time.time() - t, 1)


class Report:
    def __init__(self, level, out):
        self.level, self.out = level, out
        self.data = dict(level=level, started=now_cairo(), timezone='Africa/Cairo', steps=[], host=dict(
            os=platform.platform(), python=sys.version.split()[0], machine=platform.machine(), ci=bool(os.environ.get('CI'))))
        os.makedirs(out, exist_ok=True)

    def step(self, name, ok, seconds=0.0, skip=False, **info):
        s = dict(name=name, status='skipped' if skip else ('passed' if ok else 'FAILED'), seconds=seconds, **info)
        self.data['steps'].append(s)
        print(f"[{s['status'].upper():7}] {name} ({seconds}s)" + (f"  {info.get('why') or info.get('detail') or ''}" if not ok or skip else ''), flush=True)
        return ok or skip

    def finish(self):
        failed = [s['name'] for s in self.data['steps'] if s['status'] == 'FAILED']
        self.data.update(finished=now_cairo(), passed=not failed, failed_steps=failed)
        stamp = datetime.now(CAIRO).strftime('%Y%m%d-%H%M%S')
        for p in (os.path.join(self.out, f'{self.level}_{stamp}.json'), os.path.join(self.out, f'latest_{self.level}.json')):
            with open(p, 'w', encoding='utf-8') as f: json.dump(self.data, f, indent=1, default=str)
        print(f"\nVERIFY {self.level.upper()}: {'PASS' if not failed else 'FAIL'}"
              + (f" - failed: {', '.join(failed)}" if failed else '') + f"\nsummary: {os.path.join(self.out, f'latest_{self.level}.json')}")
        return 0 if not failed else 1


# ---------------------------------------------------------------- provenance
def git_info():
    """Commit and cleanliness. Works in a normal clone, in GitHub Actions and with the PC's external history repo."""
    cands = [[]]
    hist = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ZackBot', 'history.git')
    if os.environ.get('LOCALAPPDATA') and os.path.isdir(hist):
        cands.append([f'--git-dir={hist}', f'--work-tree={ROOT}'])
    for pre in cands:
        rc, out, _ = run(['git'] + pre + ['rev-parse', 'HEAD'], 30)
        if rc == 0 and re.fullmatch(r'[0-9a-f]{40}', out.strip()):
            _, br, _ = run(['git'] + pre + ['rev-parse', '--abbrev-ref', 'HEAD'], 30)
            _, st, _ = run(['git'] + pre + ['status', '--porcelain'], 60)
            return dict(commit=out.strip(), branch=br.strip(), dirty=bool(st.strip()), dirty_files=st.strip().splitlines()[:20])
    return dict(commit=os.environ.get('GITHUB_SHA'), branch=os.environ.get('GITHUB_REF_NAME'), dirty=None)


def dependency_versions():
    from importlib import metadata
    names = []
    for f in ('requirements.txt', 'requirements-dev.txt', 'requirements-ui.txt'):
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            names += [re.split(r'[=<>!~ ]', l.strip())[0] for l in open(p) if l.strip() and not l.startswith(('#', '-r'))]
    out = {}
    for n in dict.fromkeys(names):
        try: out[n] = metadata.version(n)
        except metadata.PackageNotFoundError: out[n] = None
    return out


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- steps
def static_checks(rep):
    t = time.time()
    py = [p for p in glob.glob(os.path.join(ROOT, '**', '*.py'), recursive=True)
          if not any(x in p for x in (os.sep + 'dev_out', os.sep + '__pycache__', os.sep + '.git'))]
    bad = []
    for p in py:
        try: compile(open(p, encoding='utf-8').read(), p, 'exec')
        except SyntaxError as e: bad.append(f'{os.path.relpath(p, ROOT)}:{e.lineno} {e.msg}')
    rep.step('static: every .py file compiles', not bad, round(time.time() - t, 1), files=len(py), errors=bad[:10])
    # secrets / private files must never be in the tree
    t = time.time()
    forbidden = [p for p in ('config.env', 'session.json', 'state.json', 'settings.json', 'trades.csv') if os.path.exists(os.path.join(ROOT, p))]
    key_rx = re.compile(r'(?<![A-Za-z0-9])[A-Za-z0-9]{64}(?![A-Za-z0-9])')
    hits = []
    for p in glob.glob(os.path.join(ROOT, '**', '*'), recursive=True):
        rel = os.path.relpath(p, ROOT)
        if (not os.path.isfile(p) or rel.split(os.sep)[0] in ('data', 'data1h', 'data_long', 'dev_out', 'research', '.git')
                or p.endswith(('.svg', '.png', '.ico', '.pyc', '.bundle', '.exe', '.json')) or os.path.getsize(p) > 2_000_000):
            continue
        try: txt = open(p, encoding='utf-8', errors='ignore').read()
        except OSError: continue
        for m in key_rx.findall(txt):
            if not re.fullmatch(r'[0-9a-fA-F]{64}', m) and len(set(m)) > 4:     # hex = checksums; 'CCCC...' = fixtures
                hits.append(f'{rel}: {m[:6]}...')
    rep.step('static: no private files or key-like strings in the tree', not forbidden and not hits, round(time.time() - t, 1),
             forbidden=forbidden, key_like=hits[:10])


def manifest_check(rep):
    t = time.time()
    p = os.path.join(ROOT, 'DATA_MANIFEST.json')
    man = json.load(open(p, encoding='utf-8'))
    missing, changed = [], []
    for rel, meta in man['files'].items():
        f = os.path.join(ROOT, *rel.split('/'))
        if not os.path.exists(f): missing.append(rel)
        elif sha256_file(f) != meta['sha256']: changed.append(rel)
    rep.data['dataset'] = dict(manifest_sha256=sha256_file(p), files=len(man['files']), missing=missing, changed=changed)
    return rep.step('dataset: every file matches DATA_MANIFEST.json', not missing and not changed, round(time.time() - t, 1),
                    files=len(man['files']), missing=missing[:10], changed=changed[:10])


def pytest_step(rep, name, args, timeout):
    junit = os.path.join(rep.out, f'junit_{rep.level}.xml')
    rc, out, sec = run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', f'--junitxml={junit}'] + args, timeout)
    counts = dict(tests=0, failures=0, errors=0, skipped=0)
    try:
        r = ET.parse(junit).getroot(); s = r if r.tag == 'testsuite' else r.find('testsuite')
        counts = {k: int(s.get(k, 0)) for k in counts}
    except Exception: pass
    counts['passed'] = counts['tests'] - counts['failures'] - counts['errors'] - counts['skipped']
    rep.data.setdefault('tests', {})[name] = counts
    tail = [l for l in out.splitlines() if l.startswith(('FAILED', 'ERROR'))][:15]
    return rep.step(name, rc == 0 and counts['tests'] > 0, sec, counts=counts, failed=tail,
                    why=None if rc == 0 else (tail[:3] or out.strip().splitlines()[-1:]))


REPLAYS = [('replay 1: MOM pyramid + DCA + Squeeze', []), ('replay 2: breakout + bear shorts + Donchian', ['3000', 'replay_scenario2.json'])]
RX = dict(matched=r'TRADES matched (\d+) \(([\d.]+)%\)', med=r'median \|dR\| ([\d.]+)', p95=r'p95 \|dR\| ([\d.]+)',
          ret=r'return gap ([\d.]+) pp', dd=r'DD gap ([\d.]+) pp', mism=r'position mismatches (\{.*?\})')


def replay_step(rep, name, args):
    out_dir = os.path.join(rep.out, 'replay_' + name.split(':')[0].replace(' ', ''))
    env = dict(os.environ, ZB_SIM_STEPS='24', ZB_REPLAY_STRICT='1', ZB_OUT=out_dir)
    rc, out, sec = run([sys.executable, 'test_engine_sim.py'] + args, 3600, env=env)
    m = {}
    for k, rx in RX.items():
        g = re.search(rx, out)
        if g: m[k] = g.groups() if k == 'matched' else g.group(1)
    met = dict(matched=int(m['matched'][0]), matched_pct=float(m['matched'][1]), median_abs_dR=float(m['med']), p95_abs_dR=float(m['p95']),
               return_gap_pp=float(m['ret']), dd_gap_pp=float(m['dd']), position_mismatches=m.get('mism')) if 'matched' in m and 'ret' in m else {}
    gate = re.search(r'^GATE .* -> (PASS|FAIL)', out, re.M)
    ok = rc == 0 and gate is not None and gate.group(1) == 'PASS' and bool(met)
    rep.data.setdefault('replays', {})[name] = dict(met, gate=gate.group(1) if gate else None, steps=24, strict=True)
    return rep.step(name, ok, sec, **met, why=None if ok else (out.strip().splitlines()[-3:]))


def ui_seeds(rep):
    """Seed files (seed_history.json, seed_missed.json) for the UI harness. Preferred: the ones replay 1 wrote in THIS run
    (same data on GitHub, the PC and the sandbox); fallback: dev_out/ from an earlier local replay. dev_out/ is not in Git,
    so without this a clean checkout (GitHub) has no closed trades and the harness's calendar-day flow cannot pass."""
    run_dir = os.path.join(rep.out, 'replay_' + REPLAYS[0][0].split(':')[0].replace(' ', ''))
    for src, d in (('replay 1 (this run)', run_dir), ('dev_out (earlier local replay)', os.path.join(ROOT, 'dev_out'))):
        f = sorted(glob.glob(os.path.join(d, 'seed_*.json')))
        if any(os.path.basename(x) == 'seed_history.json' for x in f): return f, src
    return [], 'none'


def ui_step(rep):
    ui_out = os.path.join(rep.out, 'ui')
    env = dict(os.environ, ZB_OUT=ui_out, PYTHONIOENCODING='utf-8')
    if WIN and not env.get('PLAYWRIGHT_BROWSERS_PATH'):
        env['PLAYWRIGHT_BROWSERS_PATH'] = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ZackBot', 'ms-playwright')
    seeds, src = ui_seeds(rep)                  # closed-trade history for the harness (the calendar/day flow needs some)
    os.makedirs(ui_out, exist_ok=True)
    import shutil
    for f in seeds: shutil.copy(f, ui_out)
    rc, out, sec = run([sys.executable, 'test_app_ui.py'], 2400, env=env)
    g = re.search(r'UI HARNESS: (PASS|FAIL) - (\d+)/(\d+) checks passed', out)
    summ = os.path.join(ui_out, 'ui_baseline', 'summary.json')
    info = dict(seeds=src, checks_passed=int(g.group(2)) if g else None, checks_total=int(g.group(3)) if g else None,
                evidence=os.path.relpath(summ, ROOT) if os.path.exists(summ) else None,
                failed=[l for l in out.splitlines() if l.startswith('FAIL ')][:10])
    rep.data['ui'] = info
    return rep.step('UI harness (all tabs x 3 widths, flows, API failures)', rc == 0 and g is not None and g.group(1) == 'PASS', sec, **info)


def installer_mode(rep, mode, timeout):
    """build_app.bat preflight | buildcheck | drill, non-interactive (ZB_NOPAUSE). Windows only."""
    if not WIN:
        return rep.step(f'installer {mode}', True, skip=True, why='Windows only')
    env = dict(os.environ, ZB_NOPAUSE='1')
    rc, out, sec = run(['cmd', '/d', '/c', os.path.join(ROOT, 'build_app.bat'), mode], timeout, env=env)
    logname = {'preflight': 'build_preflight.log', 'buildcheck': 'build_check.log', 'drill': 'build.log'}[mode]
    log = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ZackBot', logname)
    text = open(log, encoding='utf-8', errors='replace').read() if os.path.exists(log) else ''
    exe = re.search(r'new exe sha256 ([0-9A-F]{64})', text)
    bid = re.search(r'build id (\S+)', text)
    want = {'preflight': 'PREFLIGHT_OK', 'buildcheck': 'BUILDCHECK_OK', 'drill': 'DRILL_PASSED'}[mode]
    ok = rc == 0 and want in text
    info = dict(build_id=bid.group(1) if bid else None, exe_sha256=exe.group(1) if exe else None, log=log,
                why=None if ok else ([l for l in text.splitlines() if 'FAILED' in l or 'ROLLBACK' in l][-3:] or out.strip().splitlines()[-3:]))
    if exe: rep.data['exe'] = dict(build_id=info['build_id'], sha256=exe.group(1), source=mode)
    return rep.step(f'installer {mode}', ok, sec, **info)


def reconcile_step(rep):
    """Read-only check of the RUNNING bot through its own authenticated /api/status (never places or changes anything)."""
    t = time.time()
    ok, info, last_err = False, {}, None
    while time.time() - t < 180 and not ok:          # right after a restart the price feed needs up to a minute: wait, never relax
        try:
            sess = json.load(open(os.path.join(os.environ['LOCALAPPDATA'], 'ZackBot', 'session.json'), encoding='utf-8'))
            req = urllib.request.Request(f"http://127.0.0.1:{sess.get('port', 8765)}/api/status", headers={'X-ZB-Token': sess['token']})
            st = json.load(urllib.request.urlopen(req, timeout=20))
            h = st.get('health', {}); lots = st.get('lots', [])
            info = dict(mode=st.get('mode'), build=st.get('build'), engine=h.get('engine'), exchange=h.get('exchange'),
                        lots=len(lots), unprotected=h.get('unprotected'), untracked=h.get('untracked'), orphans=h.get('orphans'),
                        errors=h.get('errors', [])[:5])
            ok = (h.get('engine') == 'ok' and h.get('exchange') == 'ok' and not h.get('unprotected') and not h.get('untracked')
                  and not h.get('orphans') and all(l.get('protected') for l in lots))
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
        if not ok: time.sleep(10)
    if not info:
        return rep.step('testnet reconciliation (running bot)', False, round(time.time() - t, 1), why=last_err)
    rep.data['reconciliation'] = info
    return rep.step('testnet reconciliation (running bot)', ok, round(time.time() - t, 1), **info,
                    why=None if ok else 'engine/exchange not ok, or an unprotected/untracked position, or orphan orders')


# ---------------------------------------------------------------- levels
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('level', choices=['fast', 'full', 'release'])
    ap.add_argument('--out', default=os.path.join(ROOT, 'dev_out', 'verify'))
    ap.add_argument('--skip-ui', action='store_true'); ap.add_argument('--skip-replays', action='store_true')
    a = ap.parse_args()
    if a.level == 'release' and not WIN:
        print('verify release runs on the Windows PC only (rollback drill + running-bot reconciliation)'); return 2
    rep = Report(a.level, os.path.abspath(a.out))
    rep.data['git'] = git_info()
    rep.data['dependencies'] = dependency_versions()
    bi = os.path.join(ROOT, 'build_info.py')
    rep.data['build_id'] = re.search(r"'(.*)'", open(bi).read()).group(1) if os.path.exists(bi) else 'dev'
    print(f"VERIFY {a.level.upper()} - commit {str(rep.data['git'].get('commit'))[:12]} - {rep.data['started']} Cairo", flush=True)

    static_checks(rep)
    manifest_check(rep)
    if a.level == 'fast':
        pytest_step(rep, 'tests (fast set: -m "not slow")', ['-m', 'not slow', 'tests'], 1800)
        installer_mode(rep, 'preflight', 600)
    else:
        pytest_step(rep, 'tests (all)', ['tests'], 3600)
        for name, args in REPLAYS:
            if a.skip_replays: rep.step(name, True, skip=True, why='--skip-replays')
            else: replay_step(rep, name, args)
        if a.skip_ui: rep.step('UI harness (all tabs x 3 widths, flows, API failures)', True, skip=True, why='--skip-ui')
        else: ui_step(rep)
        if a.level == 'full': installer_mode(rep, 'buildcheck', 3600)
        if a.level == 'release':
            installer_mode(rep, 'drill', 3600)
            reconcile_step(rep)
    return rep.finish()


if __name__ == '__main__':
    sys.exit(main())
