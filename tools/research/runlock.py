"""RUNLOCK-01: one live process per one-shot research run, across every worktree of the repository (issue #167).

The PR #166 incident: two worktrees of the same repository started the same registered run; each worktree's relative
out-dir guard saw only its own tree. This lock is keyed to the REPOSITORY (the Git common dir every worktree shares)
plus the run's complete immutable identity, so a second start from any worktree is refused before it touches anything.

Rules:
  * the key is sha256 over {repo: canonical Git common dir, kind, commit: HEAD, identity}; the caller builds
    `identity` from its whole parsed command minus an explicit exclusion list (out-dir / author / Cairo date /
    resume), so every identity-bearing argument added later enters the key automatically;
  * the lock root is always `<Git common dir>/zb-runlock` - no environment override; it fails closed (RunLockError)
    when the common dir cannot be resolved or the root cannot be created;
  * exclusion is an OS advisory lock on byte 0 of `<key>.lock` (Windows `msvcrt.locking`, POSIX `fcntl.flock`),
    taken non-blocking: acquisition is atomic and the kernel drops it when the owner process dies, so a live (or
    hung) owner is never stolen and a reused PID can never fake liveness - liveness is the lock, never the metadata;
  * the `.lock` file is never unlinked (that races a concurrent opener into a second "holder"); its single seed
    byte is written by the first holder only;
  * owner metadata `<key>.owner.json` (pid, OS process start time where exposed, Africa/Cairo + UTC time, worktree,
    kind, identity, argv digest; never secrets or argv text) and `<key>.audit.jsonl` are written ONLY while the OS
    lock is held. A free lock whose owner file still says `held` proves that owner died without releasing: the
    takeover is audited as `recovered`;
  * release (normal exit and handled failure) marks the owner `released`, audits it and unlocks. A sealed run stays
    refused by the run's own immutable-output rules after the transient lock is gone;
  * the loser raises `RunActive` ("run already active: ...") having only opened (at most created) the lock file;
  * contention is the only lock failure read as "active": any other OS error fails closed as RunLockError.
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CAIRO = ZoneInfo('Africa/Cairo')
FORMAT = 'zb-runlock/1'
SUBDIR = 'zb-runlock'
_BUSY = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, 'EDEADLOCK', errno.EDEADLK)}


class RunLockError(Exception):
    """The lock infrastructure failed: fail closed, start nothing."""


class RunActive(RunLockError):
    """Another live process holds the same run key."""


def canon_path(p: str | None) -> str | None:
    return None if p is None else os.path.normcase(os.path.realpath(p))


def _git(repo: str, *args: str) -> str:
    try:
        out = subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, check=True,
                             stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise RunLockError(f'run lock: {repo} is not a Git checkout ({e}); refusing to start') from None
    if not out:
        raise RunLockError(f'run lock: git {" ".join(args)} returned nothing in {repo}; refusing to start')
    return out


def repo_identity(repo: str) -> str:
    """The Git common dir, identical for every worktree of one repository."""
    common = _git(repo, 'rev-parse', '--git-common-dir')
    return canon_path(common if os.path.isabs(common) else os.path.join(repo, common))


def run_key(repo_id: str, kind: str, commit: str, identity: dict) -> str:
    doc = {'format': FORMAT, 'repo': repo_id, 'kind': kind, 'commit': commit, 'identity': identity}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def process_start() -> str | None:
    """This process's start time as the OS reports it (Windows FILETIME / Linux clock ticks), else None."""
    try:
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            k32.GetProcessTimes.restype = wintypes.BOOL
            times = [wintypes.FILETIME() for _ in range(4)]
            if k32.GetProcessTimes(k32.GetCurrentProcess(), *[ctypes.byref(t) for t in times]):
                return f'win-filetime:{(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}'
            return None
        with open('/proc/self/stat', encoding='ascii') as f:
            return f'linux-ticks:{f.read().rsplit(")", 1)[1].split()[19]}'
    except Exception:                                   # metadata only: never let it block or break a lock
        return None


