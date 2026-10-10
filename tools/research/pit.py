"""Point-in-time data view + holdout guard (RES-01 R3; plan sections 1, 1a, 3, 4; the plan's `data.py`).

Layers (each the only way to reach the next):
  Dataset  one verified `zb-data-manifest/1` + its `zb-pit-universe/2`, restricted to the named asset-class `books`
           (never a mixed list; gold is served from the `gold-commodity` book). Every file is read only through the
           manifest: its bytes are re-hashed and must equal the manifest SHA-256 (and, for archives, the published
           checksum) before a row is parsed - a mismatch fails closed. Rows carry `available_ms` (klines / mark
           klines: open + interval; funding: `calc_time`). Harness-side only.
  Access   opens one split of an immutable `splits.SplitPlan` (or a development window) for one hypothesis family and
           appends a `data_access` record to the family ledger BEFORE returning any data. The sealed holdout opens only
           when (a) the ledger is the canonical registered one - `<CANONICAL_REPO>/research_evidence/ledger`, its
           runs store `<CANONICAL_REPO>/research_evidence/runs`, the access repo IS that checkout, and `ledger.verify`
           (records, registry genesis pin, append-only Git history) passes at access (Codex 6079042573 P1: a scratch
           ledger + runs tree can never open the holdout); (b) the frozen run envelope (`report.py`) is the stored
           one; (c) the executing checkout re-proves the envelope's code identity (HEAD, clean tree, Python,
           dependency set, recomputed `eval_digest`; `report.verify_code`); and (d) the ledger's last open group is
           the atomic `holdout_reveal` / `holdout_rerun` of exactly this run, with every component carrying its
           complete identity. A development window may not touch the plan's holdout. Harness-side only.
  Window   the data range of the opened split: rows with `open_ms >= lo` and `available_ms <= hi`. Harness-side only;
           it is never handed to evaluator code.
  View     `window.view(t)`: a harness-side accessor frozen at `t`; every accessor filters `available_ms <= t` and
           there is no accessor taking another time. Symbols are served only while they are members of the dataset's
           books at `t`, or under a `Position` capability that `View.enter()` issued (membership checked and the
           decision recorded at entry). Its private fields are NOT a security boundary (Codex 6079042573 P1): in the
           harness process a View can reach the Window, so evaluator code never runs in this process.
  evaluate `evaluate(window, evaluator_path, function, times)` is the only evaluation runner and the only authority
           that can produce a sealable result. The evaluator runs in a separate sandboxed process (`sandbox.py`) that
           holds no Dataset, Window, manifest, store path or ledger; its View is a proxy whose every request is
           answered here from the View frozen at the decision time, and an audit hook refuses direct file reads
           (store, manifest, research_evidence), process creation, sockets and ctypes. Each decision is then re-run
           with every cached row not yet available at `t` perturbed; any difference raises. The runner writes an
           attestation (`zb-eval-attestation/1`: runner, isolation, perturbation, window, run_digest, evaluator file
           hash, digests of the times / outputs / recorded decisions) into the family ledger as a `data_access`
           record, and `report.make_report` refuses results without a matching perturbed, isolated attestation in
           the ledger. Results from `Window.view` directly or from `perturb=False` therefore cannot be sealed.
A survivor-only legacy manifest (no universe) is served with the label SURVIVOR-ONLY.

Funding joins: `funding_events` returns (time, rate, mark) for entry <= time < exit at the ACTUAL funding timestamps
(no cadence is assumed; XAUUSDT's observed 4h schedule is just what its rows say). The mark is the funding-mark proxy
v1 (`FUNDING_MARK_PROXY`): the close of the 1h mark bar ending at or before the funding time, never forward-filled
across a gap > 1 bar.

Smoke (shape/coverage counts only, never returns; logged in the ledger as a development access):
  python tools/research/pit.py smoke --manifest research_evidence/manifests/binance-um-archive-v1.json.gz
      --store C:/Dev/ZackBot2_data/binance_um --universe research_evidence/universe/pit-top40-qv30d-v4.json
      --books crypto,gold-commodity --ledger research_evidence/ledger/res01_infra.jsonl
      --start 2026-01-05T00:00:00Z --end 2026-01-12T00:00:00Z --symbols BTCUSDT,XAUUSDT
      --author claude-code --cairo-date 2026-10-09
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import weakref
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import costs as C                                                                           # noqa: E402
import report as R                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import universe as U                                                                        # noqa: E402

Bar = namedtuple('Bar', 'open_ms open high low close volume quote_volume available_ms')
Funding = namedtuple('Funding', 'time_ms rate interval_hours available_ms')
HOUR = 3_600_000
SURVIVOR_ONLY = 'SURVIVOR-ONLY'
GROUP_KINDS = ('holdout_reveal', 'holdout_rerun')
FUNDING_MARK_PROXY = 'funding-mark-proxy-v1: close of the 1h mark bar ending at or before T (causal proxy, PROVISIONAL)'
ATTESTATION_FORMAT = 'zb-eval-attestation/1'
RUNNER_ID = 'pit.evaluate/v5'
ISOLATION_ID = ('subprocess-sSBP+materialized-closure-tree+probe-allowlist+audit-hook+two-fresh-process-replay'
                '/zb-eval-sandbox/4')
SANDBOX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox.py')
CANONICAL_REPO = M.REPO                          # the checkout whose registered ledger may open a sealed holdout
LEDGER_REL = ('research_evidence', 'ledger')
RUNS_REL = ('research_evidence', 'runs')
VIEW_CALLS = ('labels', 'members', 'enter', 'bars', 'mark_bars', 'funding', 'funding_events', 'minute_bars')


class PITError(ValueError):
    pass


class OverlapError(PITError):
    """Two manifest files of one (series, symbol, interval) declare overlapping or duplicate time coverage."""


def _refuse_overlap(key: tuple, files: list) -> None:
    """Fail closed when any two files of one key cover a common open time. Coverage is the manifest's declared
    [first_open_ms, last_open_ms] (`M.validate` requires it for every non-empty file and `Dataset.rows` re-checks it
    against the hashed bytes); an empty file covers nothing. Exactly adjacent files are fine. There is deliberately no
    source preference here: which source serves a key is chosen upstream, in the manifest / universe artifact."""
    seen = sorted((f for f in files if f['first_open_ms'] is not None),
                  key=lambda f: (f['first_open_ms'], f['last_open_ms'], f['path']))
    reach = None                                       # the file reaching furthest so far
    for f in seen:
        if reach is not None and f['first_open_ms'] <= reach['last_open_ms']:
            series, symbol, interval = key
            lo, hi = f['first_open_ms'], min(f['last_open_ms'], reach['last_open_ms'])
            raise OverlapError(f'{series} {symbol} {interval}: manifest files {reach["path"]} and {f["path"]} overlap '
                               f'on open times {S.utc(lo)} .. {S.utc(hi)}; refusing to serve duplicated coverage '
                               f'(choose one source upstream in the manifest/universe artifact)')
        if reach is None or f['last_open_ms'] > reach['last_open_ms']:
            reach = f


class Dataset:
    def __init__(self, manifest: dict, store: str, universe: dict | None, books=None):
        M.validate(manifest)
        self.manifest, self.store, self.digest = manifest, store, manifest['digest']
        self.archive = manifest['loader_version'] == M.ARCHIVE_LOADER
        if universe is None:
            if not manifest['survivor_only']:
                raise PITError('an all-listed manifest is served only through its PIT universe')
            if books is not None:
                raise PITError('books need a PIT universe')
            self.universe_digest, self.labels, self.books = None, (SURVIVOR_ONLY,), ()
            self._mondays, self._members = None, None
            self._all = frozenset(f['symbol'] for f in manifest['files'])
        else:
            if universe.get('format') != U.FORMAT or universe.get('digest') != U.digest_of(universe):
                raise PITError('universe digest does not match its content')
            if universe['manifest_digest'] != self.digest:
                raise PITError('universe was built from a different manifest')
            if not books or any(b not in universe['books'] for b in books):
                raise PITError(f'name the asset-class books to serve, from {universe["books"]} (no mixed default)')
            self.universe_digest, self.labels, self.books = universe['digest'], (), tuple(sorted(set(books)))
            self._mondays = [w['monday_ms'] for w in universe['weeks']]
            self._members = [frozenset(s for b in self.books for s in U.members(universe, w, b))
                             for w in universe['weeks']]
            self.symbol_class = {s['symbol']: (s['class'], s['subclass']) for s in universe['symbols']}
        self._files: dict[tuple, list] = {}
        for f in manifest['files']:
            key = (f.get('series', 'klines'), f['symbol'], f['interval'])
            self._files.setdefault(key, []).append(f)
        for key, v in self._files.items():
            v.sort(key=lambda f: (f['first_open_ms'] is None, f['first_open_ms'] or 0))
            _refuse_overlap(key, v)
        self._cache: dict[str, list] = {}
        self._keys: dict[str, tuple] = {}

    def members(self, at: int) -> frozenset:
        if self._mondays is None:
            return self._all
        i = bisect.bisect_right(self._mondays, at) - 1
        if i < 0:
            return frozenset()
        if at >= self._mondays[-1] + U.WEEK:
            raise PITError(f'{S.utc(at)} lies beyond the last universe week')
        return self._members[i]

    def files(self, series: str, symbol: str, interval) -> list:
        return self._files.get((series, symbol, interval), [])

    def rows(self, f: dict) -> list:
        """Parsed rows of one manifest file, after the SHA-256 re-check (fail closed). Harness-side only."""
        p = f['path']
        if p in self._cache:
            return self._cache[p]
        ap = M.contained(self.store, p)
        if self.archive:
            _, published, data = M.read_archive(ap, p)
            if published != f['sha256']:
                raise PITError(f'{p}: bytes no longer match the manifest sha256')
            raw = M.parse_archive_csv(p, data, M.archive_meta(p))
            if f['series'] == 'fundingRate':
                out = [Funding(int(r[0]), float(r[2]), int(r[1]), int(r[0])) for r in raw]
            else:
                iv = M.INTERVALS[f['interval']]
                out = [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[7]),
                           int(r[0]) + iv) for r in raw]
        else:
            with open(ap, 'rb') as fh:
                data = fh.read()
            if hashlib.sha256(data).hexdigest() != f['sha256']:
                raise PITError(f'{p}: bytes no longer match the manifest sha256')
            iv = M.INTERVALS[f['interval']]
            out = []
            for ln in data.decode('utf-8').splitlines()[1:]:
                if ln.strip():
                    t, o, h, lo, c, v = ln.split(',')
                    om = M.parse_open_ms(t)
                    out.append(Bar(om, float(o), float(h), float(lo), float(c), float(v), None, om + iv))
        if len(out) != f['rows']:
            raise PITError(f'{p}: {len(out)} rows != manifest {f["rows"]}')
        if out and (out[0][0] != f['first_open_ms'] or out[-1][0] != f['last_open_ms']):
            raise PITError(f'{p}: bars span {out[0][0]}..{out[-1][0]} != manifest coverage '
                           f'{f["first_open_ms"]}..{f["last_open_ms"]} (the overlap check relies on it)')
        self._cache[p] = out
        self._keys[p] = ([r[0] for r in out], [r.available_ms for r in out])
        return out

    def slice(self, f: dict, lo: int, t: int) -> list:
        """Rows of one file with time >= lo and available_ms <= t (both sorted, so two bisections)."""
        rows = self.rows(f)
        times, avail = self._keys[f['path']]
        return rows[bisect.bisect_left(times, lo):bisect.bisect_right(avail, t)]


_TOKEN = object()


class _Cap:
    """Opaque capability key a View holds instead of a Window reference."""
    __slots__ = ('__weakref__',)


_SOURCES: 'weakref.WeakKeyDictionary[_Cap, Window]' = weakref.WeakKeyDictionary()


class Position:
    """An open-position capability issued by `View.enter` from a recorded decision; never built by a caller."""
    __slots__ = ('symbol', 'entry_ms', 'decision_id', '_cap', '__weakref__')

    def __init__(self, symbol, entry_ms, decision_id, cap, _token=None):
        if _token is not _TOKEN:
            raise PITError('a Position is issued only by View.enter (a recorded decision)')
        for k, v in (('symbol', symbol), ('entry_ms', entry_ms), ('decision_id', decision_id), ('_cap', cap)):
            object.__setattr__(self, k, v)

    def __setattr__(self, k, v):
        raise AttributeError('a position capability is immutable')

    def __eq__(self, other):
        return isinstance(other, Position) and (self.symbol, self.entry_ms, self.decision_id) == (
            other.symbol, other.entry_ms, other.decision_id)

    def __hash__(self):
        return hash((self.symbol, self.entry_ms, self.decision_id))

    def __repr__(self):
        return f'Position({self.symbol}, {S.utc(self.entry_ms)}, {self.decision_id!r})'


class Window:
    """The rows of one opened split: open_ms >= lo and available_ms <= hi. Created only by Access; harness-side."""

    def __init__(self, ds: Dataset, lo: int, hi: int, key: str, _token=None, *, access=None, split=None,
                 window=None, run_digest=None, eval_digest=None, execution_environment=None):
        if _token is not _TOKEN:
            raise PITError('a Window is opened only through Access (ledger-recorded)')
        self._ds, self.lo, self.hi, self.key = ds, lo, hi, key
        self._access, self.split, self.window_doc = access, split, window
        self.run_digest, self.eval_digest = run_digest, eval_digest
        self.execution_environment = execution_environment      # frozen run.code identity (None: no envelope)
        self._cap = _Cap()
        self._issued: weakref.WeakSet = weakref.WeakSet()
        self._decisions: list[tuple] = []
        self._shadow = False
        _SOURCES[self._cap] = self

    def view(self, t: int) -> 'View':
        if not self.lo <= t <= self.hi:
            raise PITError(f'decision time {S.utc(t)} outside the opened window {self.key}')
        return View(self._cap, t)

    def decisions(self) -> tuple:
        """The recorded position decisions (decision_id, symbol, entry_ms), in issue order."""
        return tuple(self._decisions)


def _src(cap) -> Window:
    w = _SOURCES.get(cap) if isinstance(cap, _Cap) else None
    if w is None:
        raise PITError('this view no longer refers to an opened window')
    return w


class View:
    """The evaluator's capability: a frozen decision time over an opened window, with no Dataset/Window reference."""
    __slots__ = ('_cap', 't')

    def __init__(self, cap: _Cap, t: int):
        object.__setattr__(self, '_cap', cap)
        object.__setattr__(self, 't', t)

    def __setattr__(self, k, v):
        raise AttributeError('a view is frozen at its decision time')

    @property
    def labels(self):
        return _src(self._cap)._ds.labels

    def members(self) -> frozenset:
        return _src(self._cap)._ds.members(self.t)

    def enter(self, symbol: str, decision_id: str) -> Position:
        """Issue a position capability for a decision at this view's time (membership checked now, decision
        recorded). Its holder may keep reading the symbol after it leaves the universe."""
        w = _src(self._cap)
        if not isinstance(decision_id, str) or not decision_id:
            raise PITError('decision_id must be a non-empty string')
        if symbol not in w._ds.members(self.t):
            raise PITError(f'{symbol} is not a universe member at {S.utc(self.t)}')
        if not w._shadow:
            if any(d[0] == decision_id for d in w._decisions):
                raise PITError(f'decision {decision_id!r} already issued a position')
            w._decisions.append((decision_id, symbol, self.t))
        p = Position(symbol, self.t, decision_id, self._cap, _TOKEN)
        w._issued.add(p)
        return p

    def _check(self, symbol: str, position):
        w = _src(self._cap)
        if position is None:
            if symbol not in w._ds.members(self.t):
                raise PITError(f'{symbol} is not a universe member at {S.utc(self.t)}')
            return w
        if not isinstance(position, Position) or position._cap is not self._cap or position not in w._issued:
            raise PITError('position is not a capability issued by this window')
        if position.symbol != symbol:
            raise PITError(f'position is for {position.symbol}, not {symbol}')
        if not w.lo <= position.entry_ms <= self.t:
            raise PITError('position entry must lie inside the window and not after the decision time')
        return w

    def _rows(self, w, series, symbol, interval, n=None, since=None):
        """Rows with lo <= time, (since <= time) and available <= t; the last n if n is given."""
        lo, out = max(w.lo, since or w.lo), []
        for f in reversed(w._ds.files(series, symbol, interval)):
            if f['first_open_ms'] is None or f['first_open_ms'] > self.t or f['last_open_ms'] < lo:
                continue
            rows = w._ds.slice(f, lo, self.t)
            out = rows + out
            if n is not None and len(out) >= n:
                break
        return tuple(out[-n:] if n is not None else out)

    def bars(self, symbol: str, interval: str, n: int, *, position=None, series='klines') -> tuple:
        """The last `n` closed bars (oldest first) available at t."""
        w = self._check(symbol, position)
        if type(n) is not int or n < 1:
            raise PITError('n must be an int >= 1')
        return self._rows(w, series, symbol, interval, n=n)

    def mark_bars(self, symbol: str, n: int, *, position=None) -> tuple:
        return self.bars(symbol, '1h', n, position=position, series='markPriceKlines')

    def funding(self, symbol: str, since_ms: int, *, position=None) -> tuple:
        w = self._check(symbol, position)
        return self._rows(w, 'fundingRate', symbol, None, since=since_ms)

    def funding_events(self, position: Position, exit_ms: int) -> list:
        """[(time_ms, rate, mark_close)] at the actual funding times entry <= time < exit (entry == T counts, exit == T
        does not); the mark is `FUNDING_MARK_PROXY`."""
        w = self._check(getattr(position, 'symbol', None), position)
        entry_ms, symbol = position.entry_ms, position.symbol
        if not entry_ms <= exit_ms <= self.t:
            raise PITError('funding events need entry <= exit <= t')
        # the phase anchor (the event at or before entry) is read too, so a missing event at entry or anywhere in
        # [entry, exit) fails closed here, independently of any cost model (Codex 6088593971 P2)
        span = (C.MAX_FUNDING_INTERVAL_H + 1) * HOUR
        hist = [f for f in self._rows(w, 'fundingRate', symbol, None, since=entry_ms - span) if f.time_ms < exit_ms]
        try:
            C.check_funding_sequence(symbol, hist, start_ms=entry_ms, end_ms=exit_ms)
        except C.CostError as e:
            raise PITError(f'funding events incomplete: {e}')
        rows = [f for f in hist if f.time_ms >= entry_ms]
        marks = self._rows(w, 'markPriceKlines', symbol, '1h', since=entry_ms - 2 * HOUR)
        avail = [m.available_ms for m in marks]
        out = []
        for f in rows:
            i = bisect.bisect_right(avail, f.time_ms) - 1
            if i < 0 or f.time_ms - avail[i] >= HOUR:
                raise PITError(f'{symbol}: no 1h mark bar available at funding {S.utc(f.time_ms)} (gap > 1 bar)')
            out.append((f.time_ms, f.rate, marks[i].close))
        return out

    def minute_bars(self, symbol: str, open_ms: int, close_ms: int, *, position=None) -> tuple:
        """1m bars of one closed signal bar [open_ms, close_ms); empty when the manifest has no 1m data for it."""
        w = self._check(symbol, position)
        if close_ms > self.t:
            raise PITError('the signal bar is not closed at t')
        return tuple(b for b in self._rows(w, 'klines', symbol, '1m', since=open_ms) if b.open_ms < close_ms)


