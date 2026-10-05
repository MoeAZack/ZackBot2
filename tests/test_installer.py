"""T03: installer rollback drill - static checks of the Windows batch files and the app's --simulate-failed-launch switch.

The batch files cannot run on the Linux dev box, so these tests pin the properties the drill depends on; the drill
itself (build_app.bat drill) is run on the owner's Windows PC.
"""
import os, re, socket, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATS = sorted(f for f in os.listdir(ROOT) if f.lower().endswith('.bat'))


def _read(name):
    with open(os.path.join(ROOT, name), 'rb') as f:
        return f.read()


def _lines(name):
    return _read(name).decode('ascii').split('\r\n')


def test_batch_files_are_crlf_ascii():
    for b in BATS:
        raw = _read(b)
        raw.decode('ascii')                                   # cmd.exe + non-ASCII = surprises
        assert b'\n' not in raw.replace(b'\r\n', b''), f'{b}: LF-only line endings (cmd.exe can mis-handle labels)'


def test_every_goto_and_call_target_exists():
    for b in BATS:
        text = '\n'.join(_lines(b))
        labels = {m.lower() for m in re.findall(r'^:([A-Za-z0-9_]+)', text, re.M)}
        targets = {t.lower() for t in re.findall(r'\bgoto\s+:?([A-Za-z0-9_]+)', text, re.I)} - {'eof'}
        targets |= {t.lower() for t in re.findall(r'\bcall\s+:([A-Za-z0-9_]+)', text, re.I)}
        missing = targets - labels
        assert not missing, f'{b}: goto/call targets without a label: {sorted(missing)}'


import pytest
# build_app.bat copies the source to a staging folder WITHOUT itself (robocopy /XF build_app.bat) and runs these tests
# there; its own checks can only run in the repository (run_checks, bare pytest, CI).
needs_installer = pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, 'build_app.bat')),
                                     reason='staging copy: build_app.bat is not copied into the installer staging folder')


def _idx(lines, pred, what):
    hits = [i for i, l in enumerate(lines) if pred(l)]
    assert hits, f'build_app.bat: {what} not found'
    return hits


@needs_installer
def test_mirror_and_shortcuts_only_after_confirmed_launch():
    L = _lines('build_app.bat')
    ping_new = _idx(L, lambda l: 'ping %BUILD_ID%' in l, 'post-launch ping of the new build')[0]
    mirror = _idx(L, lambda l: l.startswith('robocopy') and '"%DST%"' in l, 'source mirror')[0]
    shortcut = _idx(L, lambda l: 'CreateShortcut' in l, 'shortcut update')[0]
    done = _idx(L, lambda l: l.startswith('echo BUILD_DONE'), 'BUILD_DONE')[0]
    assert ping_new < mirror < done and ping_new < shortcut < done, 'mirror/shortcuts must follow the confirmed launch'
    rb = _idx(L, lambda l: l == ':rollback', ':rollback label')[0]
    assert mirror < rb, 'no source mirror on the rollback path'


@needs_installer
def test_simulate_switch_reaches_the_exe_only_in_drill_mode():
    L = _lines('build_app.bat')
    sets = [l for l in L if 'LAUNCHARGS=' in l and 'simulate-failed-launch' in l]
    assert sets and all(l.startswith('if "%DRILL%"=="1" set LAUNCHARGS=') for l in sets), sets
    assert any(l.strip() == 'set LAUNCHARGS=' for l in L), 'LAUNCHARGS must be cleared first'
    launch = [l for l in L if l.startswith('start "" "%APPDIR%\\ZackBot.exe"')]
    # exactly two launches: the new exe (with %LAUNCHARGS%) and the restored exe on rollback (never with the switch)
    assert len(launch) == 2 and launch[0].endswith('%LAUNCHARGS%') and 'simulate' not in launch[1], launch
    assert any(l == 'if /i "%~1"=="drill" set DRILL=1' for l in L) and any(l == 'set DRILL=0' for l in L)


@needs_installer
def test_rollback_restores_verified_hash_then_proves_old_build_runs():
    L = _lines('build_app.bat')
    rb = _idx(L, lambda l: l == ':rollback', ':rollback')[0]
    body = L[rb:]
    order = [next(i for i, l in enumerate(body) if pred(l)) for pred in (
        lambda l: l.startswith('copy /y "%APPDIR%\\ZackBot.prev.exe"'),
        lambda l: 'RBHASH' in l and 'OLDHASH' in l,
        lambda l: l.startswith('start "" "%APPDIR%\\ZackBot.exe"'),
        lambda l: 'ping %OLDBUILD%' in l)]
    assert order == sorted(order), 'rollback must: restore -> hash check -> start -> ping the old build'
    assert any(l.startswith('if "%DRILL%"=="1" if "%OLDBUILD%"==""') for l in L), 'drill needs the old build id up front'
    step1 = _idx(L, lambda l: l.startswith('echo [1/8]'), 'step 1')[0]
    assert any('goto fail_drill_noold' in l for l in L[:step1]), 'drill must refuse before step 1 when nothing is installed'


