"""In-memory FsSeam with a power-loss model, and the FaultFs wrapper (nc02_design.md 10.1 / 10.2).

MemFs keeps, per file, the bytes made durable by the last fsync and the bytes written since; per directory entry,
whether a directory flush made it durable. `crash(model, pending)` returns what a fresh process would find after power
loss:
  model 'ntfs'  : directory entries survive without a directory flush (inferred NTFS behaviour);
  model 'posix' : entries created since the last flush of their directory are lost (with their content);
  pending       : what happened to un-fsynced bytes: 'drop' | 'all' | 'half' | 'one' | 'minus1' | 'zero' (NUL fill).
FaultFs counts every mutating call (mkdir, open_new, open_append, write, fsync, fsync_dir) and can crash before / after
call i (SimulatedCrash is a BaseException: `except Exception` / `except OSError` in store code cannot swallow it) or
raise an injected OSError (ENOSPC, EROFS, EACCES...). Reads can be failed per path as well.
"""
import errno
import os

MUTATING = ('mkdir', 'open_new', 'open_append', 'write', 'fsync', 'fsync_dir')
PENDING = ('drop', 'all', 'half', 'one', 'minus1', 'zero')


class SimulatedCrash(BaseException):
    pass


def oserror(code):
    return OSError(code, os.strerror(code))


class _F:
    __slots__ = ('durable', 'current', 'entry_durable', 'mtime')

    def __init__(self, durable=b'', current=b'', entry_durable=False, mtime=0):
        self.durable, self.current, self.entry_durable, self.mtime = durable, current, entry_durable, mtime


class _H:
    __slots__ = ('path', 'closed')

    def __init__(self, path):
        self.path, self.closed = path, False


