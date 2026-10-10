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
of answering (the boolean `exists`/`isdir`/... answer False). Audited probes (`listdir`, `scandir`, `chdir`) are
enforced by the hook itself; every unaudited one, and `_imp.create_builtin` / `_imp.create_dynamic`, is a broker that
raises a `zb.*` audit event, and only the frozen hook holds the raw function, so no Python-reachable object can restore
an original. Modules whose primitives escape the hook without an audit event (`STUBBED_MODULES`: `_posixsubprocess`,
the subinterpreter modules) are refused on direct import and creation; the copies `subprocess` / `concurrent.futures`
need are preloaded and every function in them is a refusing stub that cannot be re-executed away. The C-API test
modules (`NO_HOOK_MODULES`) are refused on every route.
Known limitation (Cowork 6094764379 finding 3, DEFERRED by Codex 6094776448): other unaudited OS primitives (`mkfifo` /
`mknod`, extended attributes, shared memory, `memfd`) are not brokered. This boundary is a CPython runtime control for
honest strategy evaluators, not a hostile-native-code jail; it is reopened only if R4-0 shows result contamination,
cross-run state, holdout leakage or an honest evaluator dependency.
The observable environment (hash seed, UTF-8 mode, time zone, locale encoding, environment keys, empty
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
Policy is not evaluator-mutable (Codex 6094254617): the audit hook closes over immutable snapshots (tuples /
frozensets, captured builtins) and consults no module global at event time; every policy / protocol / factory name is
dropped from this module's globals (reachable as `__main__` and via `View.__globals__`) before the evaluator loads;
gc object walks and frame access (`sys._getframe`, tracebacks' / generators' frames, trace / profile hooks) are refused.
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
# Cowork 6094554597 finding 2: a subinterpreter has no audit hook (hooks are per-interpreter). The C-API test modules
# (which can run one) are refused on every route and dropped if preloaded; `concurrent.interpreters` is refused.
NO_HOOK_MODULES = ('_testcapi', '_testinternalcapi', '_testlimitedcapi')
# Modules whose primitives escape the hook without an audit event: `_posixsubprocess.fork_exec` (Codex 6094447977)
# and the subinterpreter modules (Cowork 6094554597 finding 2, Codex 6094564112). `subprocess` imports
# `_posixsubprocess` on POSIX and `concurrent.futures` imports `_interpreters`, and the research code reaches both
# (finding 1), so those two stdlib modules are preloaded (`PRELOADED`) before the hook is installed. Then every function
# of a stubbed module is replaced, in the module and in every alias (`subprocess._posixsubprocess`,
# `concurrent.futures._interpreters`, ...), by a refusing stub and the module is dropped from `sys.modules`: a direct
# evaluator import is refused by the hook, creating one (`create_builtin` / `create_dynamic`) is refused, and
# re-executing a held one (`exec_builtin` / `exec_dynamic`, i.e. `importlib.reload`) is refused. The Windows process
# equivalent `_winapi` needs nothing: its process primitives raise `_winapi.*` events, which `BLOCKED_EVENTS` refuses.
STUBBED_MODULES = ('_posixsubprocess', '_interpreters', '_xxsubinterpreters', '_interpqueues', '_interpchannels',
                   '_xxinterpchannels')
PRELOADED = ('subprocess', 'concurrent.futures')
BLOCKED_IMPORTS = frozenset({'ctypes', '_ctypes', 'mmap', 'winreg', '_winreg', 'interpreters',
                             'concurrent.interpreters', *NO_HOOK_MODULES, *STUBBED_MODULES})
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
# Codex 6094254617: introspection that could reach the installed hook (gc) or the sandbox loop's frames is refused,
# matched exactly (`sys._getframemodulename` returns a name only and stays allowed).
INTROSPECTION_EVENTS = frozenset({'sys._getframe', 'sys._current_frames', 'sys._current_exceptions', 'sys.settrace',
                                  'sys.setprofile', 'gc.get_objects', 'gc.get_referrers', 'gc.get_referents'})
FRAME_ATTRS = frozenset({'tb_frame', 'gi_frame', 'cr_frame', 'ag_frame'})


class SandboxRefused(PermissionError):
    pass


THIRD_PARTY_DIRS = ('site-packages', 'dist-packages')


def _builtin_norm():
    """A path normalizer built only from builtins captured NOW: at call time it looks up no module global, no
    builtins-module name and no Python-level stdlib helper an evaluator could rebind (Codex 6094254617). It does not
    resolve symlinks (the evaluator cannot create any: links and every file-system mutation are refused)."""
    osmod = __import__(os.name)
    enc, errs = sys.getfilesystemencoding(), sys.getfilesystemencodeerrors()
    type_, str_, bytes_, err = type, str, bytes, TypeError
    if os.name == 'nt':
        full = osmod._getfullpathname

        def norm(p):
            if type_(p) is bytes_:
                p = p.decode(enc, errs)
            if type_(p) is not str_:
                raise err('path must be str or bytes')
            return full(p).replace('/', '\\').lower()
        return norm
    getcwd = osmod.getcwd
    normpath = getattr(osmod, '_path_normpath', None) or os.path.normpath

    def norm(p):
        if type_(p) is bytes_:
            p = p.decode(enc, errs)
        if type_(p) is not str_:
            raise err('path must be str or bytes')
        return normpath(p if p.startswith('/') else getcwd() + '/' + p)
    return norm


def make_hook(lib_roots, list_roots, allow_files, probe_roots):
    """The audit hook. Build it BEFORE `guard_path_probes` (it captures the raw `stat`). It closes over immutable
    snapshots only - tuples / frozensets of the normalized roots and of the policy, the builtins it calls - and never
    consults a module global at event time, so rebinding or clearing the policy names (or the evaluator-visible
    `__main__` that held them) cannot change it (Codex 6094254617)."""
    norm, realpath = _builtin_norm(), os.path.realpath

    def both(r):
        return (norm(r), norm(realpath(r)))
    lib = tuple(sorted({x for r in lib_roots for x in both(r)}))
    listable = tuple(sorted({x for r in list_roots for x in both(r)}))
    files = frozenset(x for f in allow_files for x in both(f))
    third, evidence, sep = tuple(THIRD_PARTY_DIRS), EVIDENCE_DIR, os.sep
    blocked_events, blocked_imports, write_flags = tuple(BLOCKED_EVENTS), frozenset(BLOCKED_IMPORTS), int(WRITE_FLAGS)
    introspection, frame_attrs = frozenset(INTROSPECTION_EVENTS), frozenset(FRAME_ATTRS)
    refused, raw_stat, oserror, exc = SandboxRefused, __import__(os.name).stat, OSError, Exception
    type_, str_, int_, len_ = type, str, int, len
    tuple_, dict_, list_, getattr_ = tuple, dict, list, getattr
    # the ONLY holders of the raw unaudited probes and of `_imp.create_builtin` (see `guard_path_probes`)
    import _imp
    osmod = __import__(os.name)
    probes = tuple((k, getattr(osmod, k)) for k in PROBE_FUNCS if hasattr(osmod, k))
    raw_create, raw_dynamic = _imp.create_builtin, _imp.create_dynamic
    raw_exec = {'builtin': _imp.exec_builtin, 'dynamic': _imp.exec_dynamic}
    for nm in PRELOADED:                           # before the hook: their imports of the stubbed modules must succeed
        __import__(nm)
    held = []                                      # the stubbed modules, loaded now; `guard_path_probes` stubs them
    for nm in STUBBED_MODULES:
        try:
            held.append(__import__(nm))
        except ImportError:
            pass
    held = tuple(held)
    no_dynamic = blocked_imports
    no_create = frozenset({'nt', 'posix', '_imp'}) | no_dynamic
    probe_allowed = tuple(sorted({x for r in probe_roots for x in both(r)}))

    class _Spec:
        __slots__ = ('name', 'origin')

    def under(p, roots):
        for r in roots:
            if p == r or p.startswith(r.rstrip(sep) + sep):
                return True
        return False

    def stdlib(p):
        if not under(p, lib):
            return False
        for d in p.split(sep):
            if d in third:
                return False
        return True

    def isdir(p):
        try:
            return (raw_stat(p).st_mode & 0o170000) == 0o040000
        except oserror:
            return False

    def readable(p, is_dir):
        if p in files or stdlib(p):
            return True
        return is_dir and under(p, listable) and evidence not in p.split(sep)

    def path_of(x):
        try:
            return norm(x)
        except exc:
            raise refused(f'sandbox refused a non-str/bytes path {type_(x).__name__}')

    def hook(event, args):
        n = len_(args)
        if event == 'open':
            path = args[0] if n else None
            mode = args[1] if n > 1 else None
            flags = args[2] if n > 2 else None
            if path is None or type_(path) is int_:
                return
            if mode is not None and (type_(mode) is not str_ or 'w' in mode or 'a' in mode or 'x' in mode
                                     or '+' in mode):
                raise refused(f'sandbox refused write open of {path!r}')
            if flags is not None and (type_(flags) is not int_ or flags & write_flags):
                raise refused(f'sandbox refused write open of {path!r}')
            p = path_of(path)
            if not readable(p, isdir(p)):
                raise refused(f'sandbox refused open of {path!r} (only library and code files are readable)')
        elif event == 'os.listdir' or event == 'os.scandir':
            a0 = args[0] if n else None
            if type_(a0) is int_:
                return
            p = path_of('.' if a0 is None else a0)
            if not readable(p, True):
                raise refused(f'sandbox refused listing {p!r}')
        elif event == 'import':
            if n and type_(args[0]) is str_ and (args[0] in blocked_imports
                                                 or args[0].split('.')[0] in blocked_imports):
                raise refused(f'sandbox refused import of {args[0]}')
        elif event == 'zb.probe':
            if n != 4 or type_(args[0]) is not str_ or type_(args[1]) is not tuple_ or type_(args[2]) is not dict_ \
                    or type_(args[3]) is not list_ or args[3]:
                raise refused('sandbox refused a malformed path probe')
            name, a, kw, box = args[0], args[1], dict_(args[2]), args[3]
            f = None
            for k, v in probes:
                if k == name:
                    f = v
            if f is None or 'dir_fd' in kw:
                raise refused(f'sandbox refused path probe {name}')
            path = a[0] if len_(a) else kw.get('path')
            if path is not None and type_(path) is not int_:      # an fd was opened through the audited open
                p = path_of(path)
                if not under(p, probe_allowed) or [d for d in p.split(sep) if d in third]:
                    raise refused(f'sandbox refused path probe {name}({path!r})')
            box.append(f(*a, **kw))
        elif event == 'zb.create_builtin':
            if n != 2 or type_(args[1]) is not list_ or args[1]:
                raise refused('sandbox refused a malformed create_builtin')
            nm = getattr_(args[0], 'name', None)
            if type_(nm) is not str_ or nm in no_create or nm.rpartition('.')[2] in no_create:
                raise refused(f'sandbox refused creating builtin module {nm!r}')
            spec = _Spec()
            spec.name = nm
            spec.origin = None
            args[1].append(raw_create(spec))
        elif event == 'zb.create_dynamic':
            if n != 2 or type_(args[1]) is not list_ or args[1]:
                raise refused('sandbox refused a malformed create_dynamic')
            nm, origin = getattr_(args[0], 'name', None), getattr_(args[0], 'origin', None)
            if type_(nm) is not str_ or type_(origin) is not str_:
                raise refused('sandbox refused a malformed extension spec')
            stem = origin.replace('\\', '/').rpartition('/')[2].partition('.')[0]
            if nm.partition('.')[0] in no_dynamic or nm.rpartition('.')[2] in no_dynamic or stem in no_dynamic:
                raise refused(f'sandbox refused loading extension module {nm!r}')
            spec = _Spec()                                 # one read of name / origin: no re-read after the check
            spec.name, spec.origin = nm, origin
            args[1].append(raw_dynamic(spec))
        elif event == 'zb.exec_module':
            if n != 3 or args[0] not in raw_exec or type_(args[2]) is not list_ or args[2]:
                raise refused('sandbox refused a malformed module exec')
            mod, nm = args[1], getattr_(args[1], '__name__', None)
            for h in held:
                if mod is h:
                    raise refused(f'sandbox refused re-executing {nm!r}')
            if type_(nm) is not str_ or nm in no_create or nm.rpartition('.')[2] in no_create:
                raise refused(f'sandbox refused executing module {nm!r}')
            args[2].append(raw_exec[args[0]](mod))
        elif event in introspection:
            raise refused(f'sandbox refused {event}')
        elif event == 'object.__getattr__':
            if n > 1 and args[1] in frame_attrs:
                raise refused(f'sandbox refused frame access {args[1]}')
        elif event.startswith(blocked_events):
            raise refused(f'sandbox refused {event}')
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


# Path probes with NO audit event. `listdir` / `scandir` / `chdir` are not here: they raise audit events and the hook
# enforces them, so restoring an original gains nothing.
PROBE_FUNCS = ('stat', 'lstat', 'access', 'readlink', '_path_exists', '_path_isdir', '_path_isfile', '_path_islink',
               '_path_isjunction', '_path_lexists', '_path_isdevdrive', '_getfinalpathname', '_findfirstfile',
               '_getvolumepathname')


def guard_path_probes() -> None:
    """Replace every unaudited path probe of the OS module - and every alias of it in any loaded module or class - by a
    broker that holds NO original (Codex 6094254617 follow-up, owner order): it raises the `zb.probe` audit event and
    the frozen hook, the only holder of the raw functions, checks the path against the probe roots and performs the
    call. `_imp.create_builtin` is brokered the same way (`zb.create_builtin`), since it would otherwise mint a fresh
    `nt` / `posix` module with raw probes. Restoring, re-importing or reloading gains nothing: no Python-reachable
    object holds a raw probe after this. Run after the hook is installed."""
    import _imp
    osmod = __import__(os.name)
    audit, refused, list_ = sys.audit, SandboxRefused, list

    def broker(name):
        predicate = name.startswith('_path_')           # the C exists/isdir/... never raise: outside is just False

        def probe(*a, **kw):
            box = list_()
            try:
                audit('zb.probe', name, a, kw, box)
            except refused:
                if predicate:
                    return False
                raise
            if not box:
                raise refused(f'sandbox path probe {name} is unavailable')
            return box[0]
        probe.__name__ = probe.__qualname__ = name
        return probe

    def create_builtin(spec):
        box = list_()
        audit('zb.create_builtin', spec, box)
        if not box:
            raise refused('sandbox create_builtin is unavailable')
        return box[0]

    def create_dynamic(spec, file=None):
        box = list_()
        audit('zb.create_dynamic', spec, box)
        if not box:
            raise refused('sandbox create_dynamic is unavailable')
        return box[0]

    def exec_broker(kind):
        def exec_module(mod):
            box = list_()
            audit('zb.exec_module', kind, mod, box)
            if not box:
                raise refused('sandbox module exec is unavailable')
            return box[0]
        exec_module.__name__ = exec_module.__qualname__ = f'exec_{kind}'
        return exec_module

    def gone(name):
        def stub(*a, **kw):
            raise refused(f'sandbox refused {name}')
        stub.__name__ = stub.__qualname__ = name.rpartition('.')[2]
        return stub

    repl = {}
    for name in PROBE_FUNCS:
        f = getattr(osmod, name, None)
        if f is not None:
            repl[id(f)] = (f, broker(name))
    repl[id(_imp.create_builtin)] = (_imp.create_builtin, create_builtin)
    repl[id(_imp.create_dynamic)] = (_imp.create_dynamic, create_dynamic)
    repl[id(_imp.exec_builtin)] = (_imp.exec_builtin, exec_broker('builtin'))
    repl[id(_imp.exec_dynamic)] = (_imp.exec_dynamic, exec_broker('dynamic'))
    for name in STUBBED_MODULES + NO_HOOK_MODULES:
        mod = sys.modules.get(name)                 # every STUBBED_MODULES one present was loaded by `make_hook`
        if mod is None:
            continue
        if name in NO_HOOK_MODULES:                 # Cowork 6094554597: aliases of a C-API test module become empty
            repl[id(mod)] = (mod, type(sys)(name))
        for k, v in list(vars(mod).items()):        # Codex 6094447977: the module and every alias get refusing stubs
            if callable(v) and not isinstance(v, type):     # functions only; exception / value types stay
                repl[id(v)] = (v, gone(f'{name}.{k}'))
    for m in list(sys.modules.values()):
        try:
            holders = [m] + [v for v in vars(m).values() if isinstance(v, type)]
        except TypeError:
            continue
        for h in holders:
            for k, v in list(vars(h).items()):
                r = repl.get(id(v))
                if r is not None and r[0] is v:
                    try:
                        setattr(h, k, r[1])
                    except (AttributeError, TypeError):
                        pass
    for name in STUBBED_MODULES + NO_HOOK_MODULES:  # after stubbing: a direct import is refused by the hook
        sys.modules.pop(name, None)


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


AUTHORITY_NAMES = ('BLOCKED_EVENTS', 'BLOCKED_IMPORTS', 'WRITE_FLAGS', 'EVIDENCE_DIR', 'THIRD_PARTY_DIRS',
                   'INTROSPECTION_EVENTS', 'FRAME_ATTRS', 'PROBE_FUNCS', 'STUBBED_MODULES', 'NO_HOOK_MODULES',
                   'PRELOADED', 'PROTOCOL', '_builtin_norm',
                   'make_hook', 'guard_path_probes', 'observed_env', 'execution_environment',
                   'virtualize_interpreter_paths', 'EXEC_ENV_KEYS', 'SYS_SENTINELS', 'SANDBOX_MAIN', 'BOOT', '_Chan',
                   'main', 'AUTHORITY_NAMES')


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
    # the hook captures the raw probes and is installed BEFORE the guard swaps every alias for an audit broker
    sys.addaudithook(make_hook(init['lib_roots'], init['list_roots'], init['allow_files'], init['probe_roots']))
    guard_path_probes()
    virtualize_interpreter_paths()                            # after identity + roots, before the evaluator
    # Codex 6094254617: the evaluator reaches this module's globals (sys.modules['__main__'], View.__globals__);
    # drop every policy / protocol / factory name from them before it loads. The hook keeps its own snapshots.
    g = globals()
    for name in AUTHORITY_NAMES:
        g.pop(name, None)
    del g
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