def test_simulate_failed_launch_exits_before_anything_starts():
    """The real app module: exits with code 3, binds no port and writes no session.json."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, LOCALAPPDATA=tmp, PYTHONIOENCODING='utf-8')
        # If the switch were ever broken, main() must NOT become a real app on the build PC: private free port, and the
        # App/engine start is replaced by an immediate error, so the test fails fast instead of serving or trading.
        code = ('import sys, socket\n'
                'import app\n'
                's = socket.socket(); s.bind(("127.0.0.1", 0)); app.PORT = s.getsockname()[1]; s.close()\n'
                'def _no_app(*a, **k): raise SystemExit("REACHED_APP_START")\n'
                'app.App = _no_app\n'
                'app.job_worker = lambda: None\n'
                'app.open_window = lambda *a, **k: None\n'
                'app.message_box = lambda *a, **k: None\n'
                'busy = []\n'
                '_b = socket.socket.bind\n'
                'def _bind(s, a): busy.append(a); return _b(s, a)\n'
                'socket.socket.bind = _bind\n'
                'sys.argv = ["app.py", "--no-window", "--simulate-failed-launch"]\n'
                'try:\n'
                '    app.main()\n'
                'except SystemExit as e:\n'
                '    print("EXIT", e.code, "BINDS", busy); raise\n'
                'print("RETURNED")\n')
        t = time.time()
        r = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 3, (r.returncode, r.stdout[-500:], r.stderr[-800:])
        assert 'EXIT 3 BINDS []' in r.stdout, r.stdout[-300:]
        assert not os.path.exists(os.path.join(tmp, 'ZackBot', 'session.json')), 'session.json must not be written'
        assert time.time() - t < 55


def test_switch_is_inert_without_the_flag():
    src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    assert src.count('--simulate-failed-launch') == 1, 'the switch must be read in exactly one place'
    i = src.index("if '--simulate-failed-launch' in sys.argv:")
    assert src.index("if '--selftest' in sys.argv:") < i < src.index('if port_in_use():'), 'switch must sit before the port check'


# ---------------------------------------------------------------- installer_check.ps1 (T03 review: hash failed from batch)
import hashlib, hmac as _hmac, http.server, json, shutil, threading, urllib.parse

PS1 = os.path.join(ROOT, 'installer_check.ps1')


def _ps_runner():
    """How the installer runs the helper: on Windows cmd.exe -> Windows PowerShell 5.1 (exactly like build_app.bat);
    elsewhere PowerShell 7 if installed (dev box / CI). None -> skip."""
    if os.name == 'nt' and shutil.which('powershell'):
        # Windows PowerShell 5.1 called directly with an argument list (review round 2: building a "cmd /c ..." string
        # inside a Python list made the -File path carry literal quotes). The batch-level path is covered separately by
        # test_build_app_preflight_from_cmd, which runs the real build_app.bat.
        ps = shutil.which('powershell')
        return lambda args, env=None: subprocess.run([ps, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', PS1] + list(args),
                                                     capture_output=True, text=True, env=env, timeout=120)
    pw = shutil.which('pwsh') or ('/opt/pwsh/pwsh' if os.path.exists('/opt/pwsh/pwsh') else None)
    if pw:
        return lambda args, env=None: subprocess.run([pw, '-NoProfile', '-File', PS1] + list(args),
                                                     capture_output=True, text=True, env=env, timeout=120)
    return None


RUN_PS = _ps_runner()
needs_ps = pytest.mark.skipif(RUN_PS is None, reason='no PowerShell on this machine')


def _env(**kw):
    e = dict(os.environ); e.update(kw); return e


@needs_ps
def test_hash_helper_matches_python_sha256():
    want = hashlib.sha256(_read('README.md')).hexdigest().upper()
    r = RUN_PS(['hash', os.path.join(ROOT, 'README.md')])
    assert r.returncode == 0 and r.stdout.strip() == want, (r.returncode, r.stdout, r.stderr[-400:])


@needs_ps
def test_hash_helper_ignores_a_foreign_psmodulepath():
    """The T03 failure: a PSModulePath from another PowerShell made Get-FileHash disappear. The helper must not care."""
    foreign = (r'C:\Program Files\PowerShell\7\Modules;C:\nowhere\Modules' if os.name == 'nt' else '/nowhere/ps7/Modules')
    want = hashlib.sha256(_read('README.md')).hexdigest().upper()
    r = RUN_PS(['hash', os.path.join(ROOT, 'README.md')], env=_env(PSModulePath=foreign))
    assert r.returncode == 0 and r.stdout.strip() == want, (r.returncode, r.stdout, r.stderr[-400:])


@needs_ps
def test_hash_helper_reports_why_it_failed():
    t = time.time()
    r = RUN_PS(['hash', os.path.join(ROOT, 'no-such-file.exe')])
    assert r.returncode == 1 and r.stdout.strip() == '' and 'FAILED' in r.stderr and 'not found' in r.stderr, (r.stdout, r.stderr)
    assert time.time() - t < 30, 'a missing file must not wait for the scanner-lock retries'


def test_hash_helper_uses_no_cmdlets():
    """Only .NET and the language: a module problem in the caller's environment cannot break it again."""
    src = '\n'.join(l for l in open(PS1, encoding='utf-8').read().split('\n') if not l.lstrip().startswith('#'))
    own = set(re.findall(r'^function\s+([A-Za-z]+-[A-Za-z0-9]+)', src, re.M))
    used = set(re.findall(r'(?<![\w.$:\[-])([A-Z][a-z]+-[A-Z][A-Za-z0-9]+)\b', src)) - own
    assert not used, f'installer_check.ps1 calls cmdlets: {sorted(used)}'