class MemFs:
    def __init__(self, *dirs):
        self.files, self.dirs, self.clock = {}, {}, 0
        for d in dirs:
            d = os.path.normpath(d)
            while d not in self.dirs:
                self.dirs[d] = True
                parent = os.path.dirname(d)
                if parent == d:
                    break
                d = parent

    @staticmethod
    def _n(p):
        return os.path.normpath(p)

    def kind(self, p):
        p = self._n(p)
        return 'file' if p in self.files else 'dir' if p in self.dirs else 'missing'

    def _children(self, p):
        return [x for x in list(self.files) + list(self.dirs) if x != p and os.path.dirname(x) == p]

    def listdir(self, p):
        p = self._n(p)
        if p not in self.dirs:
            raise oserror(errno.ENOENT)
        return sorted(os.path.basename(x) for x in self._children(p))

    def read_bytes(self, p):
        p = self._n(p)
        if p in self.dirs:
            raise oserror(errno.EISDIR)
        if p not in self.files:
            raise oserror(errno.ENOENT)
        return self.files[p].current

    def _need_parent(self, p):
        if os.path.dirname(p) not in self.dirs:
            raise oserror(errno.ENOENT)
        if p in self.files or p in self.dirs:
            raise oserror(errno.EEXIST)

    def mkdir(self, p):
        p = self._n(p)
        self._need_parent(p)
        self.dirs[p] = False

    def open_new(self, p):
        p = self._n(p)
        self._need_parent(p)
        self.clock += 1
        self.files[p] = _F(mtime=self.clock)
        return _H(p)

    def open_append(self, p):
        p = self._n(p)
        if p not in self.files:
            raise oserror(errno.ENOENT)
        return _H(p)

    def write(self, h, data):
        f = self.files[h.path]
        self.clock += 1
        f.current, f.mtime = f.current + bytes(data), self.clock

    def fsync(self, h):
        f = self.files[h.path]
        f.durable = f.current

    def fsync_dir(self, p):
        p = self._n(p)
        if p not in self.dirs:
            raise oserror(errno.ENOENT)
        for c in self._children(p):
            if c in self.files:
                self.files[c].entry_durable = True
            else:
                self.dirs[c] = True

    def close(self, h):
        h.closed = True

    def mark(self, label):
        pass

    # ------------------------------------------------------------------------------------------------ test helpers
    def snapshot(self):
        out = {p: ('d',) for p in self.dirs}
        out.update({p: ('f', f.current, f.mtime) for p, f in self.files.items()})
        return out

    def put(self, p, data):
        """Test setup: a durable file with these bytes (directories created durably)."""
        p = self._n(p)
        d = os.path.dirname(p)
        stack = []
        while d not in self.dirs:
            stack.append(d)
            d = os.path.dirname(d)
        for x in reversed(stack):
            self.dirs[x] = True
        self.clock += 1
        self.files[p] = _F(bytes(data), bytes(data), True, self.clock)

    def crash(self, model='ntfs', pending='drop'):
        new = MemFs()
        keep_dirs = {}
        for d, durable in sorted(self.dirs.items(), key=lambda kv: len(kv[0])):
            parent = os.path.dirname(d)
            parent_ok = parent == d or parent in keep_dirs or parent not in self.dirs
            if parent_ok and (durable or model == 'ntfs'):
                keep_dirs[d] = True
        new.dirs = keep_dirs
        for p, f in self.files.items():
            if os.path.dirname(p) not in keep_dirs or not (f.entry_durable or model == 'ntfs'):
                continue
            pend = f.current[len(f.durable):]
            extra = {'drop': b'', 'all': pend, 'half': pend[:len(pend) // 2], 'one': pend[:1],
                     'minus1': pend[:-1] if pend else b'', 'zero': b'\0' * len(pend)}[pending]
            data = f.durable + extra
            new.files[p] = _F(data, data, True, f.mtime)
        new.clock = self.clock
        return new


class FaultFs:
    """Wraps a seam. crash_at=i / when='before'|'after' crashes at the i-th mutating call; fail(op, path, i) may return
    an OSError to raise instead of performing the call; read_fail(op, path) likewise for reads."""

    def __init__(self, inner, *, crash_at=None, when='before', fail=None, read_fail=None):
        self.inner, self.crash_at, self.when, self.fail, self.read_fail = inner, crash_at, when, fail, read_fail
        self.n, self.trace, self.marks = 0, [], []

    def _mut(self, op, path, fn):
        i = self.n
        self.n += 1
        self.trace.append((i, op, os.path.basename(path)))
        if self.crash_at == i and self.when == 'before':
            raise SimulatedCrash(i)
        if self.fail is not None:
            exc = self.fail(op, path, i)
            if exc is not None:
                raise exc
        r = fn()
        if self.crash_at == i and self.when == 'after':
            raise SimulatedCrash(i)
        return r

    def _read(self, op, path, fn):
        if self.read_fail is not None:
            exc = self.read_fail(op, path)
            if exc is not None:
                raise exc
        return fn()

    def kind(self, p):
        return self._read('kind', p, lambda: self.inner.kind(p))

    def listdir(self, p):
        return self._read('listdir', p, lambda: self.inner.listdir(p))

    def read_bytes(self, p):
        return self._read('read_bytes', p, lambda: self.inner.read_bytes(p))

    def mkdir(self, p):
        return self._mut('mkdir', p, lambda: self.inner.mkdir(p))

    def open_new(self, p):
        return self._mut('open_new', p, lambda: self.inner.open_new(p))

    def open_append(self, p):
        return self._mut('open_append', p, lambda: self.inner.open_append(p))

    def write(self, h, data):
        return self._mut('write', h.path, lambda: self.inner.write(h, data))

    def fsync(self, h):
        return self._mut('fsync', h.path, lambda: self.inner.fsync(h))

    def fsync_dir(self, p):
        return self._mut('fsync_dir', p, lambda: self.inner.fsync_dir(p))

    def close(self, h):
        return self.inner.close(h)

    def mark(self, label):
        self.marks.append((self.n, label))
