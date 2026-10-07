"""AUD-00 (audit C18): one ZackBot process per data folder / account.

Before anything touches shared state (session.json, App(), Engine(), Telegram, exchange calls) the app must win BOTH:
  1. the control-panel port, bound EXCLUSIVELY (allow_reuse_address off; Windows: SO_EXCLUSIVEADDRUSE - with the stdlib
     default SO_REUSEADDR a second Windows process binds the same port, so the old check-then-bind guard did not hold);
  2. the data-folder ownership lock <DATA>/app.owner.lock: pid + creation time + a random token, created complete and
     atomically (temp file + hard link, exclusive-create fallback), released only by its own token, reclaimed only from
     an owner that is provably gone (dead pid, or the pid now belongs to a newer process), and never stolen by two
     starters at once (takeover under an exclusive marker that re-reads the lock; Windows contention = "taken").
Pure stdlib; the only Windows-specific calls are ctypes kernel32 (OpenProcess / GetExitCodeProcess / GetProcessTimes)."""
import json, os, socket, uuid
from http.server import ThreadingHTTPServer

LOCK_NAME = 'app.owner.lock'
TAKEOVER_STALE_S = 60


# ------------------------------------------------------------------ exclusive control-panel bind
class ExclusiveServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that can never share its port: no SO_REUSEADDR, and SO_EXCLUSIVEADDRUSE on Windows (also stops
    another program from binding the same address with SO_REUSEADDR after us)."""
    allow_reuse_address = False

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def bind_exclusive(addr, handler):
    """The bound server, or None when the port is taken (by another ZackBot or another program)."""
    try:
        return ExclusiveServer(addr, handler)
    except OSError:
        return None


# ------------------------------------------------------------------ process identity
def _win_proc(pid):
    import ctypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    return ctypes, k, k.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION


def proc_start(pid):
    """Creation time of the process (Windows FILETIME int; Linux start ticks), or None when unknown."""
    try:
        pid = int(pid)
        if pid <= 0: return None
        if os.name == 'nt':
            ctypes, k, h = _win_proc(pid)
            if not h: return None
            try:
                c, e, kt, ut = (ctypes.c_ulonglong() for _ in range(4))
                if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)): return None
                return int(c.value)
            finally:
                k.CloseHandle(h)
        with open(f'/proc/{pid}/stat', encoding='ascii') as f:
            return int(f.read().rsplit(')', 1)[1].split()[19])
    except Exception:
        return None


def pid_alive(pid):
    """True if a process with this pid runs. Windows: OpenProcess + GetExitCodeProcess - never os.kill(pid, 0), which on
    Windows TERMINATES the process. Unsure -> True (keep the lock: refusing to start is safe, two owners are not)."""
    try: pid = int(pid)
    except (TypeError, ValueError): return False
    if pid <= 0: return False
    if os.name == 'nt':
        try:
            ctypes, k, h = _win_proc(pid)
            if not h: return ctypes.get_last_error() == 5             # access denied: it exists
            try:
                code = ctypes.c_ulong()
                if not k.GetExitCodeProcess(h, ctypes.byref(code)): return True
                return code.value == 259                              # STILL_ACTIVE
            finally:
                k.CloseHandle(h)
        except Exception:
            return True
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True
    except OSError: return False


def owner_gone(info, alive=pid_alive, start=proc_start):
    """The recorded owner is provably not running: no/invalid record, dead pid, or the pid was reused by a newer process
    (its creation time differs from the one recorded). This process's own pid never counts as gone."""
    pid = info.get('pid')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0: return True
    if pid == os.getpid(): return False
    if not alive(pid): return True
    rec, now = info.get('start'), start(pid)
    return rec is not None and now is not None and rec != now


# ------------------------------------------------------------------ the ownership lock
def _info(p):
    try:
        with open(p, encoding='utf-8') as f: d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _create(p, data):
    """Create <p> only if absent, with its complete content already in place. True if created."""
    tmp = f'{p}.{uuid.uuid4().hex}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f); f.flush()
            try: os.fsync(f.fileno())
            except OSError: pass
        try:
            os.link(tmp, p); return True
        except (FileExistsError, PermissionError):
            return False
        except OSError:                                           # no hard links on this volume: exclusive create
            try: fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except (FileExistsError, PermissionError): return False
            with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(data, f)
            return True
    finally:
        try: os.remove(tmp)
        except OSError: pass


def acquire(data_dir, alive=pid_alive, start=proc_start, now=None):
    """Own <data_dir>: a token (truthy) if this process now holds the lock, else None (another live process owns it, or
    another starter is taking it over this very moment)."""
    import time
    os.makedirs(data_dir, exist_ok=True)
    p = os.path.join(data_dir, LOCK_NAME)
    token = uuid.uuid4().hex
    mine = dict(pid=os.getpid(), start=start(os.getpid()), token=token, t=(now or time.time)())
    if _create(p, mine): return token
    seen = _info(p)
    if not owner_gone(seen, alive, start): return None
    m = p + '.takeover'
    try:
        os.close(os.open(m, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except PermissionError:                                       # Windows: contended / delete-pending marker
        return None
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(m) > TAKEOVER_STALE_S: os.remove(m)    # its owner died mid-takeover
        except OSError:
            pass
        return None
    try:
        cur = _info(p) if os.path.exists(p) else None
        if cur is not None and (cur != seen or not owner_gone(cur, alive, start)):
            return None                                           # replaced meanwhile -> not ours
        if cur is not None:
            try: os.remove(p)
            except FileNotFoundError: pass
            except OSError: return None
        return token if _create(p, mine) else None
    finally:
        try: os.remove(m)
        except OSError: pass


def release(data_dir, token):
    """Remove the lock only if it is still ours (same token). Never raises."""
    p = os.path.join(data_dir, LOCK_NAME)
    if not token or _info(p).get('token') != token: return False
    try: os.remove(p); return True
    except OSError: return False
