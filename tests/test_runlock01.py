"""RUNLOCK-01 (issue #167): one live process per one-shot research run across every worktree of the repository.

Real two-process tests: a holder subprocess acquires the lock (from one worktree) and the test process tries the same
run from another worktree / spelling. Liveness comes from the OS lock only - forged or stale metadata never decides."""
import errno
import hashlib
import json
import os
import subprocess
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), 'tools', 'research')
sys.path.insert(0, TOOLS)
import binance_archive_fill as BF                                                           # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import runlock as RL                                                                        # noqa: E402

WINDOW = '2021-01-04T00:00:00Z/2026-09-28T00:00:00Z'
P = {'window': WINDOW, 'tier': 'CORE-10', 'store': None}

HOLDER = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import runlock as RL
repo, kind, ident, mode, root = sys.argv[2], sys.argv[3], json.loads(sys.argv[4]), sys.argv[5], sys.argv[6] or None
with RL.hold(repo, kind, ident, argv=['holder'], root=root, bind_commit=sys.argv[7] == '1'):
    print('HELD', flush=True)
    if mode == 'crash':
        os._exit(3)                                  # dies holding the lock: no release, no finally
    sys.stdin.readline()                             # hold until the test says go
print('RELEASED', flush=True)
'''


def _git(cwd, *args):
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', *args],
                   cwd=cwd, check=True, capture_output=True, stdin=subprocess.DEVNULL)


@pytest.fixture
def worktrees(tmp_path):
    """A throwaway repository with two worktrees on the same commit; locks live in its shared Git common dir."""
    wt1, wt2 = tmp_path / 'wt1', tmp_path / 'wt2'
    wt1.mkdir()
    _git(wt1, 'init', '-q')
    (wt1 / 'a.txt').write_text('a')
    _git(wt1, 'add', 'a.txt')
    _git(wt1, 'commit', '-q', '-m', 'a')
    _git(wt1, 'worktree', 'add', '-q', '--detach', str(wt2))
    return str(wt1), str(wt2)


class Holder:
    def __init__(self, tmp_path, repo, kind, ident, mode='hold', root=None, bind_commit=True):
        script = tmp_path / 'holder.py'
        script.write_text(HOLDER)
        self.p = subprocess.Popen([sys.executable, str(script), TOOLS, repo, kind, json.dumps(ident), mode,
                                   root or '', '1' if bind_commit else '0'], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True)
        line = self.p.stdout.readline().strip()
        assert line == 'HELD', (line, self.p.stderr.read() if self.p.poll() is not None else '')

    def release(self):
        self.p.stdin.write('go\n')
        self.p.stdin.flush()
        assert self.p.stdout.readline().strip() == 'RELEASED'
        assert self.p.wait(30) == 0

    def kill(self):
        self.p.kill()
        self.p.wait(30)

    def close(self):
        if self.p.poll() is None:
            self.kill()
        for s in (self.p.stdin, self.p.stdout, self.p.stderr):
            s.close()


@pytest.fixture
def holders():
    out = []
    yield out
    for h in out:
        h.close()


def _key(wt, kind, ident):
    head = subprocess.run(['git', '-C', wt, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    return RL.run_key(RL.repo_identity(wt), kind, head, ident)


def _base(wt, kind='rex', ident=P):
    return os.path.join(RL.repo_identity(wt), RL.SUBDIR, _key(wt, kind, ident))


def _audit(wt, kind='rex', ident=P):
    with open(_base(wt, kind, ident) + '.audit.jsonl', encoding='utf-8') as f:
        return [json.loads(x) for x in f]


def _snapshot(d):
    out = {}
    for base, _, names in os.walk(d):
        for n in names:
            p = os.path.join(base, n)
            st = os.stat(p)
            if n.endswith('.lock'):                     # a held byte-range lock is unreadable on Windows
                out[os.path.relpath(p, d)] = (st.st_size, st.st_mtime_ns)
                continue
            with open(p, 'rb') as f:
                out[os.path.relpath(p, d)] = (st.st_mtime_ns, hashlib.sha256(f.read()).hexdigest())
    return out


# ---------------------------------------------------------------- exclusion across worktrees

def test_both_worktrees_share_one_lock_root(worktrees):
    wt1, wt2 = worktrees
    assert RL.repo_identity(wt1) == RL.repo_identity(wt2)


def test_the_same_run_from_another_worktree_is_refused_while_live(tmp_path, worktrees, holders):
    wt1, wt2 = worktrees
    holders.append(Holder(tmp_path, wt1, 'rex', P))
    with pytest.raises(RL.RunActive, match=r'^run already active: rex key [0-9a-f]{12} held by pid '):
        with RL.hold(wt2, 'rex', P):
            pytest.fail('a second holder must never get here')


def test_the_refusal_names_the_live_owner(tmp_path, worktrees, holders):
    wt1, wt2 = worktrees
    h = Holder(tmp_path, wt1, 'rex', P)
    holders.append(h)
    with pytest.raises(RL.RunActive) as e:
        with RL.hold(wt2, 'rex', P):
            pass
    assert f'pid {h.p.pid} ' in str(e.value) and RL.canon_path(wt1) in str(e.value)


def test_the_loser_changes_no_byte_of_the_lock_infrastructure(tmp_path, worktrees, holders):
    wt1, wt2 = worktrees
    holders.append(Holder(tmp_path, wt1, 'rex', P))
    d = os.path.join(RL.repo_identity(wt1), RL.SUBDIR)
    before = _snapshot(d)
    for _ in range(3):
        with pytest.raises(RL.RunActive):
            with RL.hold(wt2, 'rex', P):
                pass
    assert _snapshot(d) == before                                                  # owner, audit, lock: untouched


def test_input_path_spellings_canonicalise_to_one_identity(tmp_path, worktrees, holders, monkeypatch):
    wt1, wt2 = worktrees
    store = tmp_path / 'store'
    store.mkdir()
    holders.append(Holder(tmp_path, wt1, 'rex', dict(P, store=RL.canon_path(str(store)))))
    monkeypatch.chdir(tmp_path)
    spellings = ['store', './store', str(store), str(store) + os.sep, os.path.join('wt2', '..', 'store')]
    if sys.platform == 'win32':
        spellings.append(str(store).upper())
    for spelling in spellings:
        with pytest.raises(RL.RunActive):
            with RL.hold(wt2, 'rex', dict(P, store=RL.canon_path(spelling))):
                pass


def test_different_run_identities_run_concurrently(tmp_path, worktrees, holders):
    wt1, wt2 = worktrees
    holders.append(Holder(tmp_path, wt1, 'rex', P))
    for kind, ident in (('rex', dict(P, tier='CORE-20')), ('rex', dict(P, variant='range_vwap_rev.v1-splitcut')),
                        ('pit', P), ('archive_fill', {'report_sha256': '0' * 64})):
        with RL.hold(wt2, kind, ident) as owner:
            assert owner['state'] == 'held' and owner['kind'] == kind


def test_a_second_hold_in_the_same_process_is_refused(worktrees):
    wt1, _ = worktrees
    with RL.hold(wt1, 'rex', P):
        with pytest.raises(RL.RunActive):
            with RL.hold(wt1, 'rex', P):
                pass


def test_a_new_commit_is_a_new_identity(tmp_path, worktrees, holders):
    wt1, wt2 = worktrees
    holders.append(Holder(tmp_path, wt1, 'rex', P))
    (tmp_path / 'wt2' / 'b.txt').write_text('b')
    _git(wt2, 'add', 'b.txt')
    _git(wt2, 'commit', '-q', '-m', 'b')
    with RL.hold(wt2, 'rex', P):
        pass


def test_a_report_bound_acquisition_collides_on_every_commit(tmp_path, worktrees, holders):
    """F1: the archive fetch owns shared zips/output for one report - a new commit must NOT make it a new resource."""
    wt1, wt2 = worktrees
    A = {'report_sha256': 'a' * 64, 'fetch': True, 'plan_only': True}
    with RL.hold(wt1, 'archive_fill', A, bind_commit=False) as owner:  # the record says it is not commit-bound
        assert owner['commit_bound'] is False
    holders.append(Holder(tmp_path, wt1, 'archive_fill', A, bind_commit=False))
    (tmp_path / 'wt2' / 'b.txt').write_text('b')
    _git(wt2, 'add', 'b.txt')
    _git(wt2, 'commit', '-q', '-m', 'b')
    with pytest.raises(RL.RunActive, match='run already active: archive_fill'):
        with RL.hold(wt2, 'archive_fill', A, bind_commit=False):
            pass
    with RL.hold(wt2, 'archive_fill', A) as owner:                  # the commit-bound key is a different lock
        assert owner['commit_bound'] is True


# ---------------------------------------------------------------- handle lifetime, release, death, crash, metadata

def test_the_lock_file_is_one_stable_seed_byte_and_never_unlinked(worktrees):
    wt1, wt2 = worktrees
    for wt in (wt1, wt2, wt1):
        with RL.hold(wt, 'rex', P):
            pass
        with open(_base(wt1) + '.lock', 'rb') as f:
            assert f.read() == b'\0'


def test_release_closes_the_handle_and_unlocks_for_another_process(tmp_path, worktrees, holders):
    wt1, _ = worktrees
    with RL.hold(wt1, 'rex', P):
        pass
    with RL.hold(wt1, 'rex', P):                                                   # same process, immediately
        pass
    h = Holder(tmp_path, wt1, 'rex', P)                                            # another process, immediately
    holders.append(h)
    h.release()


def test_release_on_normal_exit_lets_the_next_run_start_and_is_audited(tmp_path, worktrees, holders):
    wt1, wt2 = worktrees
    h = Holder(tmp_path, wt1, 'rex', P)
    holders.append(h)
    h.release()
    with RL.hold(wt2, 'rex', P):
        pass
    assert [r['event'] for r in _audit(wt2)] == ['acquired', 'released', 'acquired', 'released']


def test_a_handled_exception_releases_the_lock(worktrees):
    wt1, wt2 = worktrees
    with pytest.raises(ZeroDivisionError):
        with RL.hold(wt1, 'rex', P):
            1 / 0
    with open(_base(wt1) + '.owner.json', encoding='utf-8') as f:
        assert json.load(f)['state'] == 'released'
    with RL.hold(wt2, 'rex', P):
        pass


@pytest.mark.parametrize('mode', ['kill', 'crash'])
def test_a_dead_owner_is_recovered_atomically_and_audited(tmp_path, worktrees, holders, mode):
    wt1, wt2 = worktrees
    h = Holder(tmp_path, wt1, 'rex', P, mode='crash' if mode == 'crash' else 'hold')
    holders.append(h)
    if mode == 'kill':
        h.kill()
    else:
        assert h.p.wait(30) == 3
    with RL.hold(wt2, 'rex', P) as owner:
        assert owner['pid'] == os.getpid()
    rec = [r for r in _audit(wt2) if r['event'] == 'recovered']
    assert len(rec) == 1 and rec[0]['previous']['pid'] == h.p.pid and rec[0]['by'] == os.getpid()


def test_forged_live_pid_metadata_never_blocks_a_free_lock(worktrees):
    """PID reuse: the owner file names a LIVE pid (ours) but nobody holds the OS lock -> recovered, not refused."""
    wt1, wt2 = worktrees
    with RL.hold(wt1, 'rex', P):
        pass
    with open(_base(wt1) + '.owner.json', 'w', encoding='utf-8') as f:
        json.dump({'state': 'held', 'pid': os.getpid(), 'process_start': 'win-filetime:1'}, f)
    with RL.hold(wt2, 'rex', P):
        pass
    assert [r['event'] for r in _audit(wt2)][-3:] == ['recovered', 'acquired', 'released']


@pytest.mark.parametrize('meta', [None, b'', b'{not json', b'{"state": "released", "pid": 1}'])
def test_a_live_owner_with_missing_or_wrong_metadata_is_never_stolen(tmp_path, worktrees, holders, meta):
    """Uncertain owner: the OS lock is held, whatever the owner file says (absent, empty, garbage, 'released')."""
    wt1, wt2 = worktrees
    holders.append(Holder(tmp_path, wt1, 'rex', P))
    owner = _base(wt1) + '.owner.json'
    if meta is None:
        os.remove(owner)
    else:
        with open(owner, 'wb') as f:
            f.write(meta)
    with pytest.raises(RL.RunActive, match='held by a live process whose owner record is not readable'):
        with RL.hold(wt2, 'rex', P):
            pass


def test_the_owner_file_carries_bounded_metadata_and_no_argv(worktrees):
    wt1, _ = worktrees
    with RL.hold(wt1, 'rex', P, argv=['rex', '--secret-looking', 'value']) as owner:
        assert set(owner) == {'format', 'state', 'key', 'kind', 'commit', 'commit_bound', 'repo', 'identity_sha256',
                              'pid', 'process_start', 'acquired_utc', 'acquired_cairo', 'worktree', 'argv_sha256'}
        assert owner['identity_sha256'] == RL.identity_digest(P) and owner['commit_bound'] is True
        assert owner['acquired_utc'].endswith('Z') and owner['acquired_cairo'][-6:] in ('+02:00', '+03:00')
        assert owner['process_start'] is None or owner['process_start'].startswith(('win-filetime:', 'linux-ticks:'))
        assert 'value' not in json.dumps(owner)


def test_a_secret_shaped_identity_value_is_never_persisted(worktrees):
    """Identity is auto-derived from every CLI argument, so a future token/key argument must stay in memory only."""
    wt1, _ = worktrees
    secret = 'sk_live_' + 'Q' * 40
    ident = dict(P, api_token=secret)
    with RL.hold(wt1, 'rex', ident) as owner:
        assert secret not in json.dumps(owner)
    base = _base(wt1, 'rex', ident)
    for suffix in ('.owner.json', '.audit.jsonl'):
        with open(base + suffix, encoding='utf-8') as f:
            text = f.read()
        assert secret not in text and 'api_token' not in text
    assert _key(wt1, 'rex', ident) != _key(wt1, 'rex', dict(P, api_token='sk_live_' + 'R' * 40))
    assert RL.identity_digest(ident) != RL.identity_digest(dict(P, api_token='other'))


# ---------------------------------------------------------------- fail closed

def test_a_non_git_directory_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    with pytest.raises(RL.RunLockError, match='not a Git checkout'):
        with RL.hold(str(tmp_path), 'rex', P):
            pass


def test_an_uncreatable_lock_root_is_refused(tmp_path, worktrees):
    wt1, _ = worktrees
    blocker = tmp_path / 'not-a-dir'
    blocker.write_text('x')
    with pytest.raises(RL.RunLockError, match='cannot open'):
        with RL.hold(wt1, 'rex', P, root=str(blocker / 'locks')):
            pass


def test_a_lock_error_other_than_contention_fails_closed(worktrees, monkeypatch):
    wt1, _ = worktrees
    def boom(*a):
        raise OSError(errno.EBADF, 'bad file descriptor')
    if sys.platform == 'win32':
        import msvcrt
        monkeypatch.setattr(msvcrt, 'locking', boom)
    else:
        import fcntl
        monkeypatch.setattr(fcntl, 'flock', boom)
    with pytest.raises(RL.RunLockError, match='cannot lock') as e:
        with RL.hold(wt1, 'rex', P):
            pass
    assert not isinstance(e.value, RL.RunActive)


def test_there_is_no_environment_override(tmp_path, worktrees, monkeypatch):
    wt1, _ = worktrees
    monkeypatch.setenv('ZB_RUNLOCK_DIR', str(tmp_path / 'elsewhere'))
    with RL.hold(wt1, 'rex', P):
        pass
    assert not (tmp_path / 'elsewhere').exists() and os.path.exists(_base(wt1) + '.lock')


@pytest.mark.parametrize('bad', [dict(P, s={1, 2}), dict(P, x=float('nan')), dict(P, x=float('-inf')),
                                 dict(P, o=object()), {(1, 2): 'tuple key'}],
                         ids=['set', 'nan', 'inf', 'object', 'key'])
def test_a_non_json_identity_fails_closed_before_anything_is_created(tmp_path, worktrees, bad):
    """F3: canonical identity serialisation errors are RunLockError, never a raw TypeError / ValueError."""
    wt1, _ = worktrees
    root = tmp_path / 'locks'
    with pytest.raises(RL.RunLockError, match='not canonical JSON') as e:
        with RL.hold(wt1, 'rex', bad, root=str(root)):
            pass
    assert not isinstance(e.value, RL.RunActive) and not root.exists()
    with pytest.raises(RL.RunLockError):
        RL.identity_digest(bad)


def test_owner_replace_rides_out_a_brief_sharing_violation(worktrees, monkeypatch):
    """F4: a reader briefly holding the owner file (Windows) -> bounded retry while the OS lock stays held."""
    wt1, wt2 = worktrees
    real, fails = os.replace, {'n': 3}
    def flaky(src, dst):
        if dst.endswith('.owner.json') and fails['n']:
            fails['n'] -= 1
            raise PermissionError(errno.EACCES, 'sharing violation')
        real(src, dst)
    monkeypatch.setattr(RL.os, 'replace', flaky)
    monkeypatch.setattr(RL.time, 'sleep', lambda s: None)
    with RL.hold(wt1, 'rex', P) as owner:
        assert owner['state'] == 'held' and fails['n'] == 0
    with open(_base(wt1) + '.owner.json', encoding='utf-8') as f:
        assert json.load(f)['state'] == 'released'


def test_a_persistent_sharing_violation_fails_closed_and_unlocks(worktrees, monkeypatch):
    wt1, wt2 = worktrees
    real, calls = os.replace, []
    def stuck(src, dst):
        calls.append(dst)
        raise PermissionError(errno.EACCES, 'sharing violation')
    monkeypatch.setattr(RL.os, 'replace', stuck)
    monkeypatch.setattr(RL.time, 'sleep', lambda s: None)
    with pytest.raises(RL.RunLockError, match='cannot record the owner') as e:
        with RL.hold(wt1, 'rex', P):
            pytest.fail('must not start without an owner record')
    assert not isinstance(e.value, RL.RunActive) and len(calls) == RL.REPLACE_TRIES       # bounded, then gives up
    monkeypatch.setattr(RL.os, 'replace', real)
    with RL.hold(wt2, 'rex', P):                                                       # the OS lock was released
        pass


CHURN = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
import runlock as RL
repo, root, n = sys.argv[2], sys.argv[3], int(sys.argv[4])
wins = lost = 0
for _ in range(n):
    try:
        with RL.hold(repo, 'rex', {'churn': 1}, root=root):
            wins += 1
            time.sleep(0.003)
    except RL.RunActive as e:
        assert e.args[0].startswith('run already active: rex key '), e.args[0]
        lost += 1
print(wins, lost, flush=True)
'''