@needs_ps
def test_ping_helper_requires_build_id_and_hmac_proof():
    tok, state = 'tok-' + os.urandom(4).hex(), {'build': 'B1', 'key': None}
    state['key'] = tok

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            n = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('nonce', [''])[0]
            body = json.dumps(dict(app='zackbot', version='3.2', build=state['build'],
                                   proof=_hmac.new(state['key'].encode(), n.encode(), 'sha256').hexdigest())).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, 'ZackBot'))
        with open(os.path.join(tmp, 'ZackBot', 'session.json'), 'w') as f: json.dump({'token': tok}, f)
        env = _env(LOCALAPPDATA=tmp, ZB_PING_PORT=str(srv.server_address[1]))   # never the real bot's port
        try:
            ok = RUN_PS(['ping', 'B1', '5'], env=env)
            assert ok.returncode == 0 and 'ping ok' in ok.stdout, (ok.stdout, ok.stderr[-300:])
            assert RUN_PS(['ping', 'B2', '2'], env=env).returncode == 1, 'wrong build id must fail'
            state['key'] = 'someone-else'
            assert RUN_PS(['ping', 'B1', '2'], env=env).returncode == 1, 'a process without our token must fail'
        finally:
            srv.shutdown()


@needs_installer
def test_installer_isolates_powershell_and_checks_the_helper_first():
    L = _lines('build_app.bat')
    first_ps = _idx(L, lambda l: 'powershell' in l.lower() and not l.startswith('rem'), 'first PowerShell call')[0]
    clear = _idx(L, lambda l: l == 'set "PSModulePath="', 'PSModulePath reset')[0]
    pre = _idx(L, lambda l: l.startswith('call :hash') and 'PREHASH' in l, 'checksum preflight')[0]
    tests = _idx(L, lambda l: '-m pytest' in l, 'test step')[0]
    assert clear < first_ps and pre < tests, 'reset PSModulePath before any PowerShell; checksum preflight before the tests'
    assert any("2^>^>\"%LOG%\"" in l for l in L if 'hash "%~1"' in l), 'helper errors must reach build.log'


@needs_installer
def test_preflight_mode_is_non_destructive():
    """build_app.bat preflight: own staging folder + own log, exits right after the checksum check (before step 2),
    never pauses - so it cannot stop, swap or roll back anything and cannot hang a test."""
    L = _lines('build_app.bat')
    own_stage = _idx(L, lambda l: l == 'if "%PREFLIGHT%"=="1" set STAGE=%ROOT%\\staging_preflight', 'preflight staging')[0]
    own_log = _idx(L, lambda l: l == 'if "%PREFLIGHT%"=="1" set LOG=%ROOT%\\build_preflight.log', 'preflight log')[0]
    first_log_write = _idx(L, lambda l: '> "%LOG%"' in l, 'first log write')[0]
    first_rmdir = _idx(L, lambda l: l.startswith('if exist "%STAGE%" rmdir'), 'staging cleanup')[0]
    pre_ok = _idx(L, lambda l: l.startswith('echo PREFLIGHT_OK'), 'PREFLIGHT_OK')[0]
    exit0 = next(i for i in range(pre_ok, len(L)) if L[i] == 'exit /b 0')
    step2 = _idx(L, lambda l: l.startswith('echo [2/8]'), 'step 2')[0]
    assert own_stage < first_log_write and own_log < first_log_write and own_stage < first_rmdir
    assert pre_ok < exit0 < step2, 'preflight must exit before step 2'
    f = _idx(L, lambda l: l == ':fail', ':fail')[0]
    pause = next(i for i in range(f, len(L)) if L[i] == 'pause')
    assert any(L[i] == 'if "%PREFLIGHT%"=="1" exit /b 1' for i in range(f, pause)), 'preflight failures must not pause'


