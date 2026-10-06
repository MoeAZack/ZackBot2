"""T03/T03b: the installer (build_app.bat launcher + installer.ps1) and the app's --simulate-failed-launch switch.

T03b moved the installer logic from CMD into installer.ps1. Its whole flow (install, rollback, drill, preflight,
buildcheck) runs here under PowerShell with every outside effect replaced by a recording fake, so the fail-closed order
is tested by executing it, not by pinning lines. The real installer and the drill run only on the owner's Windows PC.
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


# ---------------------------------------------------------------- T03b: build_app.bat is a launcher, installer.ps1 the logic
INSTALLER = os.path.join(ROOT, 'installer.ps1')


@needs_installer
def test_launcher_is_tiny_and_isolates_powershell():
    L = _lines('build_app.bat')
    code = [l for l in L if l.strip() and not l.lower().startswith(('rem', '@echo', 'title'))]
    assert len(code) <= 10, f'build_app.bat must stay a launcher, found {len(code)} code lines: {code}'
    clear = _idx(L, lambda l: l == 'set "PSModulePath="', 'PSModulePath reset')[0]
    ps = _idx(L, lambda l: l.lower().startswith('powershell '), 'installer.ps1 call')
    assert len(ps) == 1 and clear < ps[0], 'clear PSModulePath before the one PowerShell call'
    assert L[ps[0]] == 'powershell -NoProfile -ExecutionPolicy Bypass -File "!ZBPS1!" -FromLauncher'
    for l in L:                                            # the only pause: when installer.ps1 refused / could not run
        if l.rstrip().endswith('pause') and not l.lower().startswith('rem'):
            assert 'if not defined ZB_NOPAUSE' in l, l


@needs_installer
def test_launchers_never_expand_their_arguments():
    """T03b review round 2: CMD interprets quotes, & and | inside an expanded argument (x"&echo MARK escaped the
    quoting). build_app.bat therefore never expands percent-1 / percent-star / percent-~1: it passes its own command
    line through DELAYED expansion (not re-parsed) and installer.ps1 picks the mode; rollback_drill.bat has a fixed mode."""
    for bat in ('build_app.bat', 'rollback_drill.bat'):
        text = '\n'.join(_lines(bat))
        assert not re.search(r'%~?[1-9*]', text), f'{bat} expands an argument'
    L = _lines('build_app.bat')
    dx = _idx(L, lambda l: l == 'setlocal EnableDelayedExpansion', 'delayed expansion')[0]
    cl = _idx(L, lambda l: l == 'set "ZB_CMDLINE=!CMDCMDLINE!"', 'command line hand-over')[0]
    path = _idx(L, lambda l: l == 'set "ZBPS1=%~dp0installer.ps1"', 'script path captured before delayed expansion')[0]
    assert path < dx < cl
    D = _lines('rollback_drill.bat')
    k = D.index('powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer.ps1" drill')
    assert D[k - 1] == 'set "PSModulePath="' and sum(l.lower().startswith('powershell') for l in D) == 1, D


@needs_installer
def test_installer_uses_no_cmdlets():
    """Same rule as installer_check.ps1: only .NET and the language, so a module problem cannot break the installer."""
    src = '\n'.join(l for l in open(INSTALLER, encoding='utf-8').read().split('\n') if not l.lstrip().startswith('#'))
    own = set(re.findall(r'^function\s+([A-Za-z]+-[A-Za-z0-9]+)', src, re.M))
    used = set(re.findall(r'(?<![\w.$:\[-])([A-Z][a-z]+-[A-Z][A-Za-z0-9]+)\b', src)) - own - {'Set-StrictMode'}
    assert not used, f'installer.ps1 calls cmdlets: {sorted(used)}'
    open(INSTALLER, encoding='ascii').read()


@needs_installer
def test_staging_copy_excludes_the_installer_and_private_files():
    src = open(INSTALLER, encoding='utf-8').read()
    xf = re.search(r"'/XF',(.*?)'/NFL'", src, re.S).group(1)
    for f in ('build_app.bat', 'rollback_drill.bat', 'installer.ps1', 'setup_git.bat', 'config.env', 'session.json', '*.log'):
        assert f"'{f}'" in xf, f'{f} must not be copied into the build'


@needs_installer
def test_staging_keeps_manifest_data_for_tests_then_removes_it_before_packaging():
    """T03c runtime gate: CI tests validate DATA_MANIFEST, so their files must exist while staged pytest runs. The
    research datasets are test inputs only and must be deleted before PyInstaller and the installed source mirror."""
    src = open(INSTALLER, encoding='utf-8').read()
    xd = re.search(r"'/XD',(.*?)'/XF'", src, re.S).group(1)
    assert all(f"'{d}'" not in xd for d in ('data', 'data1h', 'data_long'))
    pytest_at = src.index("@('-m', 'pytest'")
    cleanup_at = src.index("foreach ($name in @('data', 'data1h', 'data_long'))")
    build_at = src.index("@('-m', 'PyInstaller'")
    assert pytest_at < cleanup_at < build_at
    cleanup = src[cleanup_at:build_at]
    assert '[IO.Directory]::Delete($path, $true)' in cleanup
    assert 'Stop-Install' in cleanup


# The fake world: every outside effect of installer.ps1 is replaced; files live in a temp LOCALAPPDATA.
_FAKES = r'''
param([string]$TInstaller, [string]$TCfg, [string]$TMode, [string]$TSrc, [string]$TLad)
$ErrorActionPreference = 'Stop'
. $TInstaller -NoRun        # (its param block would reset a caller variable named $Mode: hence the T prefix)
$global:Cfg = Get-Content -Raw $TCfg | ConvertFrom-Json
$global:Calls = [Collections.Generic.List[string]]::new()
function C([string]$x) { $global:Calls.Add($x) }
function Opt($name, $default) { $p = $global:Cfg.PSObject.Properties[$name]; if ($null -ne $p) { return $p.Value } return $default }
function New-BuildId { return 'NEW1' }
function Find-Python { return 'python' }
function Wait-Seconds([int]$n) { }
function Invoke-Pause { C 'pause' }
function Remove-FileSafe([string]$path) {
    $bad = Opt 'deleteFail' ''
    if ($bad -and [IO.Path]::GetFileName($path) -eq $bad -and $path -eq $script:S.Exe) { C 'delete-failed'; return }
    if ([IO.File]::Exists($path)) { [IO.File]::Delete($path) }
}
$global:Running = $(if ([bool](Opt 'oldRunning' $true)) { 'OLDEXE' } else { '' })   # what answers pings right now
function Stop-Bot {
    C 'stop'
    if ([bool](Opt 'stopOk' $true)) { $global:Running = ''; return $true }
    if ([bool](Opt 'stopKills' $false)) { $global:Running = '' }            # killed it, but the wait timed out
    return $false
}
function Update-Shortcuts { C 'shortcuts'; return $true }
function Remove-Stage([string]$dir) { C 'rmdir'; if ([IO.Directory]::Exists($dir)) { [IO.Directory]::Delete($dir, $true) } }
function Start-App([string]$exe, [string[]]$argv) {
    $kind = [IO.File]::ReadAllText($exe)
    C ("start:$kind" + $(if ($argv) { ' ' + ($argv -join ' ') } else { '' }))
    if ((Opt 'throwOnStart' $false) -and $kind -eq 'NEWEXE') { throw 'simulated crash' }
    $global:Running = $kind
}
function Wait-Ping([string]$buildId, [int]$seconds) {
    C "ping:${buildId}:$seconds"
    if ($buildId -eq 'NEW1') { return ($global:Running -eq 'NEWEXE' -and [bool](Opt 'newAnswers' $true)) }
    return ($global:Running -eq 'OLDEXE' -and [bool](Opt 'oldAnswers' $true))
}
function Copy-FileSafe([string]$from, [string]$to) {
    C ('copy:' + [IO.Path]::GetFileName($from) + '>' + [IO.Path]::GetFileName($to))
    $bad = Opt 'copyFail' ''                     # 'from>to' file names, e.g. 'ZackBot.prev.exe>ZackBot.exe'
    if ($bad -and ([IO.Path]::GetFileName($from) + '>' + [IO.Path]::GetFileName($to)) -eq $bad) { return $false }
    [IO.File]::Copy($from, $to, $true); return $true
}
function Get-FileSha([string]$path) {
    $bad = Opt 'hashFail' ''
    if ($bad -and $path.EndsWith($bad)) { return '' }
    if ((Opt 'exeHashChangesAfterStop' $false) -and $global:Calls.Contains('stop') -and $path -eq $script:S.Exe) { return 'CHANGED' }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($path))) -replace '-', '') } finally { $sha.Dispose() }
}
function Invoke-ExeWait([string]$exe, [string[]]$argv) {
    $kind = [IO.File]::ReadAllText($exe); C "selftest:$kind"
    if ($kind -eq 'NEWEXE' -and -not (Opt 'selftestFail' $false)) {
        [IO.File]::WriteAllText($argv[1], '{"ok": true, "build": "NEW1", "version": "3.2"}')
    }
    if ($kind -eq 'OLDEXE' -and -not (Opt 'noOldBuild' $false)) { [IO.File]::WriteAllText($argv[1], '{"ok": true, "build": "OLD1"}') }
    return 0
}
function Invoke-Logged([string]$file, [string[]]$argv, [string]$cwd = '', [switch]$Quiet) {
    $s = $script:S; $code = 0; $name = [IO.Path]::GetFileName($file)
    if ($name -eq 'robocopy' -and $argv[1] -eq $s.StageSrc) {
        C 'stage'; [void][IO.Directory]::CreateDirectory($s.StageSrc)
        [IO.File]::WriteAllText([IO.Path]::Combine($s.StageSrc, 'app.py'), 'print(1)')
        $code = [int](Opt 'stageCode' 1)
    } elseif ($name -eq 'robocopy') { C 'mirror'; $code = [int](Opt 'mirrorCode' 1) }
    elseif ($name -eq 'python' -and $argv[1] -eq 'venv') {
        C 'venv'; $d = [IO.Path]::Combine($argv[2], 'Scripts'); [void][IO.Directory]::CreateDirectory($d)
        [IO.File]::WriteAllText([IO.Path]::Combine($d, 'python.exe'), 'py')
    } elseif ($name -eq 'python.exe') {
        $step = @{ pip = 'pip'; '-c' = 'libs'; pytest = 'pytest'; PyInstaller = 'pyinstaller' }
        $k = if ($argv[0] -eq '-c') { 'libs' } else { $step[$argv[1]] }
        C $k; $code = [int](Opt ($k + 'Code') 0)
        if ($k -eq 'pyinstaller' -and $code -eq 0) {
            [void][IO.Directory]::CreateDirectory([IO.Path]::Combine($s.Stage, 'dist'))
            [IO.File]::WriteAllText($s.NewExe, 'NEWEXE')
        }
    } else { C "other:$name" }
    return @{ Code = $code; Out = ''; Err = '' }
}
$rc = Invoke-Main $TMode $TSrc $TLad
[Console]::Out.WriteLine('RC=' + $rc)
[Console]::Out.WriteLine('CALLS=' + ($global:Calls -join '|'))
'''

# On Windows the installer really runs under Windows PowerShell 5.1, so the flow tests use it too; elsewhere PowerShell 7.
PWSH = (shutil.which('powershell') if os.name == 'nt' else None) or shutil.which('pwsh') or \
    ('/opt/pwsh/pwsh' if os.path.exists('/opt/pwsh/pwsh') else None)
needs_flow = pytest.mark.skipif(PWSH is None or not os.path.exists(INSTALLER), reason='no PowerShell / no installer.ps1')


def _flow(mode, have_old=True, nopause=True, **cfg):
    """Runs installer.ps1's real Invoke-Main in the fake world. Returns (rc, calls, log text, app dir)."""
    with tempfile.TemporaryDirectory() as tmp:
        lad, app = os.path.join(tmp, 'lad'), os.path.join(tmp, 'lad', 'ZackBot', 'app')
        os.makedirs(app)
        if have_old:
            open(os.path.join(app, 'ZackBot.exe'), 'w').write('OLDEXE')
        cfgp, fk = os.path.join(tmp, 'cfg.json'), os.path.join(tmp, 'fakes.ps1')
        json.dump(cfg, open(cfgp, 'w')); open(fk, 'w').write(_FAKES)
        env = dict(os.environ); env.pop('ZB_NOPAUSE', None)
        if nopause: env['ZB_NOPAUSE'] = '1'
        r = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', fk, INSTALLER, cfgp, mode, ROOT, lad],
                           capture_output=True, text=True, env=env, timeout=120)
        assert 'RC=' in r.stdout, (r.stdout[-1500:], r.stderr[-1500:])
        m = re.search(r'^RC=(\d+)$', r.stdout, re.M)
        assert m, f'Invoke-Main must return exactly one exit code (stray pipeline output?): {r.stdout[-400:]}'
        rc = int(m.group(1))
        calls = re.search(r'CALLS=(.*)', r.stdout).group(1).split('|')
        logs = [f for f in ('build.log', 'build_preflight.log', 'build_check.log') if os.path.exists(os.path.join(lad, 'ZackBot', f))]
        assert len(logs) == 1, logs
        text = open(os.path.join(lad, 'ZackBot', logs[0]), encoding='utf-8').read()
        jp = os.path.join(lad, 'ZackBot', logs[0][:-4] + '.json')
        record = json.load(open(jp, encoding='utf-8')) if os.path.exists(jp) else None
        exe = os.path.join(app, 'ZackBot.exe')
        installed = open(exe).read() if os.path.exists(exe) else None
        stages = [d for d in os.listdir(os.path.join(lad, 'ZackBot')) if d.startswith('staging')]
        return dict(rc=rc, calls=calls, log=text, logname=logs[0], installed=installed, stages=stages, out=r.stdout, rec=record)


