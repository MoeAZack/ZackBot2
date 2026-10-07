"""AUD-00 (historical audit C18, confirmed on Windows 2026-10-07): one ZackBot process per data folder / account.
Before: a check-then-bind guard (port_in_use, then App()/Engine()/session.json, then a SO_REUSEADDR bind). A second process
built its engine and overwrote session.json before its bind - and on Windows that bind even SUCCEEDED (dual bind)."""
import json, os, socket, subprocess, sys, tempfile, threading, time
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import instance as INST
from http.server import BaseHTTPRequestHandler


def _free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


# ------------------------------------------------------------------ the ownership lock
def test_one_owner_and_token_checked_release(tmp_path):
    a = INST.acquire(str(tmp_path))
    assert a and INST.acquire(str(tmp_path)) is None, 'this process already owns it: a second acquire is refused'
    assert INST.release(str(tmp_path), 'not-ours') is False and INST.release(str(tmp_path), None) is False
    assert INST.release(str(tmp_path), a) is True and INST.acquire(str(tmp_path))


def _plant(tmp_path, **info):
    p = os.path.join(tmp_path, INST.LOCK_NAME)
    with open(p, 'w', encoding='utf-8') as f: json.dump(dict(dict(t=0, token='old'), **info), f)
    return p


def test_a_live_owner_is_respected_and_a_dead_one_is_reclaimed(tmp_path):
    p = _plant(tmp_path, pid=424242, start=111)
    assert INST.acquire(str(tmp_path), alive=lambda pid: True, start=lambda pid: 111) is None
    tok = INST.acquire(str(tmp_path), alive=lambda pid: False, start=lambda pid: None)
    assert tok and json.load(open(p))['pid'] == os.getpid()


def test_a_reused_pid_is_not_mistaken_for_the_owner(tmp_path):
    """After a crash Windows may give the dead owner's pid to another program: a different creation time = owner gone."""
    _plant(tmp_path, pid=424242, start=111)
    assert INST.acquire(str(tmp_path), alive=lambda pid: True, start=lambda pid: 999)


@pytest.mark.parametrize('bad', ['[1, 2]', '', 'garbage', 'null', '{"pid": -1}', '{"pid": true}'])
def test_malformed_lock_files_are_reclaimed_never_crash(tmp_path, bad):
    open(os.path.join(tmp_path, INST.LOCK_NAME), 'w').write(bad)
    assert INST.release(str(tmp_path), 'x') is False
    assert INST.acquire(str(tmp_path))


def test_simultaneous_takeover_has_exactly_one_winner_and_no_worker_crash(tmp_path):
    for rnd in range(25):
        _plant(tmp_path, pid=424242, start=1, token=f'dead{rnd}')
        start, wins, errs = threading.Barrier(8), [], []
        def go():
            try:
                start.wait()
                t = INST.acquire(str(tmp_path), alive=lambda pid: pid != 424242, start=lambda pid: 1)
                if t: wins.append(t)
            except BaseException as ex:
                errs.append(repr(ex))
        th = [threading.Thread(target=go) for _ in range(8)]
        for x in th: x.start()
        for x in th: x.join(10)
        assert not errs, (rnd, errs)
        assert len(wins) == 1, (rnd, wins)
        assert json.load(open(os.path.join(tmp_path, INST.LOCK_NAME)))['token'] == wins[0]
        assert not os.path.exists(os.path.join(tmp_path, INST.LOCK_NAME + '.takeover'))
        os.remove(os.path.join(tmp_path, INST.LOCK_NAME))


def test_windows_contention_is_taken_not_a_crash(tmp_path, monkeypatch):
    _plant(tmp_path, pid=424242, start=1)
    real = os.open
    def contended(path, *a, **k):
        if str(path).endswith('.takeover'): raise PermissionError(13, 'Access is denied', str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(INST.os, 'open', contended)
    assert INST.acquire(str(tmp_path), alive=lambda pid: False) is None
    monkeypatch.undo()
    def denied(a, b): raise PermissionError(13, 'Access is denied')
    monkeypatch.setattr(INST.os, 'link', denied)
    assert INST._create(os.path.join(tmp_path, 'x.lock'), {}) is False


def test_process_identity_helpers():
    import subprocess as sp
    assert INST.pid_alive(os.getpid()) and not INST.pid_alive(None) and not INST.pid_alive(-3)
    pr = sp.Popen([sys.executable, '-c', 'pass']); pr.wait()
    assert INST.pid_alive(pr.pid) is False
    assert INST.proc_start(os.getpid()) is not None or os.name not in ('nt', 'posix')
    src = open(os.path.join(ROOT, 'instance.py'), encoding='utf-8').read()
    body = src[src.index('def pid_alive'):src.index('def owner_gone')]
    assert body.index("os.name == 'nt'") < body.rindex('os.kill('), 'never os.kill on Windows (it terminates the process)'


# ------------------------------------------------------------------ the exclusive control-panel bind
class _H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass


def test_the_panel_port_cannot_be_bound_twice():
    port = _free_port()
    a = INST.bind_exclusive(('127.0.0.1', port), _H)
    try:
        assert a is not None and INST.bind_exclusive(('127.0.0.1', port), _H) is None
        if os.name == 'nt':                              # not even by another program that asks for SO_REUSEADDR
            b = socket.socket(); b.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError): b.bind(('127.0.0.1', port))
            b.close()
    finally:
        a.server_close()


