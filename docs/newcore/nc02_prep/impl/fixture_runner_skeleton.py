"""NC-02 fixture runner - SKELETON (PRE-STAGE, Claude Code, 2026-10-08 Cairo). Does not implement NC-02.

For every frozen fixture NF-01..49 it:
  1. runs repro/verify_fixtures.py (contract + input bytes) and refuses to continue if that fails;
  2. recreates the fixture's data folder in a scratch work dir: exactly the files named in `input_files` (sha256-checked,
     nothing else, so NF-16 starts truly empty), `layout` (NF-33: state.json is a directory) and every input mtime
     (v2: `input_mtimes_ms`; v1: the fixed 1791400000000 ms, because git keeps no mtimes);
  3. turns `inject` into a fault plan on the FsSeam handed to the adapter (NF-13 PermissionError on read, NF-34 ENOSPC,
     NF-35 EROFS on every mutating op in the data folder); unknown inject text is an error, never ignored;
  4. loads the fake exchange from `exchange` (positions, protective orders, `order_lookup` behaviour);
  5. calls the pluggable adapter `recover(data_dir, exchange, env=...)` twice (start + restart) and checks the NC-02
     outcome fields: mode, writes allowed, files-unchanged hash (bytes + mtimes + listing), evidence copied, HOLD kind,
     permitted / forbidden actions, exchange writes, out-of-band incident, and same outcome after the restart.

Two passes (acceptance draft 5.1):
  REJECT   (5.1a, every fixture): the legacy bytes sit where legacy kept them. NEWCORE must REJECT them (A14): bytes and
           mtimes unchanged, nothing derived from them, then INIT per exchange (non-flat -> HOLD-INIT, flat -> MANAGE
           known-empty; store unwritable -> HOLD-INIT that is hard).
  SCENARIO (5.1b): needs a ScenarioBuilder (the NC-02 store's own test writer) that turns `nc02_scenario` / section 5a into
           NEWCORE store bytes. None exists yet, so without one the pass runs OUTCOME-ONLY: the adapter's result is
           checked against the expected outcome, and the file checks are marked vacuous.

Usage (stdlib only; never touches %LOCALAPPDATA%\\ZackBot or ~/.claude):
    python -I fixture_runner_skeleton.py                 # stub adapter, both passes, summary table
    python -I fixture_runner_skeleton.py --self-test     # proves the checker catches a clobbering adapter + the fault seam
    python -I fixture_runner_skeleton.py --adapter pkg.mod:factory --work <dir> [--only NF-08,NF-35] [--json out.json]
"""
from __future__ import annotations

import argparse, dataclasses, errno, fnmatch, hashlib, importlib, json, os, shutil, stat, subprocess, sys, tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

HERE = Path(__file__).resolve().parent
PREP = HERE.parent
FIXTURES = PREP / 'fixtures'
VERIFY = PREP / 'repro' / 'verify_fixtures.py'
V1_MTIME_MS = 1_791_400_000_000
NOW_MS = V1_MTIME_MS + 60_000                       # injected clock for the adapter (integer UTC ms)

# ----------------------------------------------------------------------------------------------------------------------
# Outcome vocabulary (mirrors nc02_design.md section 7; strings so an adapter needs no import from this file)
# ----------------------------------------------------------------------------------------------------------------------
MODES = {'ABORT_RO', 'HOLD', 'HOLD_INIT', 'MANAGE', 'INIT_WAIT'}
HOLD_KINDS = {None, 'soft', 'hard'}
OWNERSHIP = {None, 'unknown', 'known', 'known_empty'}   # None = not applicable (ABORT_RO: nothing was loaded)

# store write classes an adapter reports (and the seam observes)
W_NONE, W_JOURNAL, W_HOLD_SNAPSHOT, W_EVIDENCE, W_INIT, W_SEAL = 'none', 'journal_append', 'hold_snapshot', \
    'evidence_copy', 'init_commit', 'tail_seal'