def _order(calls, *names):
    idx = [calls.index(n) for n in names]
    assert idx == sorted(idx), (names, calls)


@needs_flow
def test_flow_install_succeeds_and_mirror_follows_the_confirmed_launch():
    f = _flow('install')
    assert f['rc'] == 0 and f['installed'] == 'NEWEXE', f
    _order(f['calls'], 'stage', 'pytest', 'pyinstaller', 'selftest:NEWEXE', 'copy:ZackBot.exe>ZackBot.prev.exe',
           'selftest:OLDEXE', 'stop', 'copy:ZackBot.exe>ZackBot.exe', 'start:NEWEXE', 'ping:NEW1:60', 'mirror', 'shortcuts')
    assert 'start:NEWEXE --simulate-failed-launch' not in f['calls']
    for m in ('build id NEW1', 'checksum helper ok', 'new exe sha256 ', 'backup ok sha256', 'previous build OLD1', 'BUILD_DONE build=NEW1'):
        assert m in f['log'], m
    assert 'ROLLBACK' not in f['log'] and 'pause' not in f['calls']


@needs_flow
def test_flow_first_install_without_previous_version():
    f = _flow('install', have_old=False)
    assert f['rc'] == 0 and f['installed'] == 'NEWEXE' and 'selftest:OLDEXE' not in f['calls'], f


