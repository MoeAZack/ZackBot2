"""A real, portable wall-clock deadline for a child process AND everything it started (P2 on b01d439: the 120 s hard stop
was only asserted after the adapters had returned, so a hung adapter held verify-fast until the 30-minute job timeout).

run(cmd, deadline_s) starts cmd in its own process group / session, waits at most deadline_s and on expiry kills the whole
tree: Windows `taskkill /F /T` (the child and every descendant), POSIX SIGKILL to the session's process group. Stdlib only.
"""
import os, signal, subprocess, sys, time
from dataclasses import dataclass


@dataclass
class Result:
    returncode: object       # None when the deadline killed it
    elapsed: float           # wall seconds from start until the child (tree) was gone
    timed_out: bool
    stdout: str
    stderr: str


def _kill_tree(proc):
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True, timeout=30)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)       # start_new_session=True -> pgid == pid
        except ProcessLookupError:
            pass
    try:
        proc.kill()                                   # belt and braces if the tree kill missed the direct child
    except OSError:
        pass


def run(cmd, deadline_s, cwd=None, env=None):
    kw = dict(cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    if sys.platform == 'win32':
        kw['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, **kw)
    try:
        out, err = proc.communicate(timeout=deadline_s)
        return Result(proc.returncode, time.perf_counter() - t0, False, out, err)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:            # a grandchild still holds the pipes open: do not wait on it
            out, err = '', ''
        return Result(None, time.perf_counter() - t0, True, out or '', err or '')
