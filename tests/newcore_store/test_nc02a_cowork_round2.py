"""Repros for Cowork's second pass on nc-02a-journal 8c12adb (PR #43), written before the fixes.

N1 the writer lock journal/.lock: the store NEVER unlinks it (not on release, not after a crash), so it can never
   delete a lock another writer holds. A stale .lock left by a crashed writer is harmless: the OS lock dies with the
   process. Only real contention reads as "held": an I/O error while locking is an OSError (typed by the caller),
   never JournalLocked. A .lock that is not a regular file (a directory, a link) is refused, never followed. On POSIX
   a lock whose path was swapped under it (unlinked + recreated by someone else) is not held.
N3 no raw OSError ever leaves recover_journal / create_journal: a failing kind / listdir / read /
   stat / lock / write is a typed verdict (UNREADABLE / DAMAGED / DURABILITY_UNAVAILABLE) or DurabilityUnavailable.
"""
import errno
import os
import subprocess
import sys

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, SCENARIO
from nc02a_memfs import FaultFs, MemFs, oserror
from nc02a_util import ACCT_DIR, mem_journal
from newcore.store import JournalLocked, Verdict, create_journal, recover_journal
from newcore.store.errors import DurabilityUnavailable
from newcore.store.fs import RealFs

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _real_journal(tmp_path, events=SCENARIO[:3]):
    acct = tmp_path / 'accounts' / ACCOUNT_ID
    (tmp_path / 'accounts').mkdir()
    j = create_journal(str(acct), ACCOUNT_ID, AGGREGATE_ID)
    for e in events:
        j.append(e)
    return acct, j


# ---------------------------------------------------------------------------------------------------- N1
def test_release_never_unlinks_the_lock_and_the_next_writer_takes_it(tmp_path):
    acct, j = _real_journal(tmp_path)
    lock = acct / 'journal' / '.lock'
    assert lock.is_file()
    j.close()
    assert lock.is_file()                                     # never unlinked on release
    r = recover_journal(str(acct), ACCOUNT_ID, AGGREGATE_ID)
    assert r.verdict is Verdict.CLEAN and r.journal.last_sequence() == 3
    r.journal.close()


def test_a_held_lock_cannot_be_deleted_or_shared(tmp_path):
    acct, j = _real_journal(tmp_path)
    lock = acct / 'journal' / '.lock'
    with pytest.raises(JournalLocked):
        recover_journal(str(acct), ACCOUNT_ID, AGGREGATE_ID)
    if sys.platform == 'win32':                               # the writer's open handle denies delete sharing
        with pytest.raises(PermissionError):
            os.remove(lock)
    j.close()


_HOLDER = '''
import sys
sys.path.insert(0, sys.argv[1])
from newcore.store.fs import RealFs
h = RealFs().lock_exclusive(sys.argv[2])
print('held' if h is not None else 'busy', flush=True)
if sys.argv[3] == 'crash':
    import os
    os._exit(0)                                             # no unlock, no close: a crashed writer
sys.stdin.readline()
'''


def test_a_crashed_writer_leaves_a_stale_lock_file_that_is_harmless(tmp_path):
    acct, j = _real_journal(tmp_path)
    j.close()
    lock = str(acct / 'journal' / '.lock')
    out = subprocess.run([sys.executable, '-c', _HOLDER, ROOT, lock, 'crash'], capture_output=True, text=True,
                         timeout=60)
    assert out.stdout.strip() == 'held'
    assert os.path.isfile(lock)                               # the stale file is still there ...
    r = recover_journal(str(acct), ACCOUNT_ID, AGGREGATE_ID)  # ... and does not block the next writer
    assert r.verdict is Verdict.CLEAN
    r.journal.close()


