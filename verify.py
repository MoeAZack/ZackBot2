"""ZackBot verification levels (T04). One command, one machine-readable summary per run.

    python verify.py fast      static checks + dataset manifest + fast tests (-m "not slow") + installer preflight (Windows)
    python verify.py full      fast's static checks + ALL tests + both strict replays (24 steps) + full UI harness
                               + on Windows: build_app.bat buildcheck (PyInstaller build + exe self-test, nothing installed)
    python verify.py release   Windows PC only: full (without buildcheck) + rollback drill (build, self-test, forced failed
                               launch, verified restore) + read-only testnet reconciliation of the running bot

Options: --out DIR (default dev_out/verify). There are no skip switches: a named level always runs all of its gates
(T04 review: a full/release run with an omitted gate must never report PASS). For a quick look, run pytest or the
scripts directly. Exit code 0 = every executed step passed; the only skips are Windows-only steps on other systems. Summary: <out>/<level>_<cairo-time>.json and <out>/latest_<level>.json.
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
    forbidden, hits = secret_scan(ROOT)
    rep.step('static: no private files or key-like strings in the tree', not forbidden and not hits, round(time.time() - t, 1),
             forbidden=forbidden, key_like=hits[:10])


PRIVATE_NAMES = ('config.env', 'session.json', 'state.json', 'settings.json', 'trades.csv', 'history.json', 'missed.json')
LOCAL_ONLY = ('.git', 'dev_out', 'verify_out', '__pycache__', '.pytest_cache')        # never committed (.gitignore) - not scanned
SECRET_RX = [('key-like 64-char string', re.compile(r'(?<![A-Za-z0-9])[A-Za-z0-9]{64}(?![A-Za-z0-9])')),
             ('private key block', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')),
             ('Telegram bot token', re.compile(r'(?<![0-9])[0-9]{8,10}:AA[A-Za-z0-9_-]{33}(?![A-Za-z0-9_-])')),
             ('DPAPI blob', re.compile(r'dpapi:[A-Za-z0-9+/=]{40,}'))]


def secret_scan(root):
    """Current-tree guard (T04 review P2): private app files by NAME at any depth, and secret-looking CONTENT in every text
    file up to 2 MB - JSON included - except the market-data folders. Git-history scanning is T04b."""
    forbidden, hits = [], []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in LOCAL_ONLY]
        reld = os.path.relpath(dp, root)
        top = reld.split(os.sep)[0]
        for fn in fns:
            rel = os.path.normpath(os.path.join(reld, fn))
            if fn in PRIVATE_NAMES: forbidden.append(rel)
            p = os.path.join(dp, fn)
            if (top in ('data', 'data1h', 'data_long') or fn.endswith(('.svg', '.png', '.ico', '.pyc', '.bundle', '.exe', '.zip', '.mp4'))
                    or os.path.getsize(p) > 2_000_000):
                continue
            try: txt = open(p, encoding='utf-8', errors='ignore').read()
            except OSError: continue
            for what, rx in SECRET_RX:
                for m in rx.findall(txt):
                    if what.startswith('key-like') and (re.fullmatch(r'[0-9a-fA-F]{64}', m) or len(set(m)) <= 4):
                        continue                                  # hex = checksums; 'CCCC...' = test fixtures
                    if what.startswith('Telegram') and m.startswith('123456789:'):
                        continue                                  # the tests' obviously fake bot id
                    hits.append(f'{rel}: {what} {m[:6]}...')
    return forbidden, hits


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


def installer_command(mode):
    """T03b: the drill has its own fixed launcher; build_app.bat takes only preflight / buildcheck (or nothing = install)."""
    if mode == 'drill':
        return ['cmd', '/d', '/c', os.path.join(ROOT, 'rollback_drill.bat')]
    assert mode in ('preflight', 'buildcheck'), mode
    return ['cmd', '/d', '/c', os.path.join(ROOT, 'build_app.bat'), mode]


def installer_mode(rep, mode, timeout):
    """build_app.bat preflight | buildcheck, rollback_drill.bat (drill); non-interactive (ZB_NOPAUSE). Windows only."""
    if not WIN:
        return rep.step(f'installer {mode}', True, skip=True, why='Windows only')
    env = dict(os.environ, ZB_NOPAUSE='1')
    rc, out, sec = run(installer_command(mode), timeout, env=env)
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


def runtime_problems(st):
    """Fail-closed check of the running bot's /api/status (T04 review round 2): every safety field must be PRESENT, well
    formed and exactly safe - a missing, truncated or malformed answer is a problem, never 'nothing to report'.
    Returns the list of problems; empty = PAPER (testnet), engine/exchange ok, no recent errors, nothing unprotected,
    nothing untracked, no orphan orders, and every lot explicitly protected."""
    if not isinstance(st, dict): return ['status is not a JSON object']
    p, h, lots = [], st.get('health'), st.get('lots')
    if st.get('mode') != 'PAPER': p.append(f"mode is {st.get('mode')!r}, not 'PAPER'")
    if not isinstance(h, dict): return p + ['health missing or malformed']
    for k in ('engine', 'exchange'):
        if h.get(k) != 'ok': p.append(f'health.{k} is {h.get(k)!r}, not ok')
    for k, kinds in (('errors', (list,)), ('unprotected', (list,)), ('untracked', (dict, list))):
        v = h.get(k, None)
        if not isinstance(v, kinds): p.append(f'health.{k} missing or malformed ({type(v).__name__})')
        elif len(v): p.append(f'health.{k} not empty ({len(v)})')
    o = h.get('orphans', None)
    if type(o) is not int: p.append(f'health.orphans missing or malformed ({type(o).__name__})')
    elif o != 0: p.append(f'health.orphans = {o}')
    if not isinstance(lots, list): p.append('lots missing or malformed')
    else:
        bad = [i for i, l in enumerate(lots) if not isinstance(l, dict) or l.get('protected') is not True]
        if bad: p.append(f'{len(bad)} lot(s) not explicitly protected')
    return p


def runtime_ok(st):
    return not runtime_problems(st)


def bot_status():
    sess = json.load(open(os.path.join(os.environ['LOCALAPPDATA'], 'ZackBot', 'session.json'), encoding='utf-8'))
    req = urllib.request.Request(f"http://127.0.0.1:{sess.get('port', 8765)}/api/status", headers={'X-ZB-Token': sess['token']})
    return json.load(urllib.request.urlopen(req, timeout=20))


def reconcile_step(rep, phase='after drill', wait=180):
    """Read-only check of the RUNNING bot through its own authenticated /api/status (never places or changes anything).
    Must report mode PAPER (testnet): a LIVE bot never passes, and before the drill it means the drill is not started."""
    t = time.time()
    ok, info, last_err = False, {}, None
    while not ok:                                    # right after a restart the price feed needs up to a minute: wait, never relax
        try:
            st = bot_status()
            h = st.get('health', {}); lots = st.get('lots', [])
            h = h if isinstance(h, dict) else {}; lots = lots if isinstance(lots, list) else []
            info = dict(mode=st.get('mode'), build=st.get('build'), engine=h.get('engine'), exchange=h.get('exchange'),
                        lots=len(lots), unprotected=h.get('unprotected'), untracked=h.get('untracked'), orphans=h.get('orphans'),
                        errors=(h.get('errors') or [])[:5] if isinstance(h.get('errors'), list) else h.get('errors'),
                        problems=runtime_problems(st))
            ok = not info['problems']
            if st.get('mode') != 'PAPER': break      # LIVE (or unknown) never becomes acceptable by waiting
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
        if ok or time.time() - t >= wait: break
        time.sleep(10)
    name = f'testnet reconciliation {phase} (running bot)'
    if not info:
        return rep.step(name, False, round(time.time() - t, 1), why=last_err)
    rep.data.setdefault('reconciliation', {})[phase] = info
    why = None if ok else '; '.join(info.get('problems') or ['status check failed'])
    return rep.step(name, ok, round(time.time() - t, 1), **info, why=why)


def release_steps(rep):
    """The drill stops and restarts ZackBot, so it only starts after a read-only gate proves the running bot is PAPER
    (testnet), healthy and fully protected (T04 review P1). Afterwards the same gate must pass again."""
    if not reconcile_step(rep, 'before drill', wait=60):
        return rep.step('installer drill', False, why='not started: the pre-drill gate failed (bot must be PAPER, healthy, all protected)')
    installer_mode(rep, 'drill', 3600)
    return reconcile_step(rep, 'after drill')


# ---------------------------------------------------------------- levels
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('level', choices=['fast', 'full', 'release'])
    ap.add_argument('--out', default=os.path.join(ROOT, 'dev_out', 'verify'))
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
        for name, args in REPLAYS: replay_step(rep, name, args)
        ui_step(rep)
        if a.level == 'full': installer_mode(rep, 'buildcheck', 3600)
        if a.level == 'release': release_steps(rep)
    return rep.finish()


if __name__ == '__main__':
    sys.exit(main())
