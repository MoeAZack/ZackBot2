"""`zb-run-progress/1`: the sanitized operational progress record of an official R4 run (Codex 6094685201).

One append-only JSONL file per run under the run's evidence dir (`progress-<run_id>.jsonl`). It carries ONLY:
exact commit, registered dataset / manifest identity, run and stage (fold) state, integrity failures, artifact
hashes and the final evidence locations. Never credentials, tokens or account values, and never an outcome: no PnL,
score, return, Sharpe, equity, drawdown, win rate or other metric, and no strategy comparison. Metrics live only in
the run's sealed artifacts, which this file references by SHA-256.

Enforced by `check_record` on write AND on read (`verify`):
  * every key at every depth is whitelisted per event (unknown key = refusal);
  * no float / bool anywhere; the only integers are `seq` and the stage `fold` index;
  * every string field matches a strict shape (hex digests, codes, relative paths); codes and stage names may not
    carry an outcome word (pnl, score, return, sharpe, equity, ...);
  * records are hash-chained (`prev` = previous `digest`); `digest` = SHA-256 of the canonical record without
    `digest` and `wall`. `wall` (Cairo local ISO time, optional) is the only wall-clock field and is excluded from
    the chain, so two identical runs give byte-identical records apart from `wall`;
  * after `run_sealed` or `run_aborted` nothing more is appended.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

FORMAT = 'zb-run-progress/1'
CAIRO = ZoneInfo('Africa/Cairo')
GENESIS = '0' * 64

HEX64 = re.compile(r'[0-9a-f]{64}\Z')
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
RUN_ID = re.compile(r'[0-9a-f]{16}\Z')
CODE = re.compile(r'[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z')
RELPATH = re.compile(r'[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*\Z')
WALL = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d\Z')
OUTCOME = re.compile(r'pnl|profit|loss|score|return|sharpe|sortino|calmar|equity|balance|drawdown|dd\b|wins?\b|win_?rate|'
                     r'expectan|mean|cagr|alpha|metric|perf|gain|edge|verdict|promote|reject|net_r|\br\b', re.I)

COMMON = {'format', 'seq', 'prev', 'digest', 'wall', 'event', 'run'}
EVENTS = {                         # event -> (required extra keys, optional extra keys)
    'run_start': ({'commit', 'dataset'}, set()),
    'stage_start': ({'stage'}, set()),
    'stage_done': ({'stage'}, {'artifacts'}),
    'integrity_failure': ({'integrity'}, {'stage'}),
    'run_sealed': ({'artifacts', 'evidence'}, set()),
    'run_aborted': ({'integrity'}, {'stage'}),
}
TERMINAL = {'run_sealed', 'run_aborted'}


class ProgressError(ValueError):
    pass


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def record_digest(rec: dict) -> str:
    return hashlib.sha256(canon({k: v for k, v in rec.items() if k not in ('digest', 'wall')})).hexdigest()


def run_id(commit: str, dataset: dict, config: dict) -> str:
    """Deterministic: same commit + dataset identity + run config = same id (no clock, no randomness)."""
    return hashlib.sha256(canon({'commit': commit, 'dataset': dataset, 'config': config})).hexdigest()[:16]


def _code(v, what):
    if type(v) is not str or not CODE.match(v) or OUTCOME.search(v):
        raise ProgressError(f'{what}: {v!r} is not an outcome-free code')


def _relpath(v, what):
    if type(v) is not str or not RELPATH.match(v) or any(p in ('.', '..') for p in v.split('/')):
        raise ProgressError(f'{what}: {v!r} is not a relative path inside the evidence dir')


def _keys(d, allowed, required, what):
    if not isinstance(d, dict):
        raise ProgressError(f'{what} must be an object')
    extra, missing = set(d) - allowed, required - set(d)
    if extra:
        raise ProgressError(f'{what}: key(s) outside the whitelist: {sorted(extra)}')
    if missing:
        raise ProgressError(f'{what}: missing key(s) {sorted(missing)}')


def _stage(v):
    _keys(v, {'name', 'fold'}, {'name'}, 'stage')
    _code(v['name'], 'stage.name')
    if 'fold' in v and (type(v['fold']) is not int or v['fold'] < 0):
        raise ProgressError('stage.fold must be a non-negative int')


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


def check_record(rec: dict) -> None:
    """Refuse anything outside the whitelist, any numeric outcome and any malformed field."""
    if not isinstance(rec, dict) or type(rec.get('event')) is not str or rec['event'] not in EVENTS:
        raise ProgressError(f'event must be one of {sorted(EVENTS)}')
    req, opt = EVENTS[rec['event']]
    _keys(rec, COMMON | req | opt, (COMMON - {'wall'}) | req, f'record {rec["event"]}')
    if rec['format'] != FORMAT:
        raise ProgressError(f'format must be {FORMAT}')
    if type(rec['seq']) is not int or rec['seq'] < 0:
        raise ProgressError('seq must be a non-negative int')
    for k in ('prev', 'digest'):
        if type(rec[k]) is not str or not HEX64.match(rec[k]):
            raise ProgressError(f'{k} must be 64 lowercase hex')
    if type(rec['run']) is not str or not RUN_ID.match(rec['run']):
        raise ProgressError('run must be a 16-hex run id')
    if 'wall' in rec and (type(rec['wall']) is not str or not WALL.match(rec['wall'])):
        raise ProgressError('wall must be an ISO local time with offset')
    if 'commit' in rec and (type(rec['commit']) is not str or not HEX40.match(rec['commit'])):
        raise ProgressError('commit must be a full 40-hex SHA')
    if 'dataset' in rec:
        ds = rec['dataset']
        _keys(ds, {'manifest', 'digest'}, {'manifest', 'digest'}, 'dataset')
        _relpath(ds['manifest'], 'dataset.manifest')
        if type(ds['digest']) is not str or not HEX64.match(ds['digest']):
            raise ProgressError('dataset.digest must be 64 lowercase hex')
    if 'stage' in rec:
        _stage(rec['stage'])
    if 'integrity' in rec:
        if not isinstance(rec['integrity'], list) or not rec['integrity']:
            raise ProgressError('integrity must be a non-empty list of codes')
        for c in rec['integrity']:
            _code(c, 'integrity')
    if 'artifacts' in rec:
        _artifacts(rec['artifacts'])
    if 'evidence' in rec:
        if not isinstance(rec['evidence'], list) or rec['evidence'] != sorted(set(rec['evidence'])):
            raise ProgressError('evidence must be a sorted unique list of relative paths')
        for p in rec['evidence']:
            _relpath(p, 'evidence')
    if record_digest(rec) != rec['digest']:
        raise ProgressError('digest does not match the record')


def verify(path: str) -> list[dict]:
    """Read and check the whole chain: schema, seq continuity, prev links, digests, one run id, run_start first,
    nothing after a terminal event."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, 'rb') as f:
        data = f.read()
    if data and not data.endswith(b'\n'):
        raise ProgressError(f'{path}: truncated last line')
    prev = GENESIS
    for i, line in enumerate(data.splitlines()):
        try:
            rec = json.loads(line)
        except ValueError as e:
            raise ProgressError(f'{path}:{i + 1}: not JSON: {e}')
        check_record(rec)
        if rec['seq'] != i or rec['prev'] != prev:
            raise ProgressError(f'{path}:{i + 1}: broken chain (seq / prev)')
        if (i == 0) != (rec['event'] == 'run_start'):
            raise ProgressError(f'{path}:{i + 1}: run_start must be the first and only start record')
        if out and (out[-1]['event'] in TERMINAL or rec['run'] != out[0]['run']):
            raise ProgressError(f'{path}:{i + 1}: record after a terminal event or from another run')
        if canon_line(rec) != line + b'\n':
            raise ProgressError(f'{path}:{i + 1}: not in canonical form')
        out.append(rec)
        prev = rec['digest']
    return out