@needs_flow
def test_flow_new_build_silent_rolls_back_to_verified_previous_version():
    f = _flow('install', newAnswers=False)
    assert f['rc'] == 1 and f['installed'] == 'OLDEXE', f
    c = f['calls']
    _order(c, 'start:NEWEXE', 'ping:NEW1:60', 'copy:ZackBot.prev.exe>ZackBot.exe', 'start:OLDEXE', 'ping:OLD1:60')
    assert 'mirror' not in c and 'shortcuts' not in c, 'no mirror or shortcut update on the rollback path'
    assert 'restored exe sha256' in f['log'] and 'ROLLBACK_RESULT verified=1' in f['log']
    assert 'BUILD_FAILED: the new version did not start correctly - the previous version OLD1 was restored and confirmed running' in f['log']


@needs_flow
def test_flow_rollback_refuses_a_restored_exe_with_the_wrong_hash():
    f = _flow('install', newAnswers=False, copyFail='ZackBot.prev.exe>ZackBot.exe')   # the restore copy fails
    assert f['rc'] == 1 and f['installed'] == 'NEWEXE' and 'start:OLDEXE' not in f['calls'], f
    assert 'restoring the previous exe failed - ZackBot.prev.exe is still in' in f['log'], f['log']


@needs_flow
def test_flow_swap_that_keeps_failing_rolls_back_after_five_tries():
    f = _flow('install', copyFail='ZackBot.exe>ZackBot.exe')
    assert f['calls'].count('copy:ZackBot.exe>ZackBot.exe') == 5 and 'start:NEWEXE' not in f['calls']
    assert f['rc'] == 1 and f['installed'] == 'OLDEXE' and 'ROLLBACK_RESULT verified=1' in f['log'], f