def _perturb_future(ds: Dataset, t: int) -> dict:
    """Scale every cached row not yet available at t (prices x3, rates x-3); returns what to restore."""
    saved = {}
    for p, rows in ds._cache.items():
        saved[p] = list(rows)
        for i, r in enumerate(rows):
            if r.available_ms > t:
                rows[i] = r._replace(rate=-3 * r.rate - 1e-3) if isinstance(r, Funding) else \
                    r._replace(open=r.open * 3, high=r.high * 3, low=r.low * 3, close=r.close * 3)
    return saved


def _jsonable(v):
    if isinstance(v, Position):
        return {'__position__': id(v), 'symbol': v.symbol, 'entry_ms': v.entry_ms, 'decision_id': v.decision_id}
    if isinstance(v, (frozenset, set)):
        return sorted(v)
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


class Evaluated(list):
    """The runner's outputs (one per decision time), the summary `results` payload and the ledger-bound attestation."""
    attestation: dict
    attestation_digest: str
    results: object


def stdlib_roots() -> list[str]:
    """The interpreter's own standard library (`Lib`, `DLLs`); site-packages below them stays refused."""
    out = {os.path.dirname(os.__file__)}
    for base in {sys.base_prefix, sys.base_exec_prefix}:
        dlls = os.path.join(base, 'DLLs')
        if os.path.isdir(dlls):
            out.add(dlls)
        dyn = os.path.join(os.path.dirname(os.__file__), 'lib-dynload')
        if os.path.isdir(dyn):
            out.add(dyn)
    return sorted(out)