def test_a_live_holder_in_another_process_is_contention(tmp_path):
    acct, j = _real_journal(tmp_path)
    j.close()
    lock = str(acct / 'journal' / '.lock')
    p = subprocess.Popen([sys.executable, '-c', _HOLDER, ROOT, lock, 'hold'], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == 'held'
        with pytest.raises(JournalLocked):
            recover_journal(str(acct), ACCOUNT_ID, AGGREGATE_ID)
    finally:
        p.communicate('\n', timeout=60)
    r = recover_journal(str(acct), ACCOUNT_ID, AGGREGATE_ID)
    assert r.verdict is Verdict.CLEAN
    r.journal.close()


def test_an_io_error_while_locking_is_an_oserror_not_contention(tmp_path, monkeypatch):
    p = str(tmp_path / '.lock')
    if sys.platform == 'win32':
        import msvcrt

        def boom(fd, mode, n):
            raise OSError(errno.EIO, 'device error')
        monkeypatch.setattr(msvcrt, 'locking', boom)
    else:
        import fcntl

        def boom(fd, op):
            raise OSError(errno.EIO, 'device error')
        monkeypatch.setattr(fcntl, 'flock', boom)
    with pytest.raises(OSError) as ei:
        RealFs().lock_exclusive(p)
    assert ei.value.errno == errno.EIO


def test_contention_errnos_are_held_not_errors(tmp_path, monkeypatch):
    p = str(tmp_path / '.lock')
    codes = (errno.EACCES, getattr(errno, 'EDEADLOCK', errno.EDEADLK)) if sys.platform == 'win32' else \
        (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES)
    for code in codes:
        if sys.platform == 'win32':
            import msvcrt
            monkeypatch.setattr(msvcrt, 'locking', lambda fd, mode, n, c=code: (_ for _ in ()).throw(OSError(c, 'x')))
        else:
            import fcntl
            monkeypatch.setattr(fcntl, 'flock', lambda fd, op, c=code: (_ for _ in ()).throw(OSError(c, 'x')))
        assert RealFs().lock_exclusive(p) is None


def test_a_lock_path_that_is_a_directory_is_refused_not_followed(tmp_path):
    acct, j = _real_journal(tmp_path)
    j.close()
    lock = acct / 'journal' / '.lock'
    os.remove(lock)
    lock.mkdir()
    with pytest.raises(OSError):
        RealFs().lock_exclusive(str(lock))
    r = recover_journal(str(acct), ACCOUNT_ID, AGGREGATE_ID)  # typed, never a raw OSError
    assert r.verdict is Verdict.DURABILITY_UNAVAILABLE and r.journal is None and r.view is not None


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX only: Windows refuses to delete a file a writer holds open, '
                                                    'so the path cannot be swapped under a lock there')
def test_a_lock_whose_path_was_swapped_under_it_is_not_held(tmp_path, monkeypatch):
    import fcntl
    p = str(tmp_path / '.lock')
    real = fcntl.flock

    def swap_then_lock(fd, op):
        os.remove(p)                                          # another party unlinks + recreates the lock file
        open(p, 'wb').close()
        return real(fd, op)
    monkeypatch.setattr(fcntl, 'flock', swap_then_lock)
    assert RealFs().lock_exclusive(p) is None


@pytest.mark.skipif(not hasattr(os, 'symlink'), reason='no symlinks')
def test_a_lock_path_that_is_a_link_is_refused_not_followed(tmp_path):
    target = tmp_path / 'elsewhere'
    link = tmp_path / '.lock'
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip('creating a symlink needs a privilege here')
    with pytest.raises(OSError):
        RealFs().lock_exclusive(str(link))
    assert not target.exists()                                # nothing created through the link


# ---------------------------------------------------------------------------------------------------- N3
def _nth_read_fails(n):
    count = [0]

    def read_fail(op, path):
        count[0] += 1
        return oserror(errno.EIO) if count[0] == n + 1 else None
    return read_fail, count


def _reads(entry):
    """How many read calls `entry` makes on an unfaulted copy (sweep bound)."""
    fs = mem_journal(SCENARIO[:4])
    _, count = rf = _nth_read_fails(10 ** 9)
    entry(FaultFs(fs, read_fail=rf[0]))
    return count[0]


ENTRIES = {
    'recover_journal': lambda f: recover_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=f),
}


@pytest.mark.parametrize('name', sorted(ENTRIES))
def test_a_failing_read_anywhere_in_recovery_is_typed_never_a_raw_oserror(name):
    entry = ENTRIES[name]
    total = _reads(entry)
    assert total > 3
    raw = []
    for n in range(total):
        fs = mem_journal(SCENARIO[:4])
        rf, _ = _nth_read_fails(n)
        try:
            r = entry(FaultFs(fs, read_fail=rf))
        except OSError as ex:
            raw.append((n, type(ex).__name__))
            continue
        assert r.verdict in (Verdict.UNREADABLE, Verdict.DAMAGED, Verdict.DURABILITY_UNAVAILABLE, Verdict.CLEAN,
                             Verdict.REPAIRED, Verdict.MISSING), (n, r.verdict)
        if getattr(r, 'journal', None) is not None:
            r.journal.close()
    assert raw == []


def test_a_failing_mutation_anywhere_in_recovery_is_typed_never_a_raw_oserror():
    raw = []
    for n in range(40):
        fs = mem_journal(SCENARIO[:4])
        f = FaultFs(fs, fail=lambda o, p, i, n=n: oserror(errno.EIO) if i == n else None)
        try:
            r = recover_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=f)
        except OSError as ex:
            raw.append((n, type(ex).__name__))
            continue
        if r.journal is not None:
            r.journal.close()
    assert raw == []


def test_a_failing_call_anywhere_in_create_is_durability_unavailable_never_a_raw_oserror():
    raw = []
    for n in range(20):
        for mode in ('read', 'mut'):
            fs = MemFs(os.path.dirname(ACCT_DIR))
            if mode == 'read':
                rf, _ = _nth_read_fails(n)
                f = FaultFs(fs, read_fail=rf)
            else:
                f = FaultFs(fs, fail=lambda o, p, i, n=n: oserror(errno.EIO) if i == n else None)
            try:
                j = create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=f)
            except DurabilityUnavailable:
                continue
            except OSError as ex:
                raw.append((mode, n, type(ex).__name__))
                continue
            j.close()
    assert raw == []