@needs_flow
def test_flow_no_previous_version_removes_the_failed_exe():
    f = _flow('install', have_old=False, newAnswers=False)
    assert f['rc'] == 1 and f['installed'] is None
    assert 'there was no previous version to restore' in f['log']


@needs_flow
def test_flow_drill_passes_only_when_the_old_build_is_proven_running():
    f = _flow('drill', newAnswers=True)              # the fake new exe would answer, but the drill launch must fail it
    c = f['calls']
    assert 'start:NEWEXE --simulate-failed-launch' in c and 'ping:NEW1:20' in c
    # fake: the new exe 'answers' -> the drill is INVALID (it was told to fail) and the untrusted build must NOT stay:
    # the previous verified build is restored and proven running, and the run still fails.
    assert f['rc'] == 1 and f['installed'] == 'OLDEXE', f
    assert 'ROLLBACK DRILL INVALID' in f['log'] and 'DRILL_PASSED' not in f['log'] and 'ROLLBACK_RESULT verified=1' in f['log']
    assert c.count('stop') == 2, 'the answering new exe is stopped before the restore'
    tail = c[c.index('ping:NEW1:20'):]
    assert tail[:5] == ['ping:NEW1:20', 'stop', 'copy:ZackBot.prev.exe>ZackBot.exe', 'start:OLDEXE', 'ping:OLD1:60'], tail
    rec = f['rec']
    assert rec['verdict'] == 'BUILD_FAILED' and rec['rollback']['verified'] is True and ('launch', False) in _steps(rec)
    assert rec['installed_after']['build'] == 'OLD1'
    f = _flow('drill', newAnswers=False)
    assert f['rc'] == 0 and f['installed'] == 'OLDEXE', f
    _order(f['calls'], 'start:NEWEXE --simulate-failed-launch', 'ping:NEW1:20', 'copy:ZackBot.prev.exe>ZackBot.exe',
           'start:OLDEXE', 'ping:OLD1:60')
    assert 'DRILL_PASSED new=NEW1' in f['log'] and 'restored=OLD1' in f['log'] and 'mirror' not in f['calls']
    f = _flow('drill', newAnswers=False, oldAnswers=False)
    assert f['rc'] == 1 and 'ROLLBACK DRILL FAILED' in f['log'] and 'DRILL_PASSED' not in f['log']


@needs_flow
def test_flow_drill_refuses_without_an_installed_version_or_old_build_id():
    f = _flow('drill', have_old=False)
    assert f['rc'] == 1 and 'stage' not in f['calls'] and 'needs an installed ZackBot' in f['log'], 'refuse before step 1'
    f = _flow('drill', noOldBuild=True)
    assert f['rc'] == 1 and 'stop' not in f['calls'] and 'rollback could not be proven' in f['log']


@needs_flow
def test_flow_preflight_is_non_destructive():
    f = _flow('preflight')
    assert f['rc'] == 0 and f['logname'] == 'build_preflight.log' and 'PREFLIGHT_OK build=NEW1 app.py sha256=' in f['log']
    assert f['calls'] == ['rmdir', 'stage', 'rmdir'] and f['installed'] == 'OLDEXE' and f['stages'] == [], f


@needs_flow
def test_flow_buildcheck_builds_but_never_installs():
    f = _flow('buildcheck')
    assert f['rc'] == 0 and f['logname'] == 'build_check.log' and 'BUILDCHECK_OK build=NEW1 sha256=' in f['log']
    for x in ('stop', 'start:NEWEXE', 'copy:ZackBot.exe>ZackBot.prev.exe', 'mirror'):
        assert x not in f['calls'], x
    assert f['stages'] == ['staging_buildcheck'] and f['installed'] == 'OLDEXE'


@pytest.mark.parametrize('cfg, why', [
    (dict(stageCode=8), 'copying the source failed'),
    (dict(hashFail='app.py'), 'the checksum helper does not work'),
    (dict(pipCode=1), 'installing the pinned libraries failed'),
    (dict(libsCode=1), 'the build libraries do not load'),
    (dict(pytestCode=1), 'the safety tests FAILED'),
    (dict(pyinstallerCode=1), 'PyInstaller failed'),
    (dict(selftestFail=True), 'the new exe failed its self-test'),
    (dict(copyFail='ZackBot.exe>ZackBot.prev.exe'), 'backing up the current ZackBot.exe failed'),
    (dict(stopOk=False), 'the running ZackBot did not stop'),
])
@needs_flow
def test_flow_every_failure_before_the_swap_leaves_the_old_version_untouched(cfg, why):
    f = _flow('install', **cfg)
    assert f['rc'] == 1 and f['installed'] == 'OLDEXE' and f'BUILD_FAILED: {why}' in f['log'], (cfg, f['log'][-600:])
    assert not any(c.startswith('start:') for c in f['calls']) and 'mirror' not in f['calls']


@needs_flow
def test_flow_unexpected_error_after_the_stop_restores_the_previous_version():
    """T03b real error handling: an exception after the old bot was stopped must not leave it down."""
    f = _flow('install', throwOnStart=True)
    assert f['rc'] == 1 and f['installed'] == 'OLDEXE', f
    assert 'UNEXPECTED: simulated crash' in f['log'] and 'ROLLBACK_RESULT verified=1' in f['log']
    _order(f['calls'], 'stop', 'start:NEWEXE', 'copy:ZackBot.prev.exe>ZackBot.exe', 'start:OLDEXE', 'ping:OLD1:60')