def test_multi_process_churn_never_breaks_the_lock_or_its_records(tmp_path, worktrees):
    """F4 (real processes): 6 processes x 40 attempts on one key, losers reading the owner file while winners replace
    it. No unexpected error anywhere; every win is exactly one acquired + one released record; never two at once."""
    wt1, wt2 = worktrees
    root = tmp_path / 'locks'
    script = tmp_path / 'churn.py'
    script.write_text(CHURN)
    procs = [subprocess.Popen([sys.executable, str(script), TOOLS, (wt1, wt2)[i % 2], str(root), '40'],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(6)]
    wins = 0
    for p in procs:
        out, err = p.communicate(timeout=300)
        assert p.returncode == 0, err
        w, lost = map(int, out.split())
        assert w + lost == 40
        wins += w
    key = RL.run_key(RL.repo_identity(wt1), 'rex', subprocess.run(['git', '-C', wt1, 'rev-parse', 'HEAD'],
                                                                  capture_output=True, text=True).stdout.strip(),
                     {'churn': 1})
    with open(root / (key + '.audit.jsonl'), encoding='utf-8') as f:
        ev = [json.loads(x)['event'] for x in f]
    assert wins >= 1 and ev.count('acquired') == ev.count('released') == wins and 'recovered' not in ev
    depth = 0
    for e in ev:                                                   # strictly alternating: never two holders at once
        depth += 1 if e == 'acquired' else -1
        assert depth in (0, 1)
    with open(root / (key + '.owner.json'), encoding='utf-8') as f:
        assert json.load(f)['state'] == 'released'


# ---------------------------------------------------------------- the one-shot commands

class Captured(Exception):
    pass


def _captured(monkeypatch, fn, argv):
    """The exact (repo, kind, identity, keywords) a command passes to RL.hold, captured before anything happens."""
    def cap(repo, kind, ident, **kw):
        raise Captured(repo, kind, ident, kw)
    with monkeypatch.context() as m:
        m.setattr(RL, 'hold', cap)
        with pytest.raises(Captured) as e:
            fn(argv)
    return e.value.args


def _captured_identity(monkeypatch, fn, argv):
    return _captured(monkeypatch, fn, argv)[:3]


REX = ['rex', '--window', WINDOW, '--tier', 'CORE-10']


def test_rex_identity_is_the_whole_command_minus_the_exclusions(monkeypatch, tmp_path):
    _, kind, ident = _captured_identity(monkeypatch, G.main, REX + ['--out-dir', str(tmp_path / 'a')])
    assert kind == 'rex' and set(ident) == {'window', 'tier', 'variant', 'store', 'notice', 'notice_sha256', 'addon',
                                            'addon_report', 'addon_sha256', 'addon_zips'}
    same = [REX + ['--out-dir', str(tmp_path / 'b'), '--author', 'x', '--cairo-date', '2026-10-21', '--resume']]
    for argv in same:
        assert _captured_identity(monkeypatch, G.main, argv)[2] == ident
    for extra in (['--variant', 'range_vwap_rev.v1-splitcut'], ['--addon-sha256', 'f' * 64],
                  ['--notice-sha256', 'e' * 64], ['--store', str(tmp_path)]):
        assert _captured_identity(monkeypatch, G.main, REX + extra)[2] != ident, extra
    other = ['rex', '--window', '2021-01-04T00:00:00Z/2026-09-27T20:00:00Z', '--tier', 'CORE-10']
    assert _captured_identity(monkeypatch, G.main, other)[2] != ident


def test_evaluation_commands_stay_commit_bound(monkeypatch, tmp_path):
    for argv in (REX, ['pit', '--store', str(tmp_path), '--author', 'x', '--cairo-date', 'd'],
                 ['m3', '--store', str(tmp_path), '--author', 'x', '--cairo-date', 'd']):
        assert _captured(monkeypatch, G.main, argv)[3].get('bind_commit', True) is True, argv[0]


def test_a_new_argument_enters_the_identity_automatically():
    a = types.SimpleNamespace(cmd='pit', candidate='c', store=None, out_dir='o', author='x', cairo_date='d',
                              resume=False)
    b = types.SimpleNamespace(**vars(a), future_knob=7)
    assert G.run_lock_identity(a) != G.run_lock_identity(b)


def test_pit_and_m3_take_the_lock_before_anything_else(monkeypatch, tmp_path):
    for argv in (['pit', '--store', str(tmp_path), '--author', 'x', '--cairo-date', 'd'],
                 ['m3', '--store', str(tmp_path), '--author', 'x', '--cairo-date', 'd']):
        repo, kind, ident = _captured_identity(monkeypatch, G.main, argv)
        assert repo == G.M.REPO and kind == argv[0] and ident['store'] == RL.canon_path(str(tmp_path))
        assert not {'out_dir', 'author', 'cairo_date', 'resume'} & set(ident)


def test_the_rex_window_must_be_a_strict_pair():
    a = types.SimpleNamespace(cmd='rex', window='2021-01-04T00:00:00Z')
    with pytest.raises(ValueError):
        G.run_lock_identity(a)
    with pytest.raises(G.S.SplitError):
        G.run_lock_identity(types.SimpleNamespace(cmd='rex', window='2021-01-04/2026-09-28'))


def test_a_losing_rex_command_exits_5_and_writes_nothing(tmp_path, holders, monkeypatch, capsys):
    locks = str(tmp_path / 'locks')
    repo, kind, ident = _captured_identity(monkeypatch, G.main, REX + ['--out-dir', str(tmp_path / 'x')])
    holders.append(Holder(tmp_path, repo, kind, ident, root=locks))
    real = RL.hold
    monkeypatch.setattr(RL, 'hold', lambda *a, **k: real(*a, root=locks, **k))
    evidence = os.path.join(G.M.REPO, 'research_evidence')
    before, lock_before = _snapshot(evidence), _snapshot(locks)
    out = tmp_path / 'out'
    assert G.main(REX + ['--out-dir', str(out)]) == 5
    assert 'run already active: rex key ' in capsys.readouterr().err
    assert not out.exists() and _snapshot(evidence) == before and _snapshot(locks) == lock_before


def test_a_losing_archive_fetch_exits_5_before_any_network(tmp_path, holders, monkeypatch, capsys):
    locks = str(tmp_path / 'locks')
    (tmp_path / 'report.json').write_bytes(b'{"format": "stub"}')
    monkeypatch.setattr(BF, 'check_report', lambda d: d)
    monkeypatch.setattr(BF, 'http_fetcher', lambda url: pytest.fail('the loser must not touch the network'))
    monkeypatch.setattr(BF, 'fill', lambda *a, **k: pytest.fail('the loser must not fill'))
    out, zips = tmp_path / 'addon.json', tmp_path / 'zips'
    argv = ['--report', str(tmp_path / 'report.json'), '--out', str(out), '--fetch', '--zips', str(zips)]
    repo, kind, ident, kw = _captured(monkeypatch, BF.main, argv)
    assert kind == 'archive_fill' and ident['report_sha256'] == hashlib.sha256(b'{"format": "stub"}').hexdigest()
    assert not {'out', 'zips', 'report'} & set(ident)
    assert kw['bind_commit'] is False                                  # F1: report-bound, never split by HEAD
    holders.append(Holder(tmp_path, repo, kind, ident, root=locks, bind_commit=False))
    real = RL.hold
    monkeypatch.setattr(RL, 'hold', lambda *a, **k: real(*a, root=locks, **k))
    assert BF.main(argv) == 5
    assert 'run already active: archive_fill key ' in capsys.readouterr().err
    assert not out.exists() and not zips.exists()


def test_the_same_report_spelled_elsewhere_is_the_same_fetch(tmp_path, monkeypatch):
    raw = b'{"format": "stub"}'
    (tmp_path / 'r1.json').write_bytes(raw)
    (tmp_path / 'r2.json').write_bytes(raw)
    monkeypatch.setattr(BF, 'check_report', lambda d: d)
    ids = [_captured_identity(monkeypatch, BF.main, ['--report', str(tmp_path / r), '--out', str(tmp_path / o),
                                                     '--fetch', '--zips', str(tmp_path / z)])[2]
           for r, o, z in (('r1.json', 'a', 'za'), ('r2.json', 'b', 'zb'))]
    assert ids[0] == ids[1]


def _fetch_argv(tmp_path, monkeypatch, calls):
    (tmp_path / 'report.json').write_bytes(b'{"format": "stub"}')
    monkeypatch.setattr(BF, 'check_report', lambda d: d)
    monkeypatch.setattr(BF, 'http_fetcher', lambda url: pytest.fail('no network in this test'))
    def fill(*a, **k):
        calls.append('fill')
        return {'rows': [], 'genuine_gaps': [], 'stopped': None}
    monkeypatch.setattr(BF, 'fill', fill)
    return ['--report', str(tmp_path / 'report.json'), '--out', str(tmp_path / 'addon.json'), '--fetch', '--zips',
            str(tmp_path / 'zips')]


@pytest.mark.parametrize('where', ['repo', 'root', 'lock', 'owner'])
def test_an_archive_fetch_lock_acquisition_failure_exits_2_before_any_work(tmp_path, monkeypatch, capsys, where):
    calls = []
    argv = _fetch_argv(tmp_path, monkeypatch, calls)
    real = RL.hold
    if where == 'repo':
        monkeypatch.setattr(RL, 'repo_identity', lambda repo: (_ for _ in ()).throw(RL.RunLockError('run lock: x')))
    elif where == 'root':
        (tmp_path / 'blocker').write_text('x')
        monkeypatch.setattr(RL, 'hold', lambda *a, **k: real(*a, root=str(tmp_path / 'blocker' / 'l'), **k))
    elif where == 'lock':
        monkeypatch.setattr(RL, 'hold', lambda *a, **k: real(*a, root=str(tmp_path / 'locks'), **k))
        def boom(*a):
            raise OSError(errno.EBADF, 'bad file descriptor')
        if sys.platform == 'win32':
            import msvcrt
            monkeypatch.setattr(msvcrt, 'locking', boom)
        else:
            import fcntl
            monkeypatch.setattr(fcntl, 'flock', boom)
    else:
        monkeypatch.setattr(RL, 'hold', lambda *a, **k: real(*a, root=str(tmp_path / 'locks'), **k))
        monkeypatch.setattr(RL, '_write_owner', lambda *a: (_ for _ in ()).throw(OSError(errno.ENOSPC, 'disk full')))
    assert BF.main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith('error: run lock: ') and 'Traceback' not in err
    assert calls == [] and not (tmp_path / 'addon.json').exists() and not (tmp_path / 'zips').exists()


def test_an_archive_fetch_release_record_failure_exits_2(tmp_path, monkeypatch, capsys):
    calls = []
    argv = _fetch_argv(tmp_path, monkeypatch, calls)
    real, writes = RL.hold, []
    monkeypatch.setattr(RL, 'hold', lambda *a, **k: real(*a, root=str(tmp_path / 'locks'), **k))
    real_write = RL._write_owner
    def write(path, doc):
        writes.append(doc['state'])
        if doc['state'] == 'released':
            raise OSError(errno.ENOSPC, 'disk full')
        real_write(path, doc)
    monkeypatch.setattr(RL, '_write_owner', write)
    assert BF.main(argv) == 2
    assert capsys.readouterr().err.startswith('error: run lock: cannot record the release')
    assert calls == ['fill'] and writes == ['held', 'released']


def test_plan_only_takes_no_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(BF, 'check_report', lambda d: d)
    monkeypatch.setattr(BF, 'plan_doc', lambda rep: {'requests': [], 'fetches': 0})
    monkeypatch.setattr(RL, 'hold', lambda *a, **k: pytest.fail('plan-only must not lock'))
    (tmp_path / 'report.json').write_bytes(b'{}')
    assert BF.main(['--report', str(tmp_path / 'report.json'), '--out', str(tmp_path / 'plan.json')]) == 0
