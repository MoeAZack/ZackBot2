"""`zb-ledger/1`: the append-only hypothesis-family contamination ledger (DATA-01/RES-01 plan section 4, R1 format).

One file per economic-hypothesis family, `<family>.jsonl`, one canonical-JSON record per line. Every data access,
variant, grid point, baseline choice and holdout reveal of the family (descendants, renames and variants included) is
one record. Records are hash-chained (`prev` = SHA-256 of the previous line's bytes) and numbered `seq` 1..n.

Checks:
  check_records   schema, contiguous seq, hash chain, family == file stem, holdout rules (below)
  check_append    the new bytes start with the old bytes (an edit, reorder or truncation fails)
  check_git       along Git ancestry every commit's version of the file is a prefix of its successor's (CI check)

Holdout rule (Codex clarification 1, comment 6071140643): the first `holdout_reveal` on a window spends it for the whole
family; any later `holdout_reveal` overlapping a spent window fails. A `holdout_rerun` is allowed only with the identical
`run_digest` of an earlier reveal (deterministic reproduction, no new decision). `window_spent` records a window already
seen before this ledger existed; a reveal overlapping it fails as well.

Usage: python tools/research/ledger.py check FILE [--git]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

FORMAT = 'zb-ledger/1'
GENESIS = '0' * 64
KINDS = ('data_access', 'variant', 'grid_point', 'baseline', 'holdout_reveal', 'holdout_rerun', 'window_spent')
SPLITS = ('calibration', 'train', 'walk_forward', 'holdout', 'development', 'forward')
TRIAL_KINDS = ('variant', 'grid_point', 'baseline')
KEYS = {'format', 'seq', 'prev', 'family', 'kind', 'candidate_id', 'split', 'window', 'manifest_digest', 'run_digest',
        'detail', 'author', 'cairo_date'}
FAMILY_RE = re.compile(r'^[a-z0-9][a-z0-9_]*$')
UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')


class LedgerError(ValueError):
    pass


def line_of(rec: dict) -> bytes:
    return json.dumps(rec, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii') + b'\n'


def _overlap(a, b) -> bool:
    return a['start'] < b['end'] and b['start'] < a['end']


def check_records(data: bytes, family: str) -> list[dict]:
    """Parse and check one ledger file's bytes. Returns the records; raises LedgerError on the first problem."""
    if not FAMILY_RE.match(family):
        raise LedgerError(f'family {family!r} must match {FAMILY_RE.pattern}')
    if data and not data.endswith(b'\n'):
        raise LedgerError('file must end with a newline')
    recs, prev, reveals, spent = [], GENESIS, {}, []
    for i, raw in enumerate(data.splitlines(keepends=True), 1):
        def need(ok, msg):
            if not ok:
                raise LedgerError(f'line {i}: {msg}')
        try:
            r = json.loads(raw)
        except ValueError:
            raise LedgerError(f'line {i}: not JSON')
        need(isinstance(r, dict) and set(r) == KEYS, f'keys must be exactly {sorted(KEYS)}')
        need(line_of(r) == raw, 'not canonical JSON (sorted keys, compact, ASCII)')
        need(r['format'] == FORMAT, f'format must be {FORMAT}')
        need(r['seq'] == i, f'seq must be {i}')
        need(r['prev'] == prev, 'hash chain broken (prev != sha256 of the previous line)')
        need(r['family'] == family, f'family must be {family!r}')
        need(r['kind'] in KINDS, f'kind must be one of {KINDS}')
        need(r['split'] in SPLITS, f'split must be one of {SPLITS}')
        need(isinstance(r['candidate_id'], str) and r['candidate_id'], 'candidate_id must be a non-empty string')
        w = r['window']
        need(isinstance(w, dict) and set(w) == {'start', 'end'} and all(UTC_RE.match(str(w[k])) for k in w)
             and w['start'] < w['end'], 'window must be {start, end} UTC YYYY-MM-DDTHH:MM:SSZ with start < end')
        for k in ('manifest_digest', 'run_digest'):
            need(r[k] is None or (isinstance(r[k], str) and HEX64.match(r[k])), f'{k} must be null or 64 lowercase hex')
        need(isinstance(r['detail'], dict), 'detail must be an object')
        need(isinstance(r['author'], str) and r['author'], 'author must be a non-empty string')
        need(isinstance(r['cairo_date'], str) and DATE_RE.match(r['cairo_date']), 'cairo_date must be YYYY-MM-DD')
        if r['kind'] in ('holdout_reveal', 'holdout_rerun'):
            need(r['split'] == 'holdout' and r['run_digest'] and r['manifest_digest'],
                 f'{r["kind"]} needs split=holdout, run_digest and manifest_digest')
        if r['kind'] == 'holdout_reveal':
            need(not any(_overlap(w, s) for s in spent), 'holdout window overlaps a window already spent by this family')
            spent.append(w)
            reveals[r['run_digest']] = w
        elif r['kind'] == 'holdout_rerun':
            need(reveals.get(r['run_digest']) == w, 'holdout_rerun must repeat the identical run_digest and window of a reveal')
        elif r['kind'] == 'window_spent':
            spent.append(w)
        recs.append(r)
        prev = hashlib.sha256(raw).hexdigest()
    return recs