@needs_flow
def test_flow_pauses_only_for_a_person_and_never_with_zb_nopause():
    for mode, cfg in (('install', dict(pytestCode=1)), ('install', dict(newAnswers=False)), ('drill', dict(newAnswers=False)),
                      ('install', dict(mirrorCode=8))):
        assert 'pause' not in _flow(mode, nopause=True, **cfg)['calls'], (mode, cfg)
        assert 'pause' in _flow(mode, nopause=False, **cfg)['calls'], (mode, cfg)
    for mode in ('preflight', 'buildcheck'):                   # used by tests and verify.py: never pause, even on failure
        assert 'pause' not in _flow(mode, nopause=False, stageCode=8)['calls'], mode


@needs_flow
def test_flow_unknown_mode_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        fk = os.path.join(tmp, 'f.ps1'); cfgp = os.path.join(tmp, 'c.json')
        open(fk, 'w').write(_FAKES); open(cfgp, 'w').write('{}')
        r = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', fk, INSTALLER, cfgp, 'instal', ROOT, tmp],
                           capture_output=True, text=True, timeout=120)
        assert 'RC=2' in r.stdout and 'usage' in r.stdout and 'CALLS=' in r.stdout and not os.path.exists(os.path.join(tmp, 'ZackBot'))


@needs_flow
def test_real_process_runner_and_hash_helper_path():
    """No fakes: the real Invoke-Logged starts the real installer_check.ps1 (quoted path with a space) and Get-FileSha
    returns its SHA-256; a missing file returns '' and the helper's reason lands in the log."""
    with tempfile.TemporaryDirectory() as tmp:
        stage = os.path.join(tmp, 'stage dir', 'src'); os.makedirs(stage)
        shutil.copy(PS1, stage)
        target = os.path.join(tmp, 'a file.txt'); open(target, 'wb').write(b'zackbot\r\n')
        log = os.path.join(tmp, 'build.log')
        script = os.path.join(tmp, 'real.ps1')
        open(script, 'w').write(
            "param($I, $L, $St, $F)\n. $I -NoRun\n"
            "$script:S = @{ Log = $L; StageSrc = $St }\n"
            "[Console]::Out.WriteLine('SHA=' + (Get-FileSha $F))\n"
            "[Console]::Out.WriteLine('MISSING=[' + (Get-FileSha ($F + '.nope')) + ']')\n")
        r = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', script, INSTALLER, log, stage, target],
                           capture_output=True, text=True, timeout=120)
        assert ('SHA=' + hashlib.sha256(b'zackbot\r\n').hexdigest().upper()) in r.stdout, (r.stdout, r.stderr[-500:])
        assert 'MISSING=[]' in r.stdout
        assert 'installer_check hash' in open(log, encoding='utf-8').read() and 'not found' in open(log, encoding='utf-8').read()


CAIRO_TS = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+0[23]:00$')


def _steps(rec):
    return [(x['step'], x['ok']) for x in rec['steps']]


@needs_flow
def test_json_record_of_a_successful_install():
    f = _flow('install'); rec = f['rec']
    assert rec and rec['schema'] == 1 and rec['mode'] == 'install' and rec['verdict'] == 'BUILD_DONE' and rec['exit_code'] == 0
    assert CAIRO_TS.match(rec['started']) and CAIRO_TS.match(rec['finished']), (rec['started'], rec['finished'])
    assert all(CAIRO_TS.match(x['started']) and CAIRO_TS.match(x['finished']) for x in rec['steps'])
    assert _steps(rec) == [(n, True) for n in ('stage', 'checksum_helper', 'build_env', 'libs', 'safety_tests', 'pyinstaller',
                                               'selftest_new', 'hash_new', 'backup', 'stop', 'swap', 'launch', 'mirror', 'shortcuts')]
    assert rec['build_id'] == 'NEW1' and re.fullmatch(r'[0-9A-F]{64}', rec['new_exe_sha256'])
    assert rec['previous']['build'] == 'OLD1' and rec['installed_after'] == dict(build='NEW1', sha256=rec['new_exe_sha256'])
    assert rec['bot_stop_attempted'] is True and rec['bot_stop_confirmed'] is True and rec['rollback']['attempted'] is False and rec['warnings'] == []


@needs_flow
def test_json_record_of_a_rollback_and_of_warnings():
    f = _flow('install', newAnswers=False); rec = f['rec']
    assert rec['verdict'] == 'BUILD_FAILED' and rec['exit_code'] == 1 and 'did not start correctly' in rec['reason']
    assert _steps(rec)[-2:] == [('launch', False), ('rollback', True)]
    assert rec['rollback'] == dict(attempted=True, verified=True, restored_sha256=rec['previous']['sha256'],
                                   note='the previous version OLD1 was restored and confirmed running')
    assert rec['installed_after'] == dict(build='OLD1', sha256=rec['previous']['sha256'])
    f = _flow('install', mirrorCode=8); rec = f['rec']
    assert rec['verdict'] == 'BUILD_DONE' and ('mirror', False) in _steps(rec) and len(rec['warnings']) == 1


@needs_flow
def test_json_record_of_the_drill_preflight_buildcheck_and_a_crash():
    rec = _flow('drill', newAnswers=False)['rec']
    assert rec['verdict'] == 'DRILL_PASSED' and _steps(rec)[0] == ('drill_precheck', True)
    assert ('launch', True) in _steps(rec) and _steps(rec)[-1] == ('rollback', True)       # failing launch = expected
    rec = _flow('preflight')['rec']
    assert rec['verdict'] == 'PREFLIGHT_OK' and _steps(rec) == [('stage', True), ('checksum_helper', True)]
    assert rec['bot_stop_attempted'] is False and rec['installed_after'] is None and rec['installed_file_sha256'] is None
    rec = _flow('buildcheck')['rec']
    assert rec['verdict'] == 'BUILDCHECK_OK' and _steps(rec)[-1] == ('hash_new', True) and rec['bot_stop_attempted'] is False
    rec = _flow('install', throwOnStart=True)['rec']
    assert ('launch', False) in _steps(rec) and 'unexpected: simulated crash' in str(rec['steps']) and rec['rollback']['verified'] is True