@needs_installer
@pytest.mark.skipif(os.name != 'nt', reason='runs the real build_app.bat through cmd.exe (Windows only)')
def test_build_app_preflight_from_cmd():
    """The real batch file, the real helper, Windows PowerShell 5.1 - also with a PowerShell-7-style PSModulePath.
    Must not touch the real staging folder or build.log."""
    root = os.path.join(os.environ['LOCALAPPDATA'], 'ZackBot')
    def snap():
        out = {}
        for f in ('build.log',):
            p = os.path.join(root, f)
            out[f] = hashlib.sha256(open(p, 'rb').read()).hexdigest() if os.path.exists(p) else None
        st = os.path.join(root, 'staging')
        out['staging'] = sorted(os.listdir(st)) if os.path.isdir(st) else None
        out['staging_mtime'] = os.path.getmtime(st) if os.path.isdir(st) else None
        return out
    before = snap()
    bat = os.path.join(ROOT, 'build_app.bat')
    for env in (None, _env(PSModulePath=r'C:\Program Files\PowerShell\7\Modules;C:\nowhere\Modules')):
        r = subprocess.run(['cmd', '/d', '/c', bat, 'preflight'], capture_output=True, text=True, env=env, timeout=300,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0 and 'PREFLIGHT_OK' in r.stdout, (r.returncode, r.stdout[-800:], r.stderr[-400:])
    assert snap() == before, 'preflight must not touch the real staging folder or build.log'
    assert not os.path.exists(os.path.join(root, 'staging_preflight')), 'preflight staging folder must be removed'


@needs_installer
def test_buildcheck_mode_builds_but_never_installs():
    """build_app.bat buildcheck (verify full on Windows): own staging folder + own log, exits right after the exe self-test
    and hash - before step 7, so nothing is backed up, stopped, swapped or installed."""
    L = _lines('build_app.bat')
    own_stage = _idx(L, lambda l: l == 'if "%BUILDCHECK%"=="1" set STAGE=%ROOT%\\staging_buildcheck', 'buildcheck staging')[0]
    own_log = _idx(L, lambda l: l == 'if "%BUILDCHECK%"=="1" set LOG=%ROOT%\\build_check.log', 'buildcheck log')[0]
    first_log_write = _idx(L, lambda l: '> "%LOG%"' in l, 'first log write')[0]
    selftest = _idx(L, lambda l: '--selftest "%STAGE%\\selftest.json"' in l, 'new exe self-test')[0]
    newhash = _idx(L, lambda l: l.startswith('echo   new exe sha256'), 'new exe hash line')[0]
    ok = _idx(L, lambda l: l.startswith('echo BUILDCHECK_OK'), 'BUILDCHECK_OK')[0]
    exit0 = next(i for i in range(ok, len(L)) if L[i] == 'exit /b 0')
    step7 = _idx(L, lambda l: l.startswith('echo [7/8]'), 'step 7')[0]
    assert own_stage < first_log_write and own_log < first_log_write
    assert selftest < newhash < ok < exit0 < step7, 'buildcheck must exit after the self-test and hash, before step 7'
    f = _idx(L, lambda l: l == ':fail', ':fail')[0]
    pause = next(i for i in range(f, len(L)) if L[i] == 'pause')
    assert any(L[i] == 'if "%BUILDCHECK%"=="1" exit /b 1' for i in range(f, pause)), 'buildcheck failures must not pause'


@needs_installer
def test_every_pause_honours_zb_nopause():
    """verify.py runs the installer unattended: no pause may block when ZB_NOPAUSE is set."""
    L = _lines('build_app.bat')
    f = _idx(L, lambda l: l == ':fail', ':fail')[0]
    for i, l in enumerate(L):
        if l.rstrip().endswith('pause') and not l.startswith('rem'):
            guarded = 'if not defined ZB_NOPAUSE' in l or (i > f and any(L[k] == 'if defined ZB_NOPAUSE exit /b 1' for k in range(f, i)))
            assert guarded, f'line {i + 1} can block an unattended run: {l}'
