"""`zb-eval-sandbox/1`: the evaluator process of `pit.evaluate` (RES-01 R3; Codex 6079042573 P1).

The evaluator never runs in the harness process. `pit.evaluate` starts this script as a separate Python process
(`-s -S -B -P`: no `site` - so no site-packages, `.pth` files, `sitecustomize` or user site - no bytecode writes,
no script/cwd directory on sys.path) with a fixed, fully controlled environment (`SANDBOX_ENV`: hash seed 0, UTF-8 mode,
TZ=UTC, plus SYSTEMROOT/WINDIR) and an empty working directory.

Materialized tree (Codex 6089091570 P1): the evaluator never runs from, or sees a path of, the original checkout. The
runner copies exactly the hashed static import closure into a fresh temporary tree (repo layout kept, every mtime set
to the fixed `TREE_MTIME`), and that tree is the only repository the child has: no `.git`, no ignored or untracked
file, no unrelated repo metadata exists there. Path probes (`stat`, `lstat`, `access`, `exists`/`isdir`/`isfile`,
`listdir`, `scandir`, `readlink`, final-path lookups) are additionally allowlisted to the tree, the working directory
and the interpreter's own installation (never `site-packages`), so an absolute probe of host state fails closed instead
of answering. The observable environment (hash seed, UTF-8 mode, time zone, locale encoding, environment keys, empty
cwd, tree mtimes) and the canonical `execution_environment` (implementation, version, executable SHA-256, OS,
platform, CPU count; no host path) are reported at start-up and bound into the attestation; the runner requires the
latter to equal the one frozen in `run.code` (Codex 6093381880).
The runner starts TWO such processes per run, each over its own fresh tree, and
refuses any difference (Codex 6089789885). The child is launched path-independently (`-c BOOT`, source compiled as
`SANDBOX_MAIN`; argv / orig_argv / `__main__.__file__` / importer cache scrubbed, Cowork 6094010557), so no checkout
path is evaluator-visible. The child speaks a JSON-lines protocol over its stdin/stdout:

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


PROBE_FUNCS = ('stat', 'lstat', 'access', 'listdir', 'scandir', 'readlink', 'chdir', '_path_exists', '_path_isdir',
               '_path_isfile', '_path_islink', '_path_isjunction', '_path_lexists', '_path_isdevdrive',
               '_getfinalpathname', '_findfirstfile', '_getvolumepathname')


def guard_path_probes(roots) -> None:
    """Replace every path-probing primitive of the OS module (and each alias other modules bound at import time) by an
    allowlist wrapper: a path outside `roots` raises instead of revealing whether it exists."""
    import importlib as _il
    osmod = _il.import_module(os.name)                         # nt / posix
    allowed = [os.path.normcase(os.path.abspath(r)) for r in roots]
    abspath, normcase, sep = os.path.abspath, os.path.normcase, os.sep

    def ok(path):
        if path is None or isinstance(path, int):
            return True
        p = os.fsdecode(os.fspath(path))
        q = normcase(abspath(p))
        if any(d in q.split(sep) for d in THIRD_PARTY_DIRS):
            return False
        return any(q == r or q.startswith(r.rstrip(sep) + sep) for r in allowed)

    originals = {}
    for name in PROBE_FUNCS:
        f = getattr(osmod, name, None)
        if f is None:
            continue

        def wrap(*a, _f=f, _n=name, **kw):
            path = a[0] if a else kw.get('path', '.' if _n in ('listdir', 'scandir') else None)
            if not ok(path):
                raise SandboxRefused(f'sandbox refused path probe {_n}({path!r})')
            return _f(*a, **kw)
        originals[id(f)] = wrap
    mods = [osmod, os, os.path] + [sys.modules[m] for m in ('genericpath', 'ntpath', 'posixpath') if m in sys.modules]
    for m in mods:
        for k, v in list(vars(m).items()):
            if id(v) in originals:
                setattr(m, k, originals[id(v)])


def observed_env() -> dict:
    import locale
    import time
    return {'hash_randomization': sys.flags.hash_randomization, 'utf8_mode': sys.flags.utf8_mode,
            'no_site': sys.flags.no_site, 'tzname': list(time.tzname), 'timezone': time.timezone,
            'locale_encoding': locale.getencoding(), 'env_keys': sorted(os.environ), 'cwd_entries': os.listdir('.')}


EXEC_ENV_KEYS = ('implementation', 'python', 'version', 'hexversion', 'cache_tag', 'executable_sha256', 'os',
                 'platform', 'pointer_bits', 'cpu_count', 'install_digest')
# Codex 6093943713 P1: evaluator-visible interpreter location attributes are virtualized to these path-independent
# sentinels in the sandbox child, after the identity and import roots are captured and before evaluator code loads.
SYS_SENTINELS = {'executable': '<zb-sandbox>/python', '_base_executable': '<zb-sandbox>/python',
                 'prefix': '<zb-sandbox>', 'exec_prefix': '<zb-sandbox>', 'base_prefix': '<zb-sandbox>',
                 'base_exec_prefix': '<zb-sandbox>'}
# Cowork 6094010557: the child is launched with `-c BOOT` and its source compiled under this sentinel name, so no
# checkout path reaches argv / orig_argv / __main__.__file__ / code filenames / the path-importer cache.
SANDBOX_MAIN = '<zb-sandbox>/sandbox.py'
BOOT = ('import sys\nn = int(sys.stdin.buffer.readline())\n'
        f'exec(compile(sys.stdin.buffer.read(n), {SANDBOX_MAIN!r}, "exec"), sys.modules["__main__"].__dict__)\n')


def execution_environment() -> dict:
    """The canonical execution environment (Codex 6093381880 P1): interpreter implementation, version / hexversion /
    cache tag, the executable's SHA-256 (its bytes, never its host path), OS, interpreter platform / architecture and
    CPU count, plus `install_digest` - the SHA-256 of the interpreter's install location (executable, base prefixes),
    which deliberately binds a run to its install path: what is still evaluator-visible of it (the stdlib entries of
    `sys.path`, stdlib module `__file__`) is then a committed input, and a relocated interpreter is a different run
    (Codex 6093943713). ONE function used at freeze time (`report.code_identity` -> frozen `run.code`), inside every
    fresh
    sandbox process (read before the audit hook is installed) and at `make_report` / holdout re-verification; every
    use must equal the frozen value exactly. Evidence is reproducible under the same frozen execution environment,
    not portable across arbitrary hosts. Reads nothing from the process environment variables."""
    import hashlib
    import platform
    import struct
    import sysconfig
    with open(sys.executable, 'rb') as f:
        exe = hashlib.sha256(f.read()).hexdigest()
    where = json.dumps([sys.executable, sys.base_prefix, sys.base_exec_prefix], ensure_ascii=True)
    return {'implementation': sys.implementation.name, 'python': platform.python_version(), 'version': sys.version,
            'hexversion': sys.hexversion, 'cache_tag': sys.implementation.cache_tag, 'executable_sha256': exe,
            'os': sys.platform, 'platform': sysconfig.get_platform(), 'pointer_bits': struct.calcsize('P') * 8,
            'cpu_count': os.cpu_count(), 'install_digest': hashlib.sha256(where.encode('ascii')).hexdigest()}


def virtualize_interpreter_paths() -> None:
    """Replace the evaluator-visible interpreter location attributes with `SYS_SENTINELS` (Codex 6093943713 P1), so
    no encoding of them (codepoints, hashes) can make accepted results depend on where Python is installed."""
    for name, value in SYS_SENTINELS.items():
        if name == 'executable' or hasattr(sys, name):
            setattr(sys, name, value)
    # launch-path state (Cowork 6094010557): canonical argv, no checkout path anywhere, importer cache rebuilt lazily
    sys.argv = [SANDBOX_MAIN]
    sys.orig_argv = [SYS_SENTINELS['executable'], '-s', '-S', '-B', '-P', SANDBOX_MAIN]
    sys.modules['__main__'].__file__ = SANDBOX_MAIN
    sys.path_importer_cache.clear()


def main() -> int:
    chan = _Chan(sys.stdin.buffer, sys.stdout.buffer)
    sys.stdout = sys.stderr                                   # evaluator prints never reach the protocol channel
    init = chan.recv()
    if init.get('op') != 'init' or init.get('protocol') != PROTOCOL:
        chan.send({'error': 'bad init'})
        return 2
    for p in reversed(init['sys_path']):
        sys.path.insert(0, p)
    env = observed_env()
    interp = execution_environment()
    guard_path_probes(init['probe_roots'])
    virtualize_interpreter_paths()                            # after identity + roots, before the evaluator
    sys.addaudithook(make_hook(init['lib_roots'], init['list_roots'], init['allow_files']))
    try:
        spec = importlib.util.spec_from_file_location('zb_evaluator', init['evaluator_path'])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, init['function'])
        summ = getattr(mod, init['summary']) if init.get('summary') else None
    except BaseException as e:                                # noqa: BLE001 - reported to the parent, fail closed
        chan.send({'error': f'evaluator load failed: {type(e).__name__}: {e}'})
        return 2
    chan.send({'ready': True, 'env': env, 'execution_environment': interp})
    while True:
        msg = chan.recv()
        if msg.get('op') == 'exit':
            return 0
        if msg.get('op') not in ('decide', 'summarize') or (msg['op'] == 'summarize' and summ is None):
            chan.send({'error': 'unexpected message'})
            return 2
        try:
            res = fn(View(msg['t'], chan)) if msg['op'] == 'decide' else summ(msg['outputs'])
            json.dumps(res, allow_nan=False)
        except BaseException as e:                            # noqa: BLE001
            chan.send({'error': f'{type(e).__name__}: {e}'})
            continue
        chan.send({'result': res})


if __name__ == '__main__':
    sys.exit(main())