def canon_line(rec: dict) -> bytes:
    return canon(rec) + b'\n'


def cairo_now() -> str:
    return datetime.now(CAIRO).isoformat(timespec='seconds')


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class Progress:
    """Append-only writer. `clock=None` omits `wall` entirely (tests, determinism checks)."""

    def __init__(self, evidence_dir: str, *, commit: str, dataset: dict, config: dict, clock=cairo_now):
        self.dir, self.clock = evidence_dir, clock
        self.run = run_id(commit, dataset, config)
        self.path = os.path.join(evidence_dir, f'progress-{self.run}.jsonl')
        os.makedirs(evidence_dir, exist_ok=True)
        recs = verify(self.path)
        if recs and recs[-1]['event'] in TERMINAL:
            raise ProgressError(f'{self.path}: run already {recs[-1]["event"]}; use a new evidence dir')
        self.seq, self.prev = len(recs), recs[-1]['digest'] if recs else GENESIS
        if not recs:
            self._emit('run_start', commit=commit, dataset=dataset)

    def _emit(self, event: str, **fields) -> dict:
        if self.prev is None:
            raise ProgressError('run already finished')
        rec = dict(fields, format=FORMAT, seq=self.seq, prev=self.prev, event=event, run=self.run)
        rec['digest'] = record_digest(rec)
        if self.clock is not None:
            rec['wall'] = self.clock()
        check_record(rec)
        with open(self.path, 'ab') as f:
            f.write(canon_line(rec))
            f.flush()
            os.fsync(f.fileno())
        self.seq, self.prev = self.seq + 1, rec['digest']
        if event in TERMINAL:
            self.prev = None
        return rec

    def artifacts(self, paths) -> list[dict]:
        out = []
        for p in sorted(paths):
            rel = os.path.relpath(os.path.abspath(p), os.path.abspath(self.dir)).replace(os.sep, '/')
            _relpath(rel, 'artifact.path')
            out.append({'path': rel, 'sha256': file_sha256(p)})
        return out

    def stage_start(self, name: str, fold: int | None = None) -> dict:
        return self._emit('stage_start', stage=_mk_stage(name, fold))

    def stage_done(self, name: str, fold: int | None = None, artifacts=()) -> dict:
        extra = {'artifacts': self.artifacts(artifacts)} if artifacts else {}
        return self._emit('stage_done', stage=_mk_stage(name, fold), **extra)

    def integrity_failure(self, codes, stage: str | None = None, fold: int | None = None) -> dict:
        extra = {'stage': _mk_stage(stage, fold)} if stage else {}
        return self._emit('integrity_failure', integrity=list(codes), **extra)

    def sealed(self, artifacts, evidence) -> dict:
        return self._emit('run_sealed', artifacts=self.artifacts(artifacts), evidence=sorted(set(evidence)))

    def aborted(self, codes, stage: str | None = None, fold: int | None = None) -> dict:
        extra = {'stage': _mk_stage(stage, fold)} if stage else {}
        return self._emit('run_aborted', integrity=list(codes), **extra)


def _mk_stage(name, fold):
    return {'name': name} if fold is None else {'name': name, 'fold': fold}