SANDBOX_ENV_KEYS = ('SYSTEMROOT', 'WINDIR')
# PYTHONCOERCECLOCALE=0: on POSIX under the C locale, CPython's PEP 538 coercion would otherwise ADD `LC_CTYPE` to the
# child's environment (Linux CI, not Windows), so the evaluator-visible environment would differ by platform; UTF-8 mode
# already fixes the text encoding, so coercion adds nothing.
SANDBOX_ENV = {'PYTHONCOERCECLOCALE': '0', 'PYTHONHASHSEED': '0', 'PYTHONUTF8': '1', 'TZ': 'UTC'}
TREE_MTIME = 946_684_800                         # 2000-01-01T00:00:00Z: every materialized file and directory


class _Sandbox:
    def __init__(self, tree: str, evaluator_path: str, function: str, summary, allow_files, cwd: str):
        lib = stdlib_roots()
        self._err = tempfile.TemporaryFile()
        env = {k: os.environ[k] for k in SANDBOX_ENV_KEYS if k in os.environ}
        env.update(SANDBOX_ENV)
        sys_path = [os.path.join(tree, 'tools', 'research'), tree, os.path.dirname(evaluator_path)]
        # path-independent launch (Cowork 6094010557): the child compiles sandbox.py's bytes under a sentinel name
        with open(SANDBOX, 'rb') as f:
            src = f.read()
        self.p = subprocess.Popen([sys.executable, '-s', '-S', '-B', '-P', '-c', R.SB.BOOT], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self._err, cwd=cwd, env=env)
        self.p.stdin.write(b'%d\n' % len(src) + src)
        self.send({'op': 'init', 'protocol': 'zb-eval-sandbox/1', 'evaluator_path': evaluator_path,
                   'function': function, 'summary': summary, 'lib_roots': lib, 'list_roots': [tree, cwd],
                   'probe_roots': sorted({tree, cwd, sys.base_prefix, sys.base_exec_prefix}),
                   'allow_files': sorted(allow_files), 'sys_path': sys_path})
        r = self.recv()
        if not r.get('ready'):
            self.close()
            raise PITError(f'evaluator sandbox: {r.get("error", "did not start")}')
        self.env, self.exec_env = r['env'], r['execution_environment']

    def summarize(self, outputs):
        self.send({'op': 'summarize', 'outputs': outputs})
        msg = self.recv()
        if 'error' in msg:
            raise PITError(f'evaluator summary failed: {msg["error"]}')
        return msg['result']

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj, sort_keys=True, allow_nan=False).encode('utf-8') + b'\n')
        self.p.stdin.flush()

    def recv(self) -> dict:
        line = self.p.stdout.readline()
        if not line:
            self._err.seek(0)
            tail = self._err.read()[-800:].decode('utf-8', 'replace')
            raise PITError(f'evaluator sandbox exited unexpectedly: {tail}')
        return json.loads(line)

    def decide(self, window: 'Window', t: int, positions: dict):
        view = window.view(t)
        self.send({'op': 'decide', 't': t})
        while True:
            msg = self.recv()
            if 'result' in msg:
                return msg['result']
            if 'error' in msg:
                raise PITError(f'evaluator failed at {S.utc(t)}: {msg["error"]}')
            name = msg.get('name')
            try:
                if msg.get('op') != 'call' or name not in VIEW_CALLS:
                    raise PITError(f'the sandbox asked for {name!r}, which is not a View accessor')
                args = [self._pos(a, positions) for a in msg.get('args', [])]
                kw = {k: self._pos(v, positions) for k, v in msg.get('kw', {}).items()}
                out = getattr(view, name) if name == 'labels' else getattr(view, name)(*args, **kw)
                if isinstance(out, Position):
                    positions[id(out)] = out
                self.send({'ok': _jsonable(out)})
            except (PITError, TypeError, ValueError) as e:
                self.send({'error': f'{type(e).__name__}: {e}'})

    @staticmethod
    def _pos(v, positions):
        if isinstance(v, dict) and set(v) == {'__position__'}:
            if v['__position__'] not in positions:
                raise PITError('position is not a capability issued by this window')
            return positions[v['__position__']]
        return v

    def close(self):
        try:
            if self.p.poll() is None:
                try:
                    self.send({'op': 'exit'})
                except OSError:
                    pass
                try:
                    self.p.wait(10)
                except subprocess.TimeoutExpired:
                    self.p.kill()
        finally:
            for f in (self.p.stdin, self.p.stdout):
                f.close()
            self._err.close()