# actions (permission set of the resulting state)
A = dict(
    QUERY='query', PROTECT='protect_journaled', DRAIN='cancel_entry_drain', ADOPT_PROTECT='adopt_protect_fill',
    RECONCILE='reconcile', OWNER_ITEM='owner_item_action', INCIDENT='incident', ENTER='enter', ADD='add',
    MANAGE_ORDINARY='manage_ordinary', CLOSE_MARKET='close_market', REPRICE='reprice', FALLBACK='fallback',
    PROMOTE='promote', RESUME_CLEARS_HOLD='resume_clears_hold', AUTO_CANCEL_FOREIGN='auto_cancel_foreign',
    AUTO_ADOPT='auto_adopt', CANCEL_PROTECT_UNREPLACED='cancel_protect_unreplaced',
    # hard-HOLD emergency set (A23/A24)
    QUERY_OWNED='query_owned', EMERGENCY_PROTECT='emergency_protect', DRAIN_IDEMPOTENT='cancel_entry_idempotent',
    INCIDENT_EXTERNAL='incident_external', JOURNAL='journal_append',
)
REQ = {    # required subset per (mode, hold_kind)
    ('HOLD', 'soft'): {A['QUERY'], A['PROTECT'], A['DRAIN'], A['ADOPT_PROTECT'], A['RECONCILE'], A['OWNER_ITEM'],
                       A['INCIDENT'], A['JOURNAL']},
    ('HOLD', 'hard'): {A['QUERY_OWNED'], A['EMERGENCY_PROTECT'], A['DRAIN_IDEMPOTENT'], A['ADOPT_PROTECT'],
                       A['INCIDENT_EXTERNAL']},
    ('HOLD_INIT', 'soft'): {A['QUERY'], A['OWNER_ITEM'], A['INCIDENT']},
    ('HOLD_INIT', 'hard'): {A['QUERY_OWNED'], A['INCIDENT_EXTERNAL']},
    ('MANAGE', None): {A['PROTECT'], A['ENTER']},
    ('ABORT_RO', None): set(),
}
NEW_RISK = {A['ENTER'], A['ADD']}
FORBID = {
    ('HOLD', 'soft'): NEW_RISK | {A['RESUME_CLEARS_HOLD'], A['AUTO_CANCEL_FOREIGN'], A['AUTO_ADOPT'],
                                 A['CANCEL_PROTECT_UNREPLACED'], A['REPRICE'], A['FALLBACK']},
    ('HOLD', 'hard'): NEW_RISK | {A['RESUME_CLEARS_HOLD'], A['AUTO_CANCEL_FOREIGN'], A['AUTO_ADOPT'],
                                 A['CANCEL_PROTECT_UNREPLACED'], A['REPRICE'], A['FALLBACK'], A['MANAGE_ORDINARY'],
                                 A['CLOSE_MARKET'], A['JOURNAL'], A['RECONCILE'], A['PROMOTE'], A['PROTECT'],
                                 A['DRAIN']},
    ('HOLD_INIT', 'soft'): NEW_RISK | {A['RESUME_CLEARS_HOLD'], A['AUTO_CANCEL_FOREIGN'], A['AUTO_ADOPT'],
                                      A['MANAGE_ORDINARY'], A['PROMOTE']},
    ('HOLD_INIT', 'hard'): NEW_RISK | {A['RESUME_CLEARS_HOLD'], A['AUTO_CANCEL_FOREIGN'], A['AUTO_ADOPT'],
                                      A['MANAGE_ORDINARY'], A['PROMOTE'], A['JOURNAL'], A['RECONCILE']},
    ('MANAGE', None): set(),
    ('ABORT_RO', None): set(A.values()),          # ABORT_RO permits nothing at all
}


@dataclass(frozen=True)
class EvidenceRef:
    incident_id: str
    sha256: str
    size: int
    rel_path: str
    mtime_ms: int


@dataclass
class RecoveryResult:
    """What an NC-02 adapter returns from recover(). Everything the runner asserts on is here."""
    mode: str                                    # MODES
    hold_kind: str | None = None                 # HOLD_KINDS ('soft' journaled HOLD, 'hard' store unwritable)
    ownership: str | None = None                 # OWNERSHIP; 'unknown' means collections are None, never []
    writes: frozenset = frozenset()              # store write classes performed (W_*)
    evidence: tuple = ()                         # EvidenceRef, one per damaged member copied into the envelope
    permitted_actions: frozenset = frozenset()   # A values
    account_context_created: bool = False        # must be False for ABORT_RO
    rejected_legacy: tuple = ()                  # legacy file names reported (rule 0 / A14)
    candidate: str | None = None                 # 'G-1' | 'trivially_empty' | None
    hold_items: tuple = ()                       # free-form ids, informational
    incidents_out_of_band: tuple = ()            # what the adapter wrote under env.outside_dir/incidents
    notes: str = ''


# ----------------------------------------------------------------------------------------------------------------------
# File-system seam + fault injection
# ----------------------------------------------------------------------------------------------------------------------
class FsSeam(Protocol):
    def lstat(self, p: Path) -> os.stat_result: ...
    def listdir(self, p: Path) -> list[str]: ...
    def read_bytes(self, p: Path) -> bytes: ...
    def open_new(self, p: Path): ...
    def open_append(self, p: Path): ...
    def open_slot(self, p: Path): ...
    def write(self, h, data: bytes, at: int | None = None) -> None: ...
    def truncate(self, h, n: int) -> None: ...
    def fsync(self, h) -> None: ...
    def fsync_dir(self, p: Path) -> None: ...
    def close(self, h) -> None: ...
    def mkdir(self, p: Path) -> None: ...
    def set_private_acl(self, p: Path) -> None: ...
    def unlink_own_partial(self, p: Path) -> None: ...
    def mark(self, label: str) -> None: ...