@pytest.mark.parametrize('cfg, step', [
    (dict(stageCode=8), 'stage'), (dict(hashFail='app.py'), 'checksum_helper'), (dict(pipCode=1), 'build_env'),
    (dict(libsCode=1), 'libs'), (dict(pytestCode=1), 'safety_tests'), (dict(pyinstallerCode=1), 'pyinstaller'),
    (dict(selftestFail=True), 'selftest_new'), (dict(copyFail='ZackBot.exe>ZackBot.prev.exe'), 'backup'),
])
@needs_flow
def test_json_record_names_the_failed_step(cfg, step):
    rec = _flow('install', **cfg)['rec']
    assert rec['verdict'] == 'BUILD_FAILED' and _steps(rec)[-1] == (step, False) and rec['steps'][-1]['detail'] == rec['reason']
    assert all(ok for _, ok in _steps(rec)[:-1]) and rec['installed_after'] is None


@needs_flow
def test_json_record_never_contains_secrets():
    """The record holds paths, build ids, hashes and step results - never the session token or settings."""
    rec = _flow('install')['rec']
    text = json.dumps(rec).lower()
    for bad in ('token', 'secret', 'api_key', 'apikey', 'password', 'config.env', 'session.json'):
        assert bad not in text, bad
    src = open(INSTALLER, encoding='utf-8').read()
    body = src[src.index('function Get-Record'):src.index('function Save-Record')]
    assert 'session' not in body.lower() and 'token' not in body.lower()


@needs_flow
def test_one_install_attempt_and_one_launch_per_run():
    for mode, cfg in (('install', dict(newAnswers=False)), ('drill', dict(newAnswers=False)), ('install', {})):
        c = _flow(mode, **cfg)['calls']
        assert sum(x.startswith('start:NEWEXE') for x in c) == 1 and sum(x.startswith('ping:NEW1') for x in c) == 1, (mode, c)
        assert c.count('pyinstaller') == 1 and c.count('pytest') == 1 and c.count('start:OLDEXE') <= 1, (mode, c)


@needs_flow
def test_argument_quoting_matches_windows_rules():
    """ConvertTo-ArgString must produce what CommandLineToArgvW splits back into the same list (paths with spaces,
    trailing backslashes, quotes, empty arguments)."""
    cases = [['a', 'b c', ''], ['C:\\Program Files\\x\\', '-m', 'not slow'], ['x"y', 'p q\\"r', 'end\\\\'],
             ['C:\\Users\\A B\\AppData\\Local\\ZackBot\\staging\\src\\panel.html;.']]
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, 'q.ps1')
        open(script, 'w').write("param($I, $J)\n. $I -NoRun\n$cases = Get-Content -Raw $J | ConvertFrom-Json\n"
                                "foreach ($c in $cases) { [Console]::Out.WriteLine('ARGS=' + (ConvertTo-ArgString ([string[]]@($c)))) }\n")
        jp = os.path.join(tmp, 'c.json'); json.dump(cases, open(jp, 'w'))
        r = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', script, INSTALLER, jp], capture_output=True, text=True, timeout=120)
        got = [l[5:] for l in r.stdout.splitlines() if l.startswith('ARGS=')]
        assert len(got) == len(cases), (r.stdout, r.stderr[-400:])
        assert [_split_windows(g) for g in got] == cases, got