def _code_path(f: str, repo: str) -> str:
    """Repo-relative '/' path (on-disk spelling) for a file inside the run checkout, else the absolute path."""
    rel = os.path.relpath(os.path.abspath(f), repo) if os.path.splitdrive(f)[0].lower() == \
        os.path.splitdrive(repo)[0].lower() else '..'
    if rel.startswith('..'):
        return os.path.abspath(f)
    return R._true_case(repo, rel.replace(os.sep, '/'))


def _code_hashes(files, repo: str) -> list[dict]:
    out = []
    for f in files:
        with open(f, 'rb') as fh:
            out.append({'path': _code_path(f, repo), 'sha256': hashlib.sha256(fh.read()).hexdigest()})
    return sorted(out, key=lambda c: c['path'])


def _sha_obj(obj) -> str:
    return hashlib.sha256(M.canonical(obj)).hexdigest()


def _materialize(closure, repo: str, evaluator_path: str, root: str) -> dict:
    """Copy exactly the hashed closure into `root` (repo layout; files outside the checkout but beside the evaluator
    go under `_evaluator/`), every mtime fixed to TREE_MTIME. Returns {original path: tree path}."""
    evdir = os.path.dirname(os.path.normcase(evaluator_path))
    out = {}
    for f in closure:
        rel = _code_path(f, repo)
        if os.path.isabs(rel):
            r2 = os.path.relpath(f, evdir)
            if r2.startswith('..'):
                raise PITError(f'{f}: evaluator code outside the run checkout and the evaluator directory')
            target = os.path.join(root, '_evaluator', r2)
        else:
            target = os.path.join(root, *rel.split('/'))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(f, 'rb') as src, open(target, 'wb') as dst:
            dst.write(src.read())
        out[f] = target
    for dirpath, dirs, files in os.walk(root, topdown=False):
        for n in files + dirs:
            os.utime(os.path.join(dirpath, n), (TREE_MTIME, TREE_MTIME))
    os.utime(root, (TREE_MTIME, TREE_MTIME))
    return out