class RealFs:
    """Minimal real implementation (the NC-02 one adds Windows share modes, winerror mapping, dir flush, DACL)."""
    def lstat(self, p): return os.lstat(p)
    def listdir(self, p): return sorted(os.listdir(p))
    def read_bytes(self, p):
        with open(p, 'rb') as f: return f.read()
    def open_new(self, p): return open(p, 'xb')
    def open_append(self, p): return open(p, 'ab')
    def open_slot(self, p): return open(p, 'r+b')
    def write(self, h, data, at=None):
        if at is not None: h.seek(at)
        h.write(data)
    def truncate(self, h, n): h.truncate(n)
    def fsync(self, h): h.flush(); os.fsync(h.fileno())
    def fsync_dir(self, p):
        if os.name != 'nt':
            fd = os.open(p, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
    def close(self, h): h.close()
    def mkdir(self, p): os.mkdir(p)
    def set_private_acl(self, p): pass
    def unlink_own_partial(self, p): os.unlink(p)
    def mark(self, label): pass


MUTATING = {'open_new', 'open_append', 'open_slot', 'write', 'truncate', 'mkdir', 'set_private_acl',
            'unlink_own_partial'}


@dataclass
class Fault:
    ops: frozenset            # seam method names
    glob: str                 # matched against the path relative to the data dir ('*' = anything inside it)
    exc: Callable[[], BaseException]
    scope: str = 'data'       # only paths inside data_dir are faulted; the outside dir stays writable


class FaultFs:
    """Wraps a seam; records every call; raises the planned faults. Handles remember their path for write/fsync."""
    def __init__(self, inner, data_dir: Path, faults: Iterable[Fault] = ()):
        self.inner, self.data_dir, self.faults = inner, Path(data_dir).resolve(), list(faults)
        self.calls: list[tuple[str, str]] = []
        self.mutations: list[tuple[str, str]] = []
        self._hpath: dict[int, Path] = {}

    def _rel(self, p: Path) -> str | None:
        try: return Path(p).resolve().relative_to(self.data_dir).as_posix()
        except ValueError: return None

    def _check(self, op: str, p: Path):
        rel = self._rel(p)
        self.calls.append((op, rel if rel is not None else f'<outside>{p}'))
        if op in MUTATING: self.mutations.append((op, rel if rel is not None else f'<outside>{p}'))
        if rel is None: return
        for f in self.faults:
            if op in f.ops and (f.glob == '*' or fnmatch.fnmatch(rel, f.glob)): raise f.exc()

    def _h(self, op, p, fn):
        self._check(op, p)
        h = fn(p)
        self._hpath[id(h)] = Path(p)
        return h

    def lstat(self, p): self._check('lstat', p); return self.inner.lstat(p)
    def listdir(self, p): self._check('listdir', p); return self.inner.listdir(p)
    def read_bytes(self, p): self._check('read_bytes', p); return self.inner.read_bytes(p)
    def open_new(self, p): return self._h('open_new', p, self.inner.open_new)
    def open_append(self, p): return self._h('open_append', p, self.inner.open_append)
    def open_slot(self, p): return self._h('open_slot', p, self.inner.open_slot)
    def write(self, h, data, at=None): self._check('write', self._hpath[id(h)]); self.inner.write(h, data, at)
    def truncate(self, h, n): self._check('truncate', self._hpath[id(h)]); self.inner.truncate(h, n)
    def fsync(self, h): self._check('fsync', self._hpath[id(h)]); self.inner.fsync(h)
    def fsync_dir(self, p): self._check('fsync_dir', p); self.inner.fsync_dir(p)
    def close(self, h): self.inner.close(h); self._hpath.pop(id(h), None)
    def mkdir(self, p): self._check('mkdir', p); self.inner.mkdir(p)
    def set_private_acl(self, p): self._check('set_private_acl', p); self.inner.set_private_acl(p)
    def unlink_own_partial(self, p): self._check('unlink_own_partial', p); self.inner.unlink_own_partial(p)
    def mark(self, label): self.calls.append(('mark', label))


def _oserr(code: int, name: str):
    return lambda: OSError(code, f'injected {name} (fixture runner)')


WRITE_OPS = frozenset(MUTATING | {'fsync', 'fsync_dir'})
EROFS = getattr(errno, 'EROFS', 30)
INJECT_PLANS: dict[str, list[Fault]] = {
    # exact fixture text -> plan. Text not in this table is an error (no silent ignore).
    'state.json read raises PermissionError(13) on every attempt':
        [Fault(frozenset({'read_bytes', 'open_slot', 'open_append'}), 'state.json',
               lambda: PermissionError(13, 'injected EACCES (fixture runner)'))],
    'every write, create, rename and replace in the data folder raises OSError(ENOSPC)':
        [Fault(WRITE_OPS, '*', _oserr(errno.ENOSPC, 'ENOSPC'))],
    'every write, create, rename and replace in the data folder raises OSError(EROFS)':
        [Fault(WRITE_OPS, '*', _oserr(EROFS, 'EROFS'))],
}


# ----------------------------------------------------------------------------------------------------------------------
# Fake exchange (read-mostly; every write is recorded so the runner can assert "no exchange write")
# ----------------------------------------------------------------------------------------------------------------------
class FakeExchange:
    def __init__(self, spec: dict, account: str = 'acct-fixture', down: bool = False):
        self.account = account
        self.down = down
        self.positions = [dict(p) for p in spec.get('positions', [])]
        self.protective_orders = [dict(o) for o in spec.get('protective_orders', [])]
        self.open_orders: list[dict] = [dict(o) for o in spec.get('open_orders', [])]   # none in NF-01..49
        self.order_lookup = spec.get('order_lookup')
        self.reads: list[tuple] = []
        self.writes: list[tuple] = []

    @property
    def flat(self) -> bool:
        return not any(abs(float(p['qty'])) > 0 for p in self.positions) and not self.open_orders \
            and not self.protective_orders

    def snapshot(self, now_ms: int) -> dict:
        self.reads.append(('snapshot',))
        if self.down: raise ConnectionError('fake exchange down')
        return {'account': self.account, 'taken_ms': now_ms, 'positions': [dict(p) for p in self.positions],
                'protective_orders': [dict(o) for o in self.protective_orders],
                'open_orders': [dict(o) for o in self.open_orders]}

    def query_order(self, client_id: str) -> dict:
        self.reads.append(('query_order', client_id))
        if self.down: raise ConnectionError('fake exchange down')
        if self.order_lookup and 'bare not-found' in self.order_lookup:
            return {'status': 'NOT_FOUND', 'code': None, 'record': None}      # proves nothing (A19)
        for o in self.protective_orders + self.open_orders:
            if o.get('tag') == client_id or o.get('client_id') == client_id: return {'status': 'NEW', 'record': o}
        return {'status': 'NOT_FOUND', 'code': None, 'record': None}

    # every mutating call is recorded and then refused loudly: no fixture permits an exchange write (see expectations)
    def _write(self, *call):
        self.writes.append(call)
        return {'status': 'RECORDED_BY_FAKE'}
    def place_order(self, **kw): return self._write('place_order', kw)
    def place_stop(self, **kw): return self._write('place_stop', kw)
    def cancel(self, client_id): return self._write('cancel', client_id)


# ----------------------------------------------------------------------------------------------------------------------
# Adapter protocol + environment
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class RecoverEnv:
    fs: Any                          # FsSeam (FaultFs around RealFs)
    now_ms: int                      # injected clock
    outside_dir: Path                # <base> minus data\ : anchors\, incidents\, run\ live here (design section 2)
    binding: dict                    # configured, non-secret binding the account must match
    reader_format: int = 1
    reader_seq: int = 1


class Adapter(Protocol):
    name: str
    def recover(self, data_dir: Path, exchange: FakeExchange, env: RecoverEnv) -> RecoveryResult: ...


class ScenarioBuilder(Protocol):
    """Turns a fixture's NEWCORE scenario into store bytes, using the store's own TEST writer (lands with NC-02).
    Returns the relative paths of the members that must stay byte-identical (damaged / unreadable / future)."""
    def build(self, fixture: dict, data_dir: Path, fs: Any) -> set[str]: ...


class StubAdapter:
    """Does nothing and answers UNKNOWN/HOLD (soft). Exists to smoke-test the runner, not to pass fixtures."""
    name = 'stub-unknown-hold'
    def recover(self, data_dir, exchange, env):
        return RecoveryResult(mode='HOLD', hold_kind='soft', ownership='unknown', writes=frozenset(),
                              evidence=(), permitted_actions=frozenset(REQ[('HOLD', 'soft')]),
                              account_context_created=True, notes='stub')


class ClobberAdapter(StubAdapter):
    """Self-test only: rewrites one legacy file through the seam, which the runner must catch."""
    name = 'self-test-clobber'
    def recover(self, data_dir, exchange, env):
        for n in env.fs.listdir(data_dir):
            p = Path(data_dir) / n
            if stat.S_ISREG(env.fs.lstat(p).st_mode):
                h = env.fs.open_slot(p); env.fs.write(h, b'{}', at=0); env.fs.truncate(h, 2); env.fs.fsync(h)
                env.fs.close(h)
                break
        return super().recover(data_dir, exchange, env)


# ----------------------------------------------------------------------------------------------------------------------
# Expectations
# ----------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Expect:
    mode: str
    hold_kind: str | None | tuple = None      # a tuple means "any of" (open decision)
    ownership: str | None | tuple = None
    evidence: bool | None = None              # None = either (open decision)
    unchanged: str = 'all'                    # 'all' | 'protected' (builder-declared members) | 'legacy'
    writes_max: frozenset = frozenset()       # store write classes allowed
    incident_out_of_band: bool = False
    candidate: str | None | tuple = ('G-1', 'trivially_empty', None)   # default: not asserted
    skip: str | None = None
    source: str = ''


ABORT = Expect('ABORT_RO', None, None, False, 'all', frozenset(), True, source='rule 1 / A01')
HOLD_DAMAGE = dict(mode='HOLD', hold_kind='soft', ownership='unknown', evidence=True, unchanged='protected',
                   writes_max=frozenset({W_JOURNAL, W_HOLD_SNAPSHOT, W_EVIDENCE}))
HOLD_PLAIN = dict(mode='HOLD', hold_kind='soft', evidence=False, unchanged='protected',
                  writes_max=frozenset({W_JOURNAL, W_HOLD_SNAPSHOT}))
HOLD_HARD = dict(mode='HOLD', hold_kind='hard', evidence=False, unchanged='all', writes_max=frozenset(),
                 incident_out_of_band=True)
HOLD_INIT = dict(mode='HOLD_INIT', hold_kind='soft', ownership='unknown', evidence=False, unchanged='legacy',
                 writes_max=frozenset({W_INIT, W_JOURNAL}))
INSTALLER = Expect('ABORT_RO', skip='installer row (section 4: I01-I03), not an NC-02 reader case', source='section 4')

SCENARIO: dict[str, Expect] = {
    **{f: dataclasses.replace(ABORT, source='5a ABORT-RO') for f in
       ('NF-01', 'NF-02', 'NF-03', 'NF-04', 'NF-05', 'NF-06', 'NF-07')},
    'NF-08': Expect(**HOLD_DAMAGE, candidate='G-1', source='5a / M13'),
    'NF-09': Expect(**HOLD_DAMAGE, candidate='G-1', source='5a / M13'),
    'NF-10': Expect(**HOLD_DAMAGE, candidate=None, source='5a / M16-M17'),
    'NF-11': Expect(**HOLD_DAMAGE, candidate='trivially_empty', source='5a / M14'),
    'NF-12': Expect(**HOLD_DAMAGE, source='5a (settings record damaged, design D13)'),
    'NF-13': Expect(**{**HOLD_PLAIN, 'ownership': 'unknown'}, source='5a / M18 (G untouched, unreadable: no copy)'),
    'NF-14': Expect(**{**HOLD_PLAIN, 'ownership': 'unknown'}, source='5a / M19'),
    'NF-15': Expect(**HOLD_PLAIN, ownership=('unknown', 'known'), source='5a / M20 positive control'),
    'NF-16': Expect(**{**HOLD_INIT, 'unchanged': 'all'}, source='5a / M22 (D2: rule-2 HOLD if an anchor survived)'),
    'NF-17': Expect(**HOLD_INIT, source='5a / M26'),
    'NF-18': INSTALLER, 'NF-19': INSTALLER, 'NF-20': INSTALLER,
    'NF-21': Expect(**HOLD_INIT, source='5a: REJECT for NEWCORE; non-flat exchange'),
    **{f: dataclasses.replace(ABORT, source='nc02_outcome ABORT-RO') for f in ('NF-22', 'NF-23', 'NF-24', 'NF-25',
                                                                                 'NF-29')},
    'NF-26': Expect(**HOLD_DAMAGE, candidate=None, source='nc02_outcome / M16'),
    'NF-27': Expect(**HOLD_DAMAGE, candidate=None, source='nc02_outcome / M15-M16 (A17)'),
    'NF-28': Expect(**HOLD_DAMAGE, candidate=None, source='nc02_outcome (A17)'),
    'NF-30': Expect(**HOLD_DAMAGE, source='nc02_outcome / M33'),
    'NF-31': Expect(**HOLD_DAMAGE, source='nc02_outcome / M33'),
    'NF-32': Expect(**HOLD_DAMAGE, source='nc02_outcome / M33'),
    'NF-33': Expect(**{**HOLD_PLAIN, 'hold_kind': ('soft', 'hard'), 'ownership': 'unknown'},
                    source='nc02_outcome / M34 (soft vs hard = decision D6)'),
    'NF-34': Expect(**{**HOLD_HARD, 'ownership': 'unknown'}, source='nc02_outcome / M34, A04 no partial evidence'),
    'NF-35': Expect(**HOLD_HARD, ownership=('known', 'unknown'), source='5b correction: hard HOLD (A23/A24), M45'),
    'NF-36': Expect(**{**HOLD_PLAIN, 'evidence': None}, ownership=('known', 'unknown'),
                    source='nc02_outcome / M35 (orphan kept; copy into envelope = open)'),
    'NF-37': Expect('MANAGE', None, 'known', False, 'protected', frozenset({W_JOURNAL}), source='M35 positive control'),
    'NF-38': Expect(**{**HOLD_PLAIN, 'evidence': True}, ownership='unknown', candidate='G-1',
                    source='nc02_outcome / M36 (evidence linked)'),
    'NF-39': Expect(**HOLD_INIT, source='nc02_outcome / M37'),
    'NF-40': Expect(**HOLD_DAMAGE, source='nc02_outcome / M38 (A18 invalid record = damage)'),
    'NF-41': Expect(**HOLD_DAMAGE, source='nc02_outcome / M38'),
    'NF-42': Expect(**HOLD_DAMAGE, source='nc02_outcome / M38'),
    'NF-43': Expect(**{**HOLD_PLAIN, 'ownership': 'unknown'}, source='nc02_outcome / M39'),
    'NF-44': Expect(**HOLD_PLAIN, ownership=('known', 'unknown'), source='nc02_outcome / M10b, A22'),
    'NF-45': Expect(**HOLD_PLAIN, ownership=('known', 'unknown'), source='nc02_outcome / M10b'),
    'NF-46': Expect(**HOLD_PLAIN, ownership=('known', 'unknown'), source='nc02_outcome / M40'),
    'NF-47': Expect(**HOLD_PLAIN, ownership=('known', 'unknown'), source='nc02_outcome / M41'),
    'NF-48': Expect(**HOLD_PLAIN, ownership=('known', 'unknown'), source='nc02_outcome / M42'),
    'NF-49': Expect(**HOLD_INIT, source='nc02_outcome / M26'),
}


def reject_expect(fx: dict, exchange: FakeExchange) -> Expect:
    """Pass a (5.1a): legacy files only. Never derived from them; then INIT per exchange."""
    hard = fx.get('inject') in (
        'every write, create, rename and replace in the data folder raises OSError(ENOSPC)',
        'every write, create, rename and replace in the data folder raises OSError(EROFS)')
    if exchange.flat:
        if hard:
            return Expect('INIT_WAIT', None, None, False, 'all', frozenset(), True,
                          source='A14 + rule 8, flat exchange, store unwritable: INIT cannot commit')
        return Expect('MANAGE', None, 'known_empty', False, 'legacy', frozenset({W_INIT, W_JOURNAL}),
                      source='A14 + rule 8: flat exchange -> INIT -> MANAGE known-empty')
    if hard:
        return Expect('HOLD_INIT', 'hard', 'unknown', False, 'all', frozenset(), True,
                      source='A14 + rule 8 + A21: non-flat, store unwritable')
    return Expect('HOLD_INIT', 'soft', 'unknown', False, 'legacy', frozenset({W_INIT, W_JOURNAL}),
                  source='A14 + rule 8: non-flat exchange -> HOLD-INIT')


# ----------------------------------------------------------------------------------------------------------------------
# Materialisation and tree snapshots
# ----------------------------------------------------------------------------------------------------------------------
def load_fixtures(only: set[str] | None = None) -> tuple[dict, list[dict]]:
    man = json.loads((FIXTURES / 'MANIFEST.json').read_text(encoding='utf-8'))
    out = []
    for fid in sorted(man['fixtures']):
        if only and fid not in only: continue
        fx = json.loads((FIXTURES / fid / 'fixture.json').read_text(encoding='utf-8'))
        fx['_contract'] = man.get('contract_version', {}).get(fid, 'v1')
        out.append(fx)
    return man, out


def materialize(fx: dict, data_dir: Path) -> None:
    """Exactly the contract's input files (sha256-checked), layout, mtimes. Nothing else (NF-16: truly empty)."""
    data_dir.mkdir(parents=True, exist_ok=False)
    src = FIXTURES / fx['id'] / 'input'
    for name, want in fx['input_files'].items():
        b = (src / name).read_bytes()
        got = hashlib.sha256(b).hexdigest()
        if got != want: raise RuntimeError(f"{fx['id']}: {name} sha256 {got[:12]} != contract {want[:12]}")
        (data_dir / name).write_bytes(b)
    for name, kind in (fx.get('layout') or {}).items():
        if kind != 'directory': raise RuntimeError(f"{fx['id']}: unknown layout kind {kind!r}")
        (data_dir / name).mkdir()
    mt = fx.get('input_mtimes_ms') or {n: V1_MTIME_MS for n in fx['input_files']}
    if set(mt) != set(fx['input_files']): raise RuntimeError(f"{fx['id']}: mtimes do not cover input_files")
    for name, ms in mt.items():
        os.utime(data_dir / name, ns=(ms * 1_000_000, ms * 1_000_000))


def tree(root: Path) -> dict[str, tuple]:
    """rel path -> ('f', sha256, size, mtime_ns) | ('d',) | ('other', mode). Read straight from disk, not via the seam,
    so an adapter that bypasses the seam is still caught."""
    out = {}
    for dp, dns, fns in os.walk(root):
        for n in dns + fns:
            p = Path(dp) / n
            rel = p.relative_to(root).as_posix()
            st = os.lstat(p)
            if stat.S_ISDIR(st.st_mode): out[rel] = ('d',)
            elif stat.S_ISREG(st.st_mode):
                out[rel] = ('f', hashlib.sha256(p.read_bytes()).hexdigest(), st.st_size, st.st_mtime_ns)
            else: out[rel] = ('other', st.st_mode)
    return out


def tree_diff(a: dict, b: dict) -> list[str]:
    d = []
    for k in sorted(set(a) | set(b)):
        if k not in b: d.append(f'GONE {k}')
        elif k not in a: d.append(f'NEW {k}')
        elif a[k] != b[k]: d.append(f'CHANGED {k}')
    return d


# ----------------------------------------------------------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class Check:
    name: str
    ok: bool | None          # None = vacuous / not evaluated
    detail: str = ''


def _in(v, allowed) -> bool:
    return v in allowed if isinstance(allowed, tuple) else v == allowed


def check_result(exp: Expect, r: RecoveryResult, r2: RecoveryResult | None, before: dict | None,
                 after: dict | None, after2: dict | None, protected: set[str] | None, seam: FaultFs | None,
                 exchange: FakeExchange, outside_before: dict | None, outside_after: dict | None) -> list[Check]:
    c: list[Check] = []
    c.append(Check('mode', r.mode == exp.mode, f'got {r.mode} want {exp.mode}'))
    c.append(Check('hold_kind', _in(r.hold_kind, exp.hold_kind), f'got {r.hold_kind} want {exp.hold_kind}'))
    c.append(Check('ownership', _in(r.ownership, exp.ownership), f'got {r.ownership} want {exp.ownership}'))
    c.append(Check('writes_allowed', set(r.writes) <= set(exp.writes_max),
                   f'reported {sorted(r.writes)} allowed {sorted(exp.writes_max)}'))
    if exp.evidence is None: c.append(Check('evidence_copied', None, 'either (open decision)'))
    else: c.append(Check('evidence_copied', bool(r.evidence) == exp.evidence,
                         f'got {len(r.evidence)} refs want {"some" if exp.evidence else "none"}'))
    if exp.mode == 'ABORT_RO':
        c.append(Check('no_account_context', not r.account_context_created, ''))
    if exp.candidate != ('G-1', 'trivially_empty', None):
        c.append(Check('candidate', _in(r.candidate, exp.candidate), f'got {r.candidate} want {exp.candidate}'))
    key = (exp.mode, exp.hold_kind if not isinstance(exp.hold_kind, tuple) else r.hold_kind)
    req, forb = REQ.get(key), FORBID.get(key)
    if req is None: c.append(Check('permitted_actions', None, f'no table for {key}'))
    else:
        missing, bad = req - set(r.permitted_actions), set(r.permitted_actions) & forb
        c.append(Check('permitted_actions', not missing and not bad,
                       f'missing {sorted(missing)} forbidden {sorted(bad)}' if missing or bad else ''))
    c.append(Check('no_exchange_write', not exchange.writes, f'{exchange.writes[:3]}'))
    if exp.incident_out_of_band:
        emitted = bool(r.incidents_out_of_band) or (outside_before is not None and outside_after is not None
                                                    and bool(tree_diff(outside_before, outside_after)))
        c.append(Check('incident_out_of_band', emitted, 'expected an incident outside the data folder'))
    # file checks
    if before is None or after is None:
        c.append(Check('files_unchanged', None, 'vacuous: no ScenarioBuilder (outcome-only)'))
    else:
        if exp.unchanged == 'all': scope = set(before)
        elif exp.unchanged == 'legacy': scope = set(before)          # REJECT pass: every pre-existing path
        else: scope = set(protected or ())
        changed = [k for k in sorted(scope) if before.get(k) != after.get(k)]
        extra = [k for k in tree_diff(before, after) if k.startswith('NEW ')]
        ok = not changed
        if exp.unchanged == 'all': ok = ok and not extra
        if exp.unchanged == 'legacy':        # new files allowed only under the NEWCORE store root
            ok = ok and all(e[4:].startswith('accounts/') or e[4:] == 'accounts' for e in extra)
        c.append(Check('files_unchanged', ok, '; '.join(changed[:4] + extra[:4])))
        if seam is not None and exp.unchanged == 'all':
            muts = [m for m in seam.mutations if not m[1].startswith('<outside>')]
            # attempted writes that the fault plan refused are fine; successful ones would show in the tree diff
            c.append(Check('seam_writes_in_data', True if not muts else None,
                           f'{len(muts)} attempted (refused by fault plan?)' if muts else ''))
    if r2 is None: c.append(Check('restart_same_outcome', None, 'not run'))
    else:
        same = (r2.mode, r2.hold_kind, r2.ownership) == (r.mode, r.hold_kind, r.ownership)
        if after2 is not None and exp.unchanged == 'all' and before is not None:
            same = same and not tree_diff(before, after2)
        c.append(Check('restart_same_outcome', same, f'restart -> {r2.mode}/{r2.hold_kind}/{r2.ownership}'))
    return c


def verdict(checks: list[Check]) -> str:
    if any(x.ok is False for x in checks): return 'FAIL'
    if any(x.ok is None for x in checks): return 'PASS*'      # passes every evaluated check; some vacuous
    return 'PASS'


# ----------------------------------------------------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------------------------------------------------
BINDING = {'venue': 'binance-usdm', 'environment': 'testnet', 'key_digest': 'fixture-digest', 'account': 'acct-fixture'}


def faults_for(fx: dict) -> list[Fault]:
    inj = fx.get('inject')
    if not inj: return []
    if inj not in INJECT_PLANS: raise RuntimeError(f"{fx['id']}: no fault plan for inject text {inj!r}")
    return INJECT_PLANS[inj]


def run_one(fx: dict, adapter, work: Path, builder: ScenarioBuilder | None) -> dict:
    fid = fx['id']
    res: dict[str, Any] = {'id': fid, 'kind': fx.get('kind', 'positive-control' if fid in ('NF-15', 'NF-21')
                                                     else 'negative'), 'contract': fx['_contract']}
    # ---- pass a: REJECT (legacy bytes in place) ----
    base = work / fid / 'reject'
    data, outside = base / 'data', base / 'outside'
    materialize(fx, data); outside.mkdir(parents=True)
    ex = FakeExchange(fx['exchange'])
    exp = reject_expect(fx, ex)
    before, ob = tree(data), tree(outside)
    seam = FaultFs(RealFs(), data, faults_for(fx))
    env = RecoverEnv(seam, NOW_MS, outside, dict(BINDING))
    r = adapter.recover(data, ex, env)
    after, oa = tree(data), tree(outside)
    seam2 = FaultFs(RealFs(), data, faults_for(fx))
    r2 = adapter.recover(data, ex, RecoverEnv(seam2, NOW_MS + 1000, outside, dict(BINDING)))
    after2 = tree(data)
    checks = check_result(exp, r, r2, before, after, after2, None, seam, ex, ob, oa)
    legacy = set(fx['input_files']) | set(fx.get('layout') or {})
    checks.append(Check('legacy_rejected_reported', set(r.rejected_legacy) == legacy,
                        f'reported {sorted(r.rejected_legacy)} want {sorted(legacy)}'))
    res['reject'] = {'expect': exp.mode + (f'/{exp.hold_kind}' if exp.hold_kind else ''), 'verdict': verdict(checks),
                     'checks': [dataclasses.asdict(x) for x in checks]}
    # ---- pass b: SCENARIO ----
    sexp = SCENARIO[fid]
    if sexp.skip:
        res['scenario'] = {'expect': 'SKIP', 'verdict': 'SKIP', 'checks': [], 'why': sexp.skip}
        return res
    base = work / fid / 'scenario'
    data, outside = base / 'data', base / 'outside'
    data.mkdir(parents=True); outside.mkdir(parents=True)
    ex = FakeExchange(fx['exchange'])
    protected = None
    if builder is not None:
        protected = builder.build(fx, data, RealFs())     # the builder owns NEWCORE bytes and mtimes
        before, ob = tree(data), tree(outside)
    else:
        before = ob = None
    seam = FaultFs(RealFs(), data, faults_for(fx))
    r = adapter.recover(data, ex, RecoverEnv(seam, NOW_MS, outside, dict(BINDING)))
    after = tree(data) if builder is not None else None
    oa = tree(outside) if builder is not None else None
    r2 = adapter.recover(data, ex, RecoverEnv(FaultFs(RealFs(), data, faults_for(fx)), NOW_MS + 1000, outside,
                                              dict(BINDING)))
    after2 = tree(data) if builder is not None else None
    checks = check_result(sexp, r, r2, before, after, after2, protected, seam, ex, ob, oa)
    res['scenario'] = {'expect': sexp.mode + (f'/{sexp.hold_kind}' if isinstance(sexp.hold_kind, str) else
                                              (f'/{"|".join(sexp.hold_kind)}' if sexp.hold_kind else '')),
                       'verdict': verdict(checks) if builder else verdict(checks).replace('PASS*', 'OUTCOME-OK')
                       .replace('FAIL', 'OUTCOME-FAIL'),
                       'mode': 'full' if builder else 'outcome-only', 'source': sexp.source,
                       'checks': [dataclasses.asdict(x) for x in checks]}
    return res


def verify_pack() -> None:
    p = subprocess.run([sys.executable, '-I', str(VERIFY), str(FIXTURES)], capture_output=True, text=True)
    print(p.stdout.strip() or p.stderr.strip())
    if p.returncode != 0: raise SystemExit('verify_fixtures.py failed: refusing to run on a changed fixture pack')


def load_adapter(spec: str | None):
    if not spec: return StubAdapter()
    mod, _, fn = spec.partition(':')
    return getattr(importlib.import_module(mod), fn or 'make_adapter')()


def summarize(results: list[dict]) -> str:
    lines = [f"{'fixture':8} {'kind':17} {'REJECT expect':16} {'REJECT':7} {'SCENARIO expect':20} {'SCENARIO':13} "
             f"failing checks (R: reject pass, S: scenario pass)"]
    for x in results:
        f = {p: sorted({c['name'] for c in x[p]['checks'] if c['ok'] is False}) for p in ('reject', 'scenario')}
        lines.append(f"{x['id']:8} {x['kind']:17} {x['reject']['expect']:16} {x['reject']['verdict']:7} "
                     f"{x['scenario']['expect']:20} {x['scenario']['verdict']:13} "
                     f"R:{','.join(f['reject']) or '-'}  S:{','.join(f['scenario']) or '-'}")
    intact = [x['id'] for x in results
              if all(c['ok'] is True for c in x['reject']['checks'] if c['name'] in ('files_unchanged',
                                                                                     'restart_same_outcome'))]
    lines.append(f'\nREJECT pass, legacy bytes + mtimes + listing unchanged across start and restart: '
                 f'{len(intact)}/{len(results)}')
    return '\n'.join(lines)


def self_test(work: Path) -> int:
    """The checker must (1) see a clobbered legacy file, (2) apply each inject plan, (3) refuse unknown inject text."""
    _, fxs = load_fixtures({'NF-01', 'NF-13', 'NF-34', 'NF-35', 'NF-33', 'NF-16'})
    by = {f['id']: f for f in fxs}
    ok = True
    r = run_one(by['NF-01'], ClobberAdapter(), work / 'selftest', None)
    fu = [c for c in r['reject']['checks'] if c['name'] == 'files_unchanged'][0]
    print(f"self-test clobber caught: {fu['ok'] is False} ({fu['detail']})"); ok &= fu['ok'] is False
    for fid, op, exc in (('NF-13', 'read_bytes', PermissionError), ('NF-34', 'open_new', OSError),
                         ('NF-35', 'open_append', OSError)):
        d = work / 'selftest' / f'{fid}-seam'; materialize(by[fid], d)
        s = FaultFs(RealFs(), d, faults_for(by[fid]))
        target = d / ('state.json' if op == 'read_bytes' else 'new.file')
        try:
            getattr(s, op)(target); hit = False
        except exc as e:
            hit = True; print(f'self-test {fid} {op} -> {type(e).__name__}({e.errno})')
        ok &= hit
        if fid != 'NF-13':     # reads stay allowed under ENOSPC/EROFS
            s.read_bytes(d / 'state.json')
    d = work / 'selftest' / 'NF-33-layout'; materialize(by['NF-33'], d)
    print(f"self-test NF-33 state.json is a directory: {os.path.isdir(d / 'state.json')}"); ok &= os.path.isdir(d / 'state.json')
    d = work / 'selftest' / 'NF-16-empty'; materialize(by['NF-16'], d)
    print(f"self-test NF-16 starts empty: {os.listdir(d) == []}"); ok &= os.listdir(d) == []
    try:
        faults_for({'id': 'X', 'inject': 'something new'}); ok = False
    except RuntimeError:
        print('self-test unknown inject text refused: True')
    mt = {n: os.stat(Path(work / 'selftest' / 'NF-35-seam') / n).st_mtime_ns // 1_000_000
          for n in by['NF-35']['input_files']}
    print(f"self-test mtimes set from input_mtimes_ms: {mt == by['NF-35']['input_mtimes_ms']}")
    ok &= mt == by['NF-35']['input_mtimes_ms']
    print('SELF-TEST', 'OK' if ok else 'FAILED')
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--adapter'); ap.add_argument('--work'); ap.add_argument('--only'); ap.add_argument('--json')
    ap.add_argument('--self-test', action='store_true'); ap.add_argument('--keep', action='store_true')
    a = ap.parse_args(argv)
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix='nc02_fx_'))
    for bad in (Path.home() / '.claude', Path(os.environ.get('LOCALAPPDATA', '/nonexistent')) / 'ZackBot'):
        try:
            work.resolve().relative_to(bad.resolve()); raise SystemExit(f'refusing work dir under {bad}')
        except ValueError: pass
    try:
        verify_pack()
        if a.self_test: return self_test(work)
        _, fxs = load_fixtures(set(a.only.split(',')) if a.only else None)
        adapter = load_adapter(a.adapter)
        results = [run_one(fx, adapter, work, None) for fx in fxs]
        print(f'adapter: {adapter.name}   work: {work}')
        print(summarize(results))
        sat = [x['id'] for x in results if x['scenario']['verdict'] in ('PASS', 'OUTCOME-OK')]
        rej = [x['id'] for x in results if x['reject']['verdict'] in ('PASS', 'PASS*')]
        print(f'\nSCENARIO satisfied (outcome-only): {len(sat)}: {", ".join(sat) or "-"}')
        print(f'REJECT satisfied: {len(rej)}: {", ".join(rej) or "-"}')
        if a.json: Path(a.json).write_text(json.dumps(results, indent=1), encoding='utf-8')
        return 0
    finally:
        if not a.keep and not a.work: shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