def test_main_wins_both_gates_before_touching_shared_state():
    src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    m = src[src.index('def main():'):]
    order = [m.index(x) for x in ("INST.bind_exclusive(('127.0.0.1', PORT), H)", 'OWNER = INST.acquire(DATA)', 'save_json(SESSION_F',
                                  'threading.Thread(target=job_worker', 'APP = App()', 'srv.serve_forever')]
    assert order == sorted(order), order
    assert 'ThreadingHTTPServer((' not in m and 'port_in_use()' not in m
    assert src.count('release_owner()') - src.count('def release_owner()') == 2, 'both quit paths give the data folder back'


# ------------------------------------------------------------------ two real processes (Codex acceptance item 5)
_LAUNCH = r'''
import os, sys
sys.argv = ['app.py', '--no-window']
import app
p = int(os.environ['ZB_T_PORT'])
app.PORT = p; app.HOSTS = (f'127.0.0.1:{p}', f'localhost:{p}'); app.ORIGINS = (f'http://127.0.0.1:{p}', f'http://localhost:{p}')
app.open_window = lambda tok: print('WOULD_OPEN_EXISTING', flush=True)       # never a real browser in tests
app.message_box = lambda text: print('MESSAGE', text, flush=True)
app.main()
print('MAIN_RETURNED', flush=True)
'''


def _spawn(home, port):
    env = dict(os.environ, LOCALAPPDATA=os.path.join(home, 'local'), USERPROFILE=os.path.join(home, 'home'),
               HOME=os.path.join(home, 'home'), ZB_T_PORT=str(port), PYTHONIOENCODING='utf-8')
    env.pop('ZB_TESTNET_FAULTS', None)
    return subprocess.Popen([sys.executable, '-c', _LAUNCH], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace')


def _kill(pid):
    """Forced death of the APP process itself (TerminateProcess on Windows: no atexit, no release - like a crash)."""
    import signal
    try: os.kill(int(pid), signal.SIGTERM)
    except (OSError, ValueError): pass


def _engine_starts(data):
    try: return open(os.path.join(data, 'bot.log'), encoding='utf-8', errors='replace').read().count('| mode=PAPER')
    except OSError: return 0


def _wait(cond, timeout):
    t = time.time() + timeout
    while time.time() < t:
        if cond(): return True
        time.sleep(0.5)
    return cond()


@pytest.mark.slow
def test_two_processes_started_together_give_exactly_one_engine():
    owners = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home:
        for d in ('local', 'home'): os.makedirs(os.path.join(home, d))
        data = os.path.join(home, 'local', 'ZackBot'); port = _free_port()
        procs = [_spawn(home, port), _spawn(home, port)]
        try:
            assert _wait(lambda: sum(p.poll() is None for p in procs) == 1 and _engine_starts(data) >= 1, 90), \
                [p.poll() for p in procs]
            time.sleep(3)
            alive = [p for p in procs if p.poll() is None]; loser = [p for p in procs if p.poll() is not None]
            assert len(alive) == 1 and len(loser) == 1
            out = loser[0].stdout.read()
            assert 'MAIN_RETURNED' in out and ('WOULD_OPEN_EXISTING' in out or 'MESSAGE' in out), out[-1500:]
            assert _engine_starts(data) == 1, 'the loser never built an engine'
            # (a Windows venv python.exe is a launcher: the app runs as its child, so identity comes from the app's own records)
            sess = json.load(open(os.path.join(data, 'session.json'), encoding='utf-8'))
            lock = json.load(open(os.path.join(data, INST.LOCK_NAME), encoding='utf-8'))
            owner = sess['pid']; owners.append(owner)
            assert owner == lock['pid'] and INST.pid_alive(owner), 'one process wrote session.json AND owns the folder'
            # sequential control: a later start while the owner runs changes nothing
            late = _spawn(home, port)
            assert late.wait(90) == 0 and 'MAIN_RETURNED' in late.stdout.read()
            assert _engine_starts(data) == 1 and json.load(open(os.path.join(data, 'session.json')))['pid'] == owner
            # forced death (no release, like a crash or the installer's kill): the next start reclaims the folder
            _kill(owner); alive[0].wait(30)
            assert _wait(lambda: not INST.pid_alive(owner), 30)
            nxt = _spawn(home, port); procs.append(nxt)
            assert _wait(lambda: _engine_starts(data) == 2, 90) and nxt.poll() is None
            new = json.load(open(os.path.join(data, INST.LOCK_NAME), encoding='utf-8'))['pid']; owners.append(new)
            assert new != owner and INST.pid_alive(new)
            assert json.load(open(os.path.join(data, 'session.json')))['pid'] == new
        finally:
            for pid in owners: _kill(pid)
            for p in procs:
                if p.poll() is None: p.kill(); p.wait(30)