def _host_needles(paths) -> list[str]:
    out = set()
    for p in paths:
        for v in (os.path.abspath(p), os.path.realpath(p)):
            n = os.path.normcase(v).rstrip('\\/')
            if len(n) > 3:                                   # never a bare drive / filesystem root
                out.add(n)
    return sorted(out)


def _refuse_host_paths(obj, needles, where: str) -> None:
    """Codex 6089789885 item 2: evaluator-visible results never carry a temporary-tree, cwd or interpreter host path
    (they would make one frozen identity mint a different result on every run / host)."""
    if isinstance(obj, str):
        s = os.path.normcase(obj)
        hit = next((n for n in needles if n in s), None)
        if hit is not None:
            raise PITError(f'the evaluator {where} contains a host path of the sandbox (temporary tree / cwd / '
                           f'interpreter): {obj[:120]!r}; results must not depend on where the run executes')
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _refuse_host_paths(k, needles, where)
            _refuse_host_paths(v, needles, where)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _refuse_host_paths(v, needles, where)


def _interpreter_roots() -> list[str]:
    return sorted({sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix, os.path.dirname(sys.executable)})


def _run_fresh(window: Window, closure, repo: str, evaluator_path: str, function: str, summary, times, *,
               perturb: bool, record: bool):
    """One complete evaluation in ONE fresh sandbox process over ITS OWN freshly materialized tree and cwd. With
    `record` False the window records no decision (a replay). Returns (outputs, results, sandbox env, execution
    environment)."""
    tmp = tempfile.TemporaryDirectory(prefix='zb-eval-')
    base_shadow = not record
    try:
        tree, cwd = os.path.join(tmp.name, 'tree'), os.path.join(tmp.name, 'cwd')
        os.makedirs(tree)
        os.makedirs(cwd)
        code_before = _code_hashes(closure, repo)
        where = _materialize(closure, repo, evaluator_path, tree)
        if sorted(c['sha256'] for c in code_before) != sorted(
                c['sha256'] for c in _code_hashes(list(where.values()), tree)):
            raise PITError('the materialized tree differs from the hashed closure')
        box = _Sandbox(tree, where[os.path.normcase(evaluator_path)], function, summary, list(where.values()), cwd)
        out, positions, results = [], {}, None
        window._shadow = base_shadow
        try:
            for t in times:
                res = box.decide(window, t, positions)
                if perturb:
                    ds = window._ds
                    saved = _perturb_future(ds, t)
                    window._shadow = True
                    try:
                        again = box.decide(window, t, positions)
                    finally:
                        window._shadow = base_shadow
                        for p, rows in saved.items():
                            ds._cache[p][:] = rows
                    if again != res:
                        raise PITError(f'evaluator output at {S.utc(t)} depends on data not available at t (future '
                                       'perturbation changed it): the view was bypassed')
                out.append(res)
            if summary is not None:
                results = box.summarize(list(out))
                if box.summarize(list(out)) != results:
                    raise PITError('the summary is not deterministic in the outputs')
            env, interp = box.env, box.exec_env
        finally:
            window._shadow = False
            box.close()
        needles = _host_needles([tmp.name, tree, cwd] + _interpreter_roots())
        _refuse_host_paths(out, needles, 'outputs')
        _refuse_host_paths(results, needles, 'summary results')
    finally:
        tmp.cleanup()
    return out, results, env, interp