def check_append(old: bytes, new: bytes) -> None:
    if not new.startswith(old):
        raise LedgerError('not append-only: the new version does not start with the old bytes')


def n_trials(recs) -> int:
    """Holm family size read from the ledger: every variant, grid point and baseline choice recorded."""
    return sum(r['kind'] in TRIAL_KINDS for r in recs)


def append(path: str, *, kind, candidate_id, split, window, author, cairo_date, manifest_digest=None, run_digest=None,
           detail=None) -> dict:
    family = os.path.splitext(os.path.basename(path))[0]
    old = open(path, 'rb').read() if os.path.exists(path) else b''
    recs = check_records(old, family)
    rec = {'format': FORMAT, 'seq': len(recs) + 1,
           'prev': hashlib.sha256(old.splitlines(keepends=True)[-1]).hexdigest() if old else GENESIS,
           'family': family, 'kind': kind, 'candidate_id': candidate_id, 'split': split, 'window': window,
           'manifest_digest': manifest_digest, 'run_digest': run_digest, 'detail': detail or {}, 'author': author,
           'cairo_date': cairo_date}
    new = old + line_of(rec)
    check_records(new, family)
    with open(path, 'ab') as f:
        f.write(line_of(rec))
    return rec


def _git(repo, *args) -> bytes:
    return subprocess.run(['git', '-C', repo, *args], check=True, capture_output=True).stdout


def _blob(repo, rev, rel):
    p = subprocess.run(['git', '-C', repo, 'show', f'{rev}:{rel}'], capture_output=True)
    return p.stdout if p.returncode == 0 else None


def check_git(repo: str, rel: str, rev: str = 'HEAD') -> int:
    """For every commit touching `rel` reachable from `rev`, each parent's version must be a prefix of the commit's.
    A deleted file counts as a rewrite. Returns the number of commits checked."""
    commits = _git(repo, 'rev-list', '--reverse', '--full-history', rev, '--', rel).decode().split()
    for c in commits:
        new = _blob(repo, c, rel)
        if new is None:
            raise LedgerError(f'{rel} deleted in {c[:10]}')
        parents = _git(repo, 'rev-list', '--parents', '-n', '1', c).decode().split()[1:]
        for p in parents:
            old = _blob(repo, p, rel)
            if old is not None and not new.startswith(old):
                raise LedgerError(f'{rel} rewritten in {c[:10]} (parent {p[:10]} is not a prefix)')
        check_records(new, os.path.splitext(os.path.basename(rel))[0])
    return len(commits)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='check a zb-ledger/1 file')
    ap.add_argument('cmd', choices=['check'])
    ap.add_argument('file')
    ap.add_argument('--git', action='store_true', help='also check append-only history along Git ancestry')
    a = ap.parse_args(argv)
    try:
        recs = check_records(open(a.file, 'rb').read(), os.path.splitext(os.path.basename(a.file))[0])
        if a.git:
            repo = os.path.dirname(os.path.abspath(a.file))
            top = _git(repo, 'rev-parse', '--show-toplevel').decode().strip()
            check_git(top, os.path.relpath(os.path.abspath(a.file), top).replace(os.sep, '/'))
    except LedgerError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    print(f'OK {len(recs)} records, n_trials={n_trials(recs)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
