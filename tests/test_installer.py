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
    assert any('fail_drill_noold' in l for l in L[:40]), 'drill must refuse early when nothing is installed'


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
