"""`zb-run-progress/2`: the sanitized operational progress record of an official R4 run (Codex 6094685201;
format per Codex 6094780810 Q2/Q3).

Two files per run in the run's evidence dir:
  progress-<run>.json   ONE live snapshot of the operational state, atomically replaced on every change (temp file in
                        the same directory + fsync + os.replace): a reader sees the previous or the next snapshot,
                        never a torn one. There is no append log to recover.
  final-<run>.json      ONE immutable sealed final record, written once at the end (`sealed` or `aborted`) and never
                        replaced (created by hard-linking a fully written temp file: an existing final refuses).
Both carry ONLY: the run identity, run state, stage (fold) state, integrity failure codes, artifact hashes and the
final evidence locations. Never credentials, tokens or account values, and never an outcome: no PnL, score, return,
Sharpe, equity, drawdown, win rate or other metric, no strategy comparison. Metrics live only in the sealed artifacts,
referenced here by SHA-256.

Identity (Codex 6094780810 Q2): the evaluator-identity digest plus the exact commit and the manifest, universe,
sourced-classification and preregistration digests (`null` = not applicable to this run, e.g. the survivor-only M3
reproduction has no universe). `identity_digest` = SHA-256 of the canonical identity; the run id = SHA-256 of identity
+ run config (first 16 hex). Wall time (`wall`, Cairo local ISO, optional) is the only clock field and is excluded
from every digest, so two identical runs produce byte-identical records apart from `wall`.

Enforced by `check_doc` on write AND on read: every key at every depth is whitelisted (unknown key = refusal); no
float / bool anywhere; the only integers are `seq` and a stage `fold` (bounded
by the preregistered walk-forward fold count, `fold_count()`); every string matches a strict shape (hex
digests, relative paths); stage names and integrity codes come from fixed allowlists (`STAGE_NAMES`,
`INTEGRITY_CODES`; an unknown code refuses), which are themselves checked against the outcome-word denylist.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

FORMAT = 'zb-run-progress/2'
CAIRO = ZoneInfo('Africa/Cairo')

HEX64 = re.compile(r'[0-9a-f]{64}\Z')
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
RUN_ID = re.compile(r'[0-9a-f]{16}\Z')
CODE = re.compile(r'[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z')
RELPATH = re.compile(r'[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*\Z')
WALL = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d\Z')
OUTCOME = re.compile(r'pnl|profit|loss|score|return|sharpe|sortino|calmar|equity|balance|drawdown|dd\b|wins?\b|win_?rate|'
                     r'expectan|mean|cagr|alpha|metric|perf|gain|edge|verdict|promote|reject|net_r|\br\b', re.I)

IDENTITY = ('commit', 'evaluator', 'manifest', 'universe', 'classification', 'prereg')
LIVE_KEYS = {'format', 'kind', 'run', 'seq', 'state', 'identity', 'identity_digest', 'stages', 'integrity', 'digest',
             'wall'}
FINAL_KEYS = LIVE_KEYS | {'artifacts', 'evidence'}
STATES = {'live': ('running', 'sealed', 'aborted'), 'final': ('sealed', 'aborted')}
TERMINAL = ('sealed', 'aborted')
# The fold channel is bounded by the PREREGISTERED walk-forward fold count (Codex 6095886395 N3), read from the
# committed preregistration, never hardcoded: a fold is an int (not bool) in range(fold_count()).
PREREG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      'research_evidence', 'prereg', 'trend_ema_mom.v1.json')


class ProgressError(ValueError):
    pass


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def doc_digest(doc: dict) -> str:
    return hashlib.sha256(canon({k: v for k, v in doc.items() if k not in ('digest', 'wall')})).hexdigest()


def identity_digest(identity: dict) -> str:
    return hashlib.sha256(canon(identity)).hexdigest()


def run_id(identity: dict, config: dict) -> str:
    """Deterministic: same identity + run config = same id (no clock, no randomness)."""
    return hashlib.sha256(canon({'identity': identity, 'config': config})).hexdigest()[:16]


# Fixed allowlists (Cowork 6095679832 F2, Codex 6095727203): a stage name or integrity code is one of these exact
# strings or the record refuses. Free-form codes could carry an interim outcome past any denylist ("beats_b0",
# "gate_cost_failed", "best_fold", ...). Extending a list is a reviewed code change; every entry is also checked
# against the OUTCOME denylist below at import time.
STAGE_NAMES = ('m3.open', 'm3.base', 'm3.2x', 'pit.open', 'pit.development', 'pit.walk_forward')
INTEGRITY_CODES = ('GateError', 'ReproError', 'PITError', 'OverlapError', 'GapUnderPositionError', 'ReproOnlyError',
                   'ManifestError', 'FamilyLogRefused', 'ReportError', 'ProgressError', 'CostError', 'SplitError',
                   'DataGap', 'OSError', 'Interrupted', 'Unexpected')
if any(OUTCOME.search(c) or not CODE.match(c) for c in STAGE_NAMES + INTEGRITY_CODES):
    raise RuntimeError('an allowlisted progress code carries an outcome word')


RENAMED = {'LedgerError': 'FamilyLogRefused'}           # 'Ledger' contains the denylisted word 'edge'


def integrity_code(name: str) -> str:
    """Map an exception class name onto the integrity allowlist (anything else is 'Unexpected')."""
    name = RENAMED.get(name, name)
    return name if name in INTEGRITY_CODES else 'Unexpected'


def _code(v, what, allowed):
    if type(v) is not str or v not in allowed or not CODE.match(v) or OUTCOME.search(v):
        raise ProgressError(f'{what}: {v!r} is not an allowlisted outcome-free code')


def _relpath(v, what):
    if type(v) is not str or not RELPATH.match(v) or any(p in ('.', '..') for p in v.split('/')):
        raise ProgressError(f'{what}: {v!r} is not a relative path inside the evidence dir')


def _keys(d, allowed, required, what):
    if not isinstance(d, dict):
        raise ProgressError(f'{what} must be an object')
    extra, missing = set(d) - set(allowed), set(required) - set(d)
    if extra:
        raise ProgressError(f'{what}: key(s) outside the whitelist: {sorted(extra)}')
    if missing:
        raise ProgressError(f'{what}: missing key(s) {sorted(missing)}')


def check_identity(v) -> None:
    _keys(v, IDENTITY, IDENTITY, 'identity')
    if type(v['commit']) is not str or not HEX40.match(v['commit']):
        raise ProgressError('identity.commit must be a full 40-hex SHA')
    if type(v['evaluator']) is not str or not HEX64.match(v['evaluator']):
        raise ProgressError('identity.evaluator must be a 64-hex evaluator-identity digest')
    for k in ('manifest', 'universe', 'classification', 'prereg'):
        if v[k] is not None and (type(v[k]) is not str or not HEX64.match(v[k])):
            raise ProgressError(f'identity.{k} must be a 64-hex digest or null (not applicable)')


@lru_cache(maxsize=None)
def _fold_count_of(path: str) -> int:
    try:
        with open(path, 'rb') as f:
            pre = json.loads(f.read())
        folds = [w['fold'] for w in pre['splits']['splits'] if w['name'] == 'walk_forward']
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ProgressError(f'{path}: cannot read the preregistered walk-forward folds: {e!r}')
    if not folds or any(type(k) is not int for k in folds) or folds != list(range(len(folds))):
        raise ProgressError(f'{path}: walk-forward folds must be the ints 0..n-1 in order')
    return len(folds)


def fold_count() -> int:
    """The preregistered walk-forward fold count (from the committed prereg `splits`)."""
    return _fold_count_of(PREREG)


def _fold(v, what):
    n = fold_count()
    if type(v) is not int or not 0 <= v < n:
        raise ProgressError(f'{what} must be an int in [0, {n}) (the preregistered walk-forward folds)')


def _stage(v):
    _keys(v, {'name', 'fold', 'status', 'artifacts'}, {'name', 'status'}, 'stage')
    _code(v['name'], 'stage.name', STAGE_NAMES)
    if v['status'] not in ('started', 'done'):
        raise ProgressError('stage.status must be started / done')
    if 'fold' in v:
        _fold(v['fold'], 'stage.fold')
    if 'artifacts' in v:
        _artifacts(v['artifacts'])


def _artifacts(v):
    if not isinstance(v, list):
        raise ProgressError('artifacts must be a list')
    for a in v:
        _keys(a, {'path', 'sha256'}, {'path', 'sha256'}, 'artifact')
        _relpath(a['path'], 'artifact.path')
        if type(a['sha256']) is not str or not HEX64.match(a['sha256']):
            raise ProgressError('artifact.sha256 must be 64 lowercase hex')
    if [a['path'] for a in v] != sorted({a['path'] for a in v}):
        raise ProgressError('artifacts must be sorted by path and unique')


def _integrity(v):
    if not isinstance(v, list):
        raise ProgressError('integrity must be a list')
    for i in v:
        _keys(i, {'code', 'stage', 'fold'}, {'code'}, 'integrity entry')
        _code(i['code'], 'integrity.code', INTEGRITY_CODES)
        if 'stage' in i:
            _code(i['stage'], 'integrity.stage', STAGE_NAMES)
        if 'fold' in i:
            _fold(i['fold'], 'integrity.fold')


def check_doc(doc: dict) -> None:
    """Refuse anything outside the whitelist, any numeric outcome and any malformed field."""
    if not isinstance(doc, dict) or doc.get('kind') not in STATES:
        raise ProgressError('kind must be live or final')
    kind = doc['kind']
    allowed = LIVE_KEYS if kind == 'live' else FINAL_KEYS
    _keys(doc, allowed, allowed - {'wall'}, f'{kind} record')
    if doc['format'] != FORMAT:
        raise ProgressError(f'format must be {FORMAT}')
    if type(doc['run']) is not str or not RUN_ID.match(doc['run']):
        raise ProgressError('run must be a 16-hex run id')
    if type(doc['seq']) is not int or doc['seq'] < 0:
        raise ProgressError('seq must be a non-negative int')
    if doc['state'] not in STATES[kind]:
        raise ProgressError(f'state must be one of {STATES[kind]}')
    check_identity(doc['identity'])
    if doc['identity_digest'] != identity_digest(doc['identity']):
        raise ProgressError('identity_digest does not match the identity')
    if not isinstance(doc['stages'], list):
        raise ProgressError('stages must be a list')
    for s in doc['stages']:
        _stage(s)
    _integrity(doc['integrity'])
    if kind == 'final':
        _artifacts(doc['artifacts'])
        if not isinstance(doc['evidence'], list) or doc['evidence'] != sorted(set(doc['evidence'])):
            raise ProgressError('evidence must be a sorted unique list of relative paths')
        for p in doc['evidence']:
            _relpath(p, 'evidence')
    if 'wall' in doc and (type(doc['wall']) is not str or not WALL.match(doc['wall'])):
        raise ProgressError('wall must be an ISO local time with offset')
    if type(doc['digest']) is not str or doc['digest'] != doc_digest(doc):
        raise ProgressError('digest does not match the record')


def doc_bytes(doc: dict) -> bytes:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False).encode('ascii') + b'\n'


def read(path: str) -> dict | None:
    """Read and check one snapshot / final record (None when absent)."""
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        data = f.read()
    try:
        doc = json.loads(data)
    except ValueError as e:
        raise ProgressError(f'{path}: not JSON: {e}')
    check_doc(doc)
    if doc_bytes(doc) != data:
        raise ProgressError(f'{path}: not in canonical form')
    return doc


def _tmp(path: str, data: bytes) -> str:
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(path) + '.', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _unlink(tmp)
        raise
    return tmp


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def atomic_replace(path: str, data: bytes) -> None:
    """Replace `path` with `data` atomically (temp in the same dir + fsync + os.replace)."""
    tmp = _tmp(path, data)
    try:
        os.replace(tmp, path)
    except BaseException:
        _unlink(tmp)
        raise


def write_once(path: str, data: bytes) -> None:
    """Create `path` with `data` exactly once: a fully written temp file is hard-linked into place, which fails if
    `path` exists (on Windows and POSIX alike), so a final record is never replaced or torn."""
    tmp = _tmp(path, data)
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise ProgressError(f'{path}: the sealed final record already exists (immutable)')
    finally:
        _unlink(tmp)


def cairo_now() -> str:
    return datetime.now(CAIRO).isoformat(timespec='seconds')


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class Progress:
    """Live snapshot writer + one sealed final. `clock=None` omits `wall` entirely (tests, determinism checks)."""

    def __init__(self, evidence_dir: str, *, identity: dict, config: dict, clock=cairo_now):
        check_identity(identity)
        self.dir, self.clock, self.identity = evidence_dir, clock, dict(identity)
        self.run = run_id(self.identity, config)
        self.path = os.path.join(evidence_dir, f'progress-{self.run}.json')
        self.final_path = os.path.join(evidence_dir, f'final-{self.run}.json')
        os.makedirs(evidence_dir, exist_ok=True)
        if os.path.exists(self.final_path):
            raise ProgressError(f'{self.final_path}: run already sealed; use a new evidence dir')
        cur = read(self.path)
        if cur is not None and cur['state'] in TERMINAL:
            raise ProgressError(f'{self.path}: run already {cur["state"]}; use a new evidence dir')
        if cur is not None and cur['identity'] != self.identity:
            raise ProgressError(f'{self.path}: snapshot belongs to another identity')
        self.seq = cur['seq'] if cur else -1
        self.stages = cur['stages'] if cur else []
        self.integrity = cur['integrity'] if cur else []
        self.done = False
        self._write('running')

    def _doc(self, kind: str, state: str, **extra) -> dict:
        doc = dict(extra, format=FORMAT, kind=kind, run=self.run, seq=self.seq, state=state, identity=self.identity,
                   identity_digest=identity_digest(self.identity), stages=self.stages, integrity=self.integrity)
        doc['digest'] = doc_digest(doc)
        if self.clock is not None:
            doc['wall'] = self.clock()
        check_doc(doc)
        return doc

    def _write(self, state: str) -> dict:
        if self.done:
            raise ProgressError('run already finished')
        self.seq += 1
        doc = self._doc('live', state)
        atomic_replace(self.path, doc_bytes(doc))
        return doc

    def artifacts(self, paths) -> list[dict]:
        out = []
        for p in sorted(paths):
            rel = os.path.relpath(os.path.abspath(p), os.path.abspath(self.dir)).replace(os.sep, '/')
            _relpath(rel, 'artifact.path')
            out.append({'path': rel, 'sha256': file_sha256(p)})
        return out

    def stage_start(self, name: str, fold: int | None = None) -> dict:
        self.stages = self.stages + [_mk_stage(name, fold, 'started')]
        return self._write('running')

    def stage_done(self, name: str, fold: int | None = None, artifacts=()) -> dict:
        s = _mk_stage(name, fold, 'done')
        if artifacts:
            s['artifacts'] = self.artifacts(artifacts)
        key = (name, fold)
        idx = max((i for i, x in enumerate(self.stages) if (x['name'], x.get('fold')) == key
                   and x['status'] == 'started'), default=None)
        if idx is None:
            raise ProgressError(f'stage {name} was not started')
        self.stages = self.stages[:idx] + [s] + self.stages[idx + 1:]
        return self._write('running')

    def integrity_failure(self, codes, stage: str | None = None, fold: int | None = None) -> dict:
        self.integrity = self.integrity + [_mk_integrity(c, stage, fold) for c in codes]
        return self._write('running')

    def _finish(self, state: str, artifacts, evidence) -> dict:
        self._write(state)
        final = self._doc('final', state, artifacts=self.artifacts(artifacts), evidence=sorted(set(evidence)))
        write_once(self.final_path, doc_bytes(final))
        self.done = True
        return final

    def sealed(self, artifacts, evidence) -> dict:
        return self._finish('sealed', artifacts, evidence)

    def aborted(self, codes, stage: str | None = None, fold: int | None = None) -> dict:
        self.integrity = self.integrity + [_mk_integrity(c, stage, fold) for c in codes]
        return self._finish('aborted', (), ())


def _mk_stage(name, fold, status):
    s = {'name': name, 'status': status}
    if fold is not None:
        s['fold'] = fold
    _stage(s)                                   # refuse before the in-memory state changes
    return s


def _mk_integrity(code, stage, fold):
    i = {'code': code}
    if stage is not None:
        i['stage'] = stage
    if fold is not None:
        i['fold'] = fold
    _integrity([i])                             # refuse before the in-memory state changes
    return i