def evaluate(window: Window, evaluator_path: str, function: str, times, *, summary: str | None = None,
             perturb: bool = True) -> Evaluated:
    """Run `function(view)` from the evaluator file `evaluator_path` in the sandbox at each decision time of the
    canonical schedule `times` (strictly increasing int ms; the evaluator receives a View proxy only), then, with
    `summary`, run `summary(outputs)` from the same file in the sandbox to produce the report `results` payload.
    With `perturb`, each decision is recomputed with every cached not-yet-available row perturbed (positions it
    issues there are not recorded) and must be identical; the summary runs twice and must be identical. The whole run
    is then replayed in a second fresh sandbox process over its own materialized tree, and outputs, summary, observed
    environment and execution environment must be identical - and, for a window opened with a frozen envelope, equal
    to `run.code.execution_environment` exactly; results carrying a temporary-tree, cwd or interpreter
    host path are refused. The evaluator runs from a materialized copy of its hashed closure (never the checkout).
    The attestation - entrypoint, schedule, outputs and results digests, closure hashes, observed sandbox environment,
    execution environment (`sandbox.execution_environment`, no host path) - is appended to the family ledger before
    anything is returned."""
    if not isinstance(window, Window) or window._access is None:
        raise PITError('evaluate needs a Window opened through Access')
    if not isinstance(evaluator_path, str) or not os.path.isfile(evaluator_path) or not evaluator_path.endswith('.py'):
        raise PITError('the evaluator must be a .py file')
    for fn in (function, summary):
        if fn is not None and (not isinstance(fn, str) or not fn.isidentifier()):
            raise PITError('the evaluator function / summary must be identifiers')
    times = list(times)
    try:
        schedule = R.schedule_of(times)
    except R.ReportError as e:
        raise PITError(str(e))
    evaluator_path = os.path.abspath(evaluator_path)
    repo = os.path.abspath(window._access.repo)
    # the evaluator runs the run checkout's research code, never another tree's (Codex 6088593971 P1)
    roots = [os.path.join(repo, 'tools', 'research'), repo, os.path.dirname(evaluator_path)]
    closure = R.closure_abs([evaluator_path], roots)
    code_before = _code_hashes(closure, repo)
    # Codex 6089789885 item 1: the run is executed in TWO fresh sandbox processes, each over its own materialized
    # tree and cwd; the second replays the schedule without recording decisions. Any difference in outputs, summary,
    # observed environment or execution environment (wall clock, PID, auto-seeded random, __file__, cwd ...) refuses.
    first = _run_fresh(window, closure, repo, evaluator_path, function, summary, times, perturb=perturb, record=True)
    second = _run_fresh(window, closure, repo, evaluator_path, function, summary, times, perturb=False, record=False)
    for i, what in enumerate(('outputs', 'summary results', 'observed sandbox environment', 'execution environment')):
        if first[i] != second[i]:
            raise PITError(f'the evaluator {what} differ between two fresh sandbox processes: one frozen identity '
                           'must yield one result (wall clock, PID, unseeded random, file/cwd paths?)')
    # Codex 6093381880 P1: each fresh process must run in exactly the execution environment frozen in run.code.
    if window.execution_environment is not None:
        for n, proc in enumerate((first, second), 1):
            bad = R.exec_env_mismatch(window.execution_environment, proc[3], f'fresh sandbox process {n}')
            if bad:
                raise PITError('; '.join(bad) + ' (evidence reproduces only under the frozen execution environment)')
    out, results, sandbox_env, exec_env = Evaluated(first[0]), first[1], first[2], first[3]
    code = _code_hashes(closure, repo)
    if code != code_before:
        raise PITError('evaluator code changed while it ran')
    rel = _code_path(os.path.normcase(evaluator_path), repo)
    ev_sha = next(c['sha256'] for c in code if c['path'] == rel)
    att = {'format': ATTESTATION_FORMAT, 'runner': RUNNER_ID, 'isolation': ISOLATION_ID, 'perturbed': bool(perturb),
           'split': window.split, 'window': window.window_doc, 'window_key': window.key,
           'run_digest': window.run_digest,
           'evaluator': {'path': rel, 'sha256': ev_sha, 'function': function, 'summary': summary},
           'code': code, 'schedule': schedule, 'outputs_digest': _sha_obj(list(out)),
           'results_digest': R.results_digest(results) if summary is not None else None,
           'decisions_digest': _sha_obj([list(d) for d in window.decisions()]),
           'sandbox_env': dict(sandbox_env, tree_mtime=TREE_MTIME, env=dict(SANDBOX_ENV)),
           'execution_environment': exec_env}
    digest = _sha_obj(att)
    window._access._record_evaluation(window, att, digest)
    out.attestation, out.attestation_digest, out.results = att, digest, results
    return out