def _try_lock(f) -> bool:
    """True = held; False = another holder (contention); any other failure raises RunLockError (fail closed)."""
    f.seek(0)
    try:
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in _BUSY:
            return False
        raise RunLockError(f'run lock: cannot lock {f.name} ({e}); refusing to start') from None
    return True


def _unlock(f) -> None:
    f.seek(0)
    if sys.platform == 'win32':
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _read_json(path: str):
    try:
        with open(path, 'rb') as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return None


def _write_owner(path: str, doc: dict) -> None:
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(doc, f, sort_keys=True, indent=1)
        f.write('\n')
    os.replace(tmp, path)


def _audit(path: str, event: str, **kw) -> None:
    with open(path, 'a', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(dict(event=event, utc=_utc(datetime.now(timezone.utc)), **kw), sort_keys=True) + '\n')


def _utc(t: datetime) -> str:
    return t.isoformat(timespec='seconds').replace('+00:00', 'Z')


def describe(owner) -> str:
    if not isinstance(owner, dict) or owner.get('state') != 'held':
        return 'a live process whose owner record is not readable'
    return (f"pid {owner.get('pid')} since {owner.get('acquired_cairo')} Cairo "
            f"(worktree {owner.get('worktree')})")


@contextlib.contextmanager
def hold(repo: str, kind: str, identity: dict, *, argv=None, root: str | None = None):
    """Hold the run lock for `kind` + `identity` at `repo`'s HEAD; raises RunActive while another process holds it.
    `root` replaces the lock directory for tests only; production callers never pass it."""
    repo_id = repo_identity(repo)
    commit = _git(repo, 'rev-parse', 'HEAD')
    key = run_key(repo_id, kind, commit, identity)
    d = root or os.path.join(repo_id, SUBDIR)
    base = os.path.join(d, key)
    try:
        os.makedirs(d, exist_ok=True)
        f = open(base + '.lock', 'a+b')
    except OSError as e:
        raise RunLockError(f'run lock: cannot open {base}.lock ({e}); refusing to start') from None
    try:
        if not _try_lock(f):
            raise RunActive(f'run already active: {kind} key {key[:12]} held by '
                            f'{describe(_read_json(base + ".owner.json"))} - do not start a second copy')
        try:
            now = datetime.now(timezone.utc)
            owner = {'format': FORMAT, 'state': 'held', 'key': key, 'kind': kind, 'commit': commit, 'repo': repo_id,
                     'identity': identity, 'pid': os.getpid(), 'process_start': process_start(),
                     'acquired_utc': _utc(now), 'acquired_cairo': now.astimezone(CAIRO).isoformat(timespec='seconds'),
                     'worktree': canon_path(_git(repo, 'rev-parse', '--show-toplevel')),
                     'argv_sha256': hashlib.sha256(json.dumps(list(argv or [])).encode()).hexdigest()}
            try:
                if os.fstat(f.fileno()).st_size == 0:            # first holder seeds the stable lock byte
                    f.write(b'\0')
                    f.flush()
                prev = _read_json(base + '.owner.json')
                if isinstance(prev, dict) and prev.get('state') == 'held':  # lock was free: that owner is dead
                    _audit(base + '.audit.jsonl', 'recovered', previous=prev, by=owner['pid'])
                _write_owner(base + '.owner.json', owner)
                _audit(base + '.audit.jsonl', 'acquired', pid=owner['pid'], process_start=owner['process_start'],
                       worktree=owner['worktree'])
            except OSError as e:
                raise RunLockError(f'run lock: cannot record the owner of {key[:12]} ({e}); refusing to start') from None
            try:
                yield owner
            finally:
                try:
                    _write_owner(base + '.owner.json', dict(owner, state='released',
                                                            released_utc=_utc(datetime.now(timezone.utc))))
                    _audit(base + '.audit.jsonl', 'released', pid=owner['pid'])
                except OSError as e:
                    raise RunLockError(f'run lock: cannot record the release of {key[:12]} ({e})') from None
        finally:
            _unlock(f)
    finally:
        f.close()