def _split_windows(cmd):
    """CommandLineToArgvW / MS C runtime splitting: 2n backslashes + quote -> n backslashes and a quote toggle,
    2n+1 backslashes + quote -> n backslashes and a literal quote, other backslashes are literal."""
    args, cur, quoted, have, i = [], [], False, False, 0
    while i < len(cmd):
        ch = cmd[i]
        if ch == '\\':
            n = 0
            while i < len(cmd) and cmd[i] == '\\': n += 1; i += 1
            if i < len(cmd) and cmd[i] == '"':
                cur.append('\\' * (n // 2)); have = True
                if n % 2: cur.append('"'); i += 1
            else:
                cur.append('\\' * n); have = True
            continue
        if ch == '"': quoted = not quoted; have = True
        elif ch in ' \t' and not quoted:
            if have: args.append(''.join(cur)); cur, have = [], False
        else: cur.append(ch); have = True
        i += 1
    if have: args.append(''.join(cur))
    return args


@needs_installer
def test_installer_is_windows_powershell_5_1_syntax():
    """installer.ps1 runs under Windows PowerShell 5.1: no PowerShell-7-only operators or features."""
    src = '\n'.join(l.split(' #')[0] for l in open(INSTALLER, encoding='utf-8').read().split('\n') if not l.lstrip().startswith('#'))
    code = re.sub(r"'[^']*'|\"[^\"]*\"", "''", src)                      # ignore string contents
    for pat, what in ((r'\?\?', 'null-coalescing ??'), (r'\?\.', 'null-conditional ?.'), (r'&&|\|\|', 'pipeline chain && / ||'),
                      (r'\s\?\s[^:]*\s:\s', 'ternary ? :'), (r'-Parallel\b', 'ForEach -Parallel'), (r'\bclean\s*\{', 'clean block')):
        assert not re.search(pat, code), f'installer.ps1 uses {what} (PowerShell 7 only)'
    assert 'Set-StrictMode -Version 2.0' in src and "$ErrorActionPreference = 'Stop'" in src


@needs_installer
def test_bare_pytest_cannot_reach_a_real_side_effect():
    """Every installer function that starts/stops processes, touches shortcuts or pauses is replaced in the fake world,
    so a bare pytest run on the owner's PC can never stop the bot, start an exe or install anything."""
    src = open(INSTALLER, encoding='utf-8').read()
    funcs = re.findall(r'^function\s+([A-Za-z]+-[A-Za-z0-9]+)[^\n]*\n(.*?)(?=^function\s|\Z)', src, re.S | re.M)
    risky = {n for n, body in funcs if re.search(r'Process\]::Start|\.Kill\(\)|CreateShortcut|cmd\.exe|robocopy', body)}
    faked = set(re.findall(r'^function\s+([A-Za-z]+-[A-Za-z0-9]+)', _FAKES, re.M))
    # Invoke-Logged / Invoke-ExeWait / Start-App etc. are the only process starters; callers go through them
    starters = {'Invoke-Logged', 'Invoke-ExeWait', 'Start-App', 'Stop-Bot', 'Update-Shortcuts', 'Remove-Stage', 'Invoke-Pause'}
    assert risky <= starters | {'Invoke-Step1Stage', 'Invoke-Finish', 'Invoke-Helper'}, risky
    assert starters <= faked, starters - faked


# ---------------------------------------------------------------- T03b review round 1 (Codex): post-stop recovery
@needs_flow
def test_failed_stop_never_swaps_and_proves_the_old_build_still_answers():
    f = _flow('install', stopOk=False)                    # old process keeps running and answering
    c = f['calls']
    assert f['rc'] == 1 and f['installed'] == 'OLDEXE' and 'copy:ZackBot.exe>ZackBot.exe' not in c and not any(x.startswith('start:') for x in c)
    _order(c, 'stop', 'ping:OLD1:20')
    assert 'STOP_RECOVERY verified=1' in f['log'] and 'still running and answered' in f['log']
    rec = f['rec']
    assert rec['bot_stop_attempted'] is True and rec['bot_stop_confirmed'] is False and rec['rollback']['attempted'] is False
    assert rec['stop_recovery']['attempted'] is True and rec['stop_recovery']['verified'] is True
    assert _steps(rec)[-2:] == [('stop', False), ('stop_recovery', True)] and rec['installed_after']['build'] == 'OLD1'
    assert 'the exe was NOT replaced' in rec['reason']


@needs_flow
def test_failed_stop_that_killed_the_bot_restarts_the_verified_old_exe_once():
    f = _flow('install', stopOk=False, stopKills=True)
    c = f['calls']
    assert f['rc'] == 1 and f['installed'] == 'OLDEXE' and 'copy:ZackBot.exe>ZackBot.exe' not in c
    _order(c, 'stop', 'ping:OLD1:20', 'start:OLDEXE', 'ping:OLD1:60')
    assert c.count('start:OLDEXE') == 1 and 'restarted once and confirmed running' in f['log']
    assert f['rec']['stop_recovery']['verified'] is True


@needs_flow
def test_failed_stop_with_an_unrecoverable_bot_is_reported_unverified():
    f = _flow('install', stopOk=False, stopKills=True, oldAnswers=False)
    c = f['calls']
    assert f['rc'] == 1 and c.count('start:OLDEXE') == 1 and 'STOP_RECOVERY verified=0' in f['log']
    rec = f['rec']
    assert rec['stop_recovery']['verified'] is False and rec['installed_after'] is None
    assert rec['installed_file_sha256'] == rec['previous']['sha256'] and 'open ZackBot and check' in rec['reason']


@needs_flow
def test_failed_stop_does_not_restart_an_exe_whose_hash_changed():
    """Defensive: the recovery restarts only the exe that still matches the verified old hash."""
    f = _flow('install', stopOk=False, stopKills=True, exeHashChangesAfterStop=True)
    assert f['rc'] == 1 and 'start:OLDEXE' not in f['calls'] and 'no longer matches its verified hash' in f['log']


@needs_flow
def test_record_never_claims_an_unverified_install():
    rec = _flow('install', newAnswers=False, oldAnswers=False)['rec']       # restored file, but it never answered
    assert rec['rollback'] == dict(attempted=True, verified=False, restored_sha256=rec['previous']['sha256'],
                                   note='the previous version was restored but did NOT confirm it is running - open ZackBot and check')
    assert rec['installed_after'] is None and rec['installed_file_sha256'] == rec['previous']['sha256']
    rec = _flow('install', noOldBuild=True, newAnswers=False)['rec']         # old build id unknown: restored but unverifiable
    assert rec['installed_after'] is None and rec['rollback']['verified'] is False
    rec = _flow('install')['rec']
    assert rec['bot_stop_attempted'] is True and rec['bot_stop_confirmed'] is True and rec['installed_file_sha256'] == rec['new_exe_sha256']


# ---------------------------------------------------------------- T03b review round 2 (Codex): file state + launcher
@needs_flow
def test_launcher_mode_is_picked_from_the_raw_command_line():
    cases = {
        r'C:\WINDOWS\system32\cmd.exe /c ""C:\Dev\ZackBot2\build_app.bat" "': 'install',          # double-click
        r'cmd /d /c C:\Dev\ZackBot2\build_app.bat preflight': 'preflight',                           # verify.py
        r'cmd /d /c "C:\Program Files\Zack Bot\build_app.bat" buildcheck': 'buildcheck',
        r'cmd /d /c C:\Dev\ZackBot2\build_app.bat "BUILDCHECK"': 'buildcheck',
        r'cmd /d /c C:\Dev\ZackBot2\build_app.bat drill': None,                        # drill = rollback_drill.bat only
        r'cmd /d /c C:\build_app.bat\build_app.bat preflight': 'preflight',
        r'cmd /c C:\Dev\ZackBot2\build_app.bat typo build_app.bat': None,              # Codex round 3: a later
        r'cmd /c C:\Dev\ZackBot2\build_app.bat x build_app.bat drill': None,           # build_app.bat never
        r'cmd /c C:\Dev\ZackBot2\build_app.bat typo C:\x\build_app.bat preflight': None,   # re-anchors the parse
        r'cmd /d /c C:\build_app.bat-old\build_app.bat': 'install',
        r'cmd /d /c C:\build_app.bat-old\setup_git.bat buildcheck': None,             # name only CONTAINS it
        r'cmd /d /c C:\Dev\ZackBot2\build_app.bat x"&echo MARK_B': None,                            # Codex probe
        r'cmd /d /c C:\Dev\ZackBot2\build_app.bat x&echo MARK_A': None,
        r'cmd /d /c C:\Dev\ZackBot2\build_app.bat preflight extra': None,
        r'cmd /d /c C:\Dev\ZackBot2\build_app.bat "preflight&calc"': None,
        r'cmd /d /c C:\Dev\ZackBot2\build_app.bat instal': None,
        r'C:\WINDOWS\system32\cmd.exe': None,                                                       # typed in a console
        '': None,
    }
    with tempfile.TemporaryDirectory() as tmp:
        jp, script = os.path.join(tmp, 'c.json'), os.path.join(tmp, 'q.ps1')
        json.dump(list(cases), open(jp, 'w'))
        open(script, 'w').write("param($I, $J)\n. $I -NoRun\n"
                                "foreach ($c in (Get-Content -Raw $J | ConvertFrom-Json)) { $m = Get-LauncherMode $c; "
                                "[Console]::Out.WriteLine('MODE=' + $(if ($m) { $m } else { '<none>' })) }\n")
        r = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', script, INSTALLER, jp], capture_output=True, text=True, timeout=120)
        got = [l[5:] for l in r.stdout.splitlines() if l.startswith('MODE=')]
        assert got == [v or '<none>' for v in cases.values()], (list(zip(cases, got)), r.stderr[-400:])


@needs_flow
def test_refused_launcher_arguments_change_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        env = _env(ZB_CMDLINE=r'cmd /d /c C:\x\build_app.bat x"&echo MARK_B', LOCALAPPDATA=tmp, ZB_NOPAUSE='1')
        r = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', INSTALLER, '-FromLauncher'], capture_output=True,
                           text=True, timeout=120, env=env)
        assert r.returncode == 2 and 'Nothing was changed' in r.stdout and 'cmd /c build_app.bat' in r.stdout, r.stdout
        assert not os.path.exists(os.path.join(tmp, 'ZackBot')), 'a refused launch must not even create the log folder'


@needs_flow
def test_record_reads_the_installed_file_at_the_end():
    f = _flow('install', have_old=False, newAnswers=False)              # first install fails -> the exe is removed
    rec = f['rec']
    assert f['installed'] is None and rec['installed_file_present'] is False and rec['installed_file_sha256'] is None, rec
    f = _flow('install', stopOk=False, stopKills=True, exeHashChangesAfterStop=True)   # hash change detected
    assert f['rec']['installed_file_sha256'] == 'CHANGED' and f['rec']['installed_file_present'] is True
    rec = _flow('install')['rec']
    assert rec['installed_file_present'] is True and rec['installed_file_sha256'] == rec['new_exe_sha256']
    rec = _flow('preflight')['rec']
    assert rec['installed_file_present'] is None and rec['installed_file_sha256'] is None, 'preflight never inspects the install'


@needs_flow
def test_failed_first_install_reports_an_exe_it_could_not_remove():
    f = _flow('install', have_old=False, newAnswers=False, deleteFail='ZackBot.exe')
    rec = f['rec']
    assert f['rc'] == 1 and f['installed'] == 'NEWEXE' and 'the failed exe could NOT be removed' in rec['reason'], rec['reason']
    assert rec['installed_file_present'] is True and rec['installed_file_sha256'] == rec['new_exe_sha256'] and rec['installed_after'] is None


@needs_installer
@pytest.mark.skipif(os.name != 'nt', reason='runs the real launcher through cmd.exe (Windows only)')
def test_launcher_refuses_unknown_and_extra_tokens_before_staging():
    """The launcher contract (Codex round 3): supported invocations pick exactly one fixed mode; ordinary unknown or extra
    tokens are refused (exit 2) before anything is staged. Only harmless tokens are used here - none of them can map to
    install or the drill even if the parser were broken. Hostile raw CMD syntax in the CALLER's command line (x"&...) is
    parsed by the calling shell before build_app.bat runs, so no batch file can neutralise it: out of scope."""
    root = os.path.join(os.environ['LOCALAPPDATA'], 'ZackBot')
    before = sorted(os.listdir(root)) if os.path.isdir(root) else None
    for args in (['zzz'], ['preflight', 'zzz'], ['"buildcheck"', 'zzz']):
        r = subprocess.run(['cmd', '/d', '/c', os.path.join(ROOT, 'build_app.bat')] + args, capture_output=True, text=True,
                           timeout=120, env=_env(ZB_NOPAUSE='1'), stdin=subprocess.DEVNULL)
        assert r.returncode == 2 and 'Nothing was changed' in r.stdout, (args, r.returncode, r.stdout[-500:])
    assert (sorted(os.listdir(root)) if os.path.isdir(root) else None) == before, 'a refused launch must not stage or log'