def _frozen_env(envelope):
    return envelope['run']['code']['execution_environment'] if envelope else None


class Access:
    """Opens data for one hypothesis family; every opening is a ledger `data_access` record."""

    def __init__(self, ds: Dataset, plan: S.SplitPlan | None, ledger_path: str, *, candidate_id: str, author: str,
                 cairo_date: str, runs_dir=None, repo: str = M.REPO):
        self.ds, self.plan, self.path, self.repo = ds, plan, ledger_path, repo
        self.family = os.path.splitext(os.path.basename(ledger_path))[0]
        self.candidate_id, self.author, self.cairo_date = candidate_id, author, cairo_date
        self.runs_dir = runs_dir if runs_dir is not None else L._runs_of(L._dir_of(ledger_path))

    def _family_exists(self) -> bool:
        return os.path.exists(self.path)

    def _record(self, split, window, run_digest, detail, lineage):
        L.append(self.path, kind='data_access', candidate_id=self.candidate_id, split=split, window=window,
                 author=self.author, cairo_date=self.cairo_date, manifest_digest=self.ds.digest, run_digest=run_digest,
                 detail=detail, lineage=None if self._family_exists() else lineage)

    def _base_detail(self, extra):
        d = {'universe_digest': self.ds.universe_digest, 'books': list(self.ds.books), 'labels': list(self.ds.labels)}
        if self.plan is not None:
            d['split_plan_digest'] = self.plan.digest
        d.update(extra or {})
        return d

    def _record_evaluation(self, window: Window, att: dict, digest: str) -> None:
        """The runner's attestation as a ledger record (a component of a holdout reveal group when sealed)."""
        d = self._base_detail({'purpose': 'evaluation attestation', 'evaluation_attestation': digest,
                               'attestation': att})
        if window.eval_digest is not None:
            d['eval_digest'] = window.eval_digest
        self._record(window.split, window.window_doc, window.run_digest, d, None)

    def open(self, key: str, *, envelope=None, detail=None, lineage=None) -> Window:
        if self.plan is None:
            raise PITError('a split opens only through a SplitPlan')
        name, window = self.plan.split(key)['name'], self.plan.window(key)
        lo, hi = self.plan.access_range(key)
        d = self._base_detail(detail)
        d['split_key'] = key
        if name != 'holdout':
            if envelope is not None and envelope['run']['split'] == 'holdout':
                raise PITError('a holdout envelope cannot open a non-holdout split')
            rd = envelope['run_digest'] if envelope else None
            self._record(name, window, rd, d, lineage)
            return Window(self.ds, lo, hi, key, _TOKEN, access=self, split=name, window=window, run_digest=rd,
                          execution_environment=_frozen_env(envelope))
        self._guard_holdout(envelope, window)
        d['eval_digest'] = envelope['run']['eval_digest']
        self._record('holdout', window, envelope['run_digest'], d, lineage)
        return Window(self.ds, lo, hi, key, _TOKEN, access=self, split='holdout', window=window,
                      run_digest=envelope['run_digest'], eval_digest=d['eval_digest'],
                      execution_environment=_frozen_env(envelope))

    def open_development(self, start: str, end: str, *, detail=None, lineage=None) -> Window:
        lo, hi = S.parse_utc(start), S.parse_utc(end)
        if not lo < hi:
            raise PITError('development window needs start < end')
        if self.plan is not None and self.plan.overlaps_holdout(lo, hi):
            raise PITError('a development window may not touch the sealed holdout')
        win = {'start': start, 'end': end}
        self._record('development', win, None, self._base_detail(detail), lineage)
        return Window(self.ds, lo, hi, f'development {start}..{end}', _TOKEN, access=self, split='development',
                      window=win)

    def _guard_holdout(self, env, window):
        def need(ok, msg):
            if not ok:
                raise PITError(f'sealed holdout: {msg}')
        need(env is not None and self.runs_dir is not None, 'needs a frozen run envelope and a runs store')
        def real(x):
            return os.path.normcase(os.path.realpath(x))
        canon = real(CANONICAL_REPO)
        need(real(self.repo) == canon, 'the access checkout is not the canonical research checkout')
        need(real(os.path.dirname(os.path.abspath(self.path))) == os.path.join(canon, *LEDGER_REL),
             'the ledger is not the canonical registered ledger (research_evidence/ledger of the checkout)')
        need(real(self.runs_dir) == os.path.join(canon, *RUNS_REL),
             'the runs store is not the canonical research_evidence/runs of the checkout')
        try:
            L.verify(self.path)
        except L.LedgerError as e:
            raise PITError(f'sealed holdout: the ledger failed append-only verification: {e}')
        try:
            stored = R.load_envelope(self.runs_dir, env['run_digest'])
            need(R.run_digest(env['run']) == env['run_digest'], 'envelope digest mismatch')
        except R.ReportError as e:
            raise PITError(f'sealed holdout: {e}')
        need(stored == env, 'envelope is not the stored frozen envelope')
        need(not R.sealable(env), f'run not sealable: {R.sealable(env)}')
        bad = R.verify_code(env, self.repo)
        need(not bad, f'the executing checkout does not match the frozen code identity: {bad}')
        run = env['run']
        want = {'family': self.family, 'candidate_id': self.candidate_id, 'split': 'holdout', 'window': window,
                'manifest_digest': self.ds.digest, 'universe_digest': self.ds.universe_digest,
                'split_plan_digest': self.plan.digest, 'books': list(self.ds.books)}
        need(all(run[k] == v for k, v in want.items()), 'envelope identity differs from this access')
        need(self._family_exists(), 'no ledger for this family')
        recs = L._check_state(self.path)[self.family]
        idx = max((i for i, r in enumerate(recs) if r['kind'] in GROUP_KINDS), default=None)
        need(idx is not None, 'no atomic holdout_reveal record in the family ledger')
        head = recs[idx]
        ident = L.holdout_identity(head)
        need(ident == {'family': self.family, 'candidate_id': self.candidate_id, 'manifest_digest': self.ds.digest,
                       'window': window, 'eval_digest': run['eval_digest'], 'run_digest': env['run_digest']},
             'the latest reveal belongs to another run')
        need(all(r['split'] == 'holdout' and r['kind'] in L.COMPONENT_KINDS and L.holdout_identity(r) == ident
                 for r in recs[idx + 1:]), 'the reveal group is closed (another record followed it)')


