"""`zb-eval-sandbox/1`: the evaluator process of `pit.evaluate` (RES-01 R3; Codex 6079042573 P1).

The evaluator never runs in the harness process. `pit.evaluate` starts this script as a separate Python process
(`-I -S -B`: no environment, no `site` - so no site-packages, `.pth` files, `sitecustomize` or user site - and no
bytecode writes; empty working directory, minimal environment) and speaks a JSON-lines protocol over its stdin/stdout:

  parent -> child  {"op": "init", ...}            evaluator file + function, the read policy
  child  -> parent {"ready": true}
  parent -> child  {"op": "decide", "t": T}       one decision
  child  -> parent {"op": "call", "name": ..., "args": [...], "kw": {...}}   a View request (answered by the parent
                                                   from its own View frozen at T; the child cannot name another time)
  child  -> parent {"result": ...} | {"error": "..."}
  parent -> child  {"op": "exit"}

The child holds no Dataset, Window, manifest, store path or ledger: the only data it can obtain is what the parent's
View returns. Before the evaluator is imported, an audit hook (`sys.addaudithook`, which cannot be removed) refuses:
  * opening any file except (a) the Python standard library (the interpreter's `Lib` / `DLLs`, never a
    `site-packages` / `dist-packages` directory: third-party distributions are not pinned, `RESEARCH_DEPS` is empty)
    and (b) exactly the evaluator's static import closure (`report.closure_abs`), which the runner hashes into the
    attestation; any other repository file - an ignored or untracked helper, a module reached only by a dynamic
    import / `importlib` / `__import__` / `runpy`, a data file read to be `exec`'d, any `.pyc` in the repository -
    is refused, and every write mode is refused;
  * listing any directory outside the stdlib and the code search roots (listing reveals names, never bytes);
  * process creation, sockets, ctypes, mmap, Windows API file/process calls, file-system mutation.
This is a runtime control that turns a bypass into a hard failure; it is not claimed to be a security boundary against
hostile native code. The authority that makes a result sealable is the runner's attestation bound into the ledger
(`pit.evaluate` -> `report.make_report`), never a private Python name.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections import namedtuple

PROTOCOL = 'zb-eval-sandbox/1'
Bar = namedtuple('Bar', 'open_ms open high low close volume quote_volume available_ms')
Funding = namedtuple('Funding', 'time_ms rate interval_hours available_ms')
EVIDENCE_DIR = 'research_evidence'
BLOCKED_EVENTS = ('subprocess.', 'os.system', 'os.exec', 'os.spawn', 'os.posix_spawn', 'os.fork', 'os.startfile',
                  'os.kill', 'os.remove', 'os.rename', 'os.rmdir', 'os.mkdir', 'os.chdir', 'os.chmod', 'os.link',
                  'os.symlink', 'os.truncate', 'os.utime', 'os.putenv', 'os.unsetenv', 'os.add_dll_directory',
                  'shutil.', 'socket.', 'ctypes.', '_winapi.', 'winreg.', 'mmap.', 'msvcrt.', 'webbrowser.',
                  'urllib.', 'http.', 'ftplib.', 'smtplib.', 'sqlite3.', 'tempfile.', 'pty.', 'resource.')
BLOCKED_IMPORTS = {'ctypes', '_ctypes', 'mmap', 'winreg', '_winreg'}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


class SandboxRefused(PermissionError):
    pass


def _norm(p) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(p)))


THIRD_PARTY_DIRS = ('site-packages', 'dist-packages')


def make_hook(lib_roots, list_roots, allow_files):
    lib = [_norm(r) for r in lib_roots]
    listable = [_norm(r) for r in list_roots]
    files = {_norm(f) for f in allow_files}

    def under(p, roots):
        return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

    def stdlib(p):
        return under(p, lib) and not any(d in p.split(os.sep) for d in THIRD_PARTY_DIRS)

    def readable(p, is_dir):
        if p in files or stdlib(p):
            return True
        return is_dir and under(p, listable) and EVIDENCE_DIR not in p.split(os.sep)

    def hook(event, args):
        if event == 'open':
            path, mode, flags = (list(args) + [None, None, None])[:3]
            if path is None or isinstance(path, int):
                return
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
                    isinstance(flags, int) and flags & WRITE_FLAGS):
                raise SandboxRefused(f'sandbox refused write open of {path!r}')
            p = _norm(path)
            if not readable(p, os.path.isdir(p)):
                raise SandboxRefused(f'sandbox refused open of {path!r} (only library and code files are readable)')
        elif event in ('os.listdir', 'os.scandir'):
            p = _norm(args[0] if args and args[0] is not None else '.')
            if not readable(p, True):
                raise SandboxRefused(f'sandbox refused listing {p!r}')
        elif event == 'import':
            if args and isinstance(args[0], str) and args[0].split('.')[0] in BLOCKED_IMPORTS:
                raise SandboxRefused(f'sandbox refused import of {args[0]}')
        elif event.startswith(BLOCKED_EVENTS):
            raise SandboxRefused(f'sandbox refused {event}')
    return hook


class _Chan:
    def __init__(self, inp, out):
        self.inp, self.out = inp, out

    def send(self, obj):
        self.out.write(json.dumps(obj, sort_keys=True, allow_nan=False).encode('utf-8') + b'\n')
        self.out.flush()

    def recv(self):
        line = self.inp.readline()
        if not line:
            raise SystemExit(0)
        return json.loads(line)


class SandboxError(RuntimeError):
    pass


class Position:
    """A handle on a position capability held by the parent (issued by `View.enter`)."""
    __slots__ = ('symbol', 'entry_ms', 'decision_id', '_id')

    def __init__(self, d):
        for k in ('symbol', 'entry_ms', 'decision_id'):
            object.__setattr__(self, k, d[k])
        object.__setattr__(self, '_id', d['__position__'])

    def __setattr__(self, k, v):
        raise AttributeError('a position capability is immutable')

    def __eq__(self, other):
        return isinstance(other, Position) and (self.symbol, self.entry_ms, self.decision_id) == (
            other.symbol, other.entry_ms, other.decision_id)

    def __hash__(self):
        return hash((self.symbol, self.entry_ms, self.decision_id))


def _enc(v):
    if isinstance(v, Position):
        return {'__position__': v._id}
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    return v


class View:
    """The evaluator's only capability: a decision time and a channel to the parent's View frozen at that time."""
    __slots__ = ('t', '_chan')

    def __init__(self, t, chan):
        object.__setattr__(self, 't', t)
        object.__setattr__(self, '_chan', chan)

    def __setattr__(self, k, v):
        raise AttributeError('a view is frozen at its decision time')

    def _call(self, name, *args, **kw):
        self._chan.send({'op': 'call', 'name': name, 'args': _enc(list(args)), 'kw': _enc(kw)})
        r = self._chan.recv()
        if 'error' in r:
            raise SandboxError(r['error'])
        return r['ok']

    @property
    def labels(self):
        return tuple(self._call('labels'))

    def members(self):
        return frozenset(self._call('members'))

    def enter(self, symbol, decision_id):
        return Position(self._call('enter', symbol, decision_id))

    def bars(self, symbol, interval, n, *, position=None, series='klines'):
        return tuple(Bar(*r) for r in self._call('bars', symbol, interval, n, position=position, series=series))

    def mark_bars(self, symbol, n, *, position=None):
        return tuple(Bar(*r) for r in self._call('mark_bars', symbol, n, position=position))

    def funding(self, symbol, since_ms, *, position=None):
        return tuple(Funding(*r) for r in self._call('funding', symbol, since_ms, position=position))

    def funding_events(self, position, exit_ms):
        return [tuple(e) for e in self._call('funding_events', position, exit_ms)]

    def minute_bars(self, symbol, open_ms, close_ms, *, position=None):
        return tuple(Bar(*r) for r in self._call('minute_bars', symbol, open_ms, close_ms, position=position))


def main() -> int:
    chan = _Chan(sys.stdin.buffer, sys.stdout.buffer)
    sys.stdout = sys.stderr                                   # evaluator prints never reach the protocol channel
    init = chan.recv()
    if init.get('op') != 'init' or init.get('protocol') != PROTOCOL:
        chan.send({'error': 'bad init'})
        return 2
    for p in reversed(init['sys_path']):
        sys.path.insert(0, p)
    sys.addaudithook(make_hook(init['lib_roots'], init['list_roots'], init['allow_files']))
    try:
        spec = importlib.util.spec_from_file_location('zb_evaluator', init['evaluator_path'])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, init['function'])
    except BaseException as e:                                # noqa: BLE001 - reported to the parent, fail closed
        chan.send({'error': f'evaluator load failed: {type(e).__name__}: {e}'})
        return 2
    chan.send({'ready': True})
    while True:
        msg = chan.recv()
        if msg.get('op') == 'exit':
            return 0
        if msg.get('op') != 'decide':
            chan.send({'error': 'unexpected message'})
            return 2
        try:
            res = fn(View(msg['t'], chan))
            json.dumps(res, allow_nan=False)
        except BaseException as e:                            # noqa: BLE001
            chan.send({'error': f'{type(e).__name__}: {e}'})
            continue
        chan.send({'result': res})


if __name__ == '__main__':
    sys.exit(main())