# ------------------------------------------------------------------ smoke (shape / coverage only, no returns)
def smoke(ds: Dataset, ledger_path: str, start: str, end: str, symbols, *, author, cairo_date,
          candidate_id='res01.r3.smoke') -> dict:
    acc = Access(ds, None, ledger_path, candidate_id=candidate_id, author=author, cairo_date=cairo_date)
    w = acc.open_development(start, end, lineage='root',
                             detail={'purpose': 'R3 PIT-view shape/coverage smoke; no strategy, no returns',
                                     'symbols': list(symbols)})
    v0, v = w.view(w.lo), w.view(w.hi)
    out = {'window': [start, end], 'manifest_digest': ds.digest, 'universe_digest': ds.universe_digest,
           'books': list(ds.books), 'symbols': {}}
    big = 10 ** 7
    for s in symbols:
        member = s in ds.members(w.lo)
        row = {'member_at_start': member}
        if member:
            pos = v0.enter(s, f'smoke-{s}')
            row['1d'] = len(v.bars(s, '1d', big, position=pos))
            row['4h'] = len(v.bars(s, '4h', big, position=pos))
            row['mark_1h'] = len(v.mark_bars(s, big, position=pos))
            row['funding'] = len(v.funding(s, w.lo, position=pos))
            row['1m'] = len(v.minute_bars(s, w.lo, w.hi, position=pos))
            row['1m_expected'] = (w.hi - w.lo) // 60_000
            row['funding_marks_joined'] = len(v.funding_events(pos, w.hi))
        out['symbols'][s] = row
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='PIT view smoke: shape/coverage counts over a development window')
    ap.add_argument('cmd', choices=['smoke'])
    for k in ('--manifest', '--store', '--universe', '--books', '--ledger', '--start', '--end', '--symbols', '--author',
              '--cairo-date'):
        ap.add_argument(k, required=True)
    a = ap.parse_args(argv)
    try:
        with open(a.universe, encoding='utf-8') as f:
            u = json.load(f)
        ds = Dataset(M.load(a.manifest), a.store, u, books=a.books.split(','))
        print(json.dumps(smoke(ds, a.ledger, a.start, a.end, a.symbols.split(','), author=a.author,
                               cairo_date=a.cairo_date), indent=1, sort_keys=True))
        return 0
    except (PITError, M.ManifestError, L.LedgerError, U.UniverseError, S.SplitError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
