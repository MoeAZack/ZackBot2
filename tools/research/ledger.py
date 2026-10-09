"""`zb-ledger/1`: the append-only hypothesis-family contamination ledger (DATA-01/RES-01 plan section 4, R1 format).

One file per economic-hypothesis family, `<family>.jsonl`, one canonical-JSON record per line. Every data access,
variant, grid point, baseline choice and holdout reveal of the family (descendants, renames and variants included) is
one record. Records are hash-chained (`prev` = SHA-256 of the previous line's bytes) and numbered `seq` 1..n.

`REGISTRY.jsonl` in the same directory (`zb-ledger-registry/1`, hash-chained the same way) is the cross-file registry:
  family  declares a family before its first record: `parent` (null = an explicitly independent root hypothesis, else
          an earlier declared family it descends from / was renamed from) and `genesis` (SHA-256 of its first line)
  spend   a copy of every `holdout_reveal` / `window_spent` record, so a spend survives a renamed or deleted file
A window spent anywhere in a lineage (root + all descendants) is spent for the whole lineage. Every declared family
file must exist, every family file must be declared, and the spend copies must match the family files exactly.

Verification -- the ONLY public check is `verify()` (CLI `check`); it always runs everything below:
  records   schema, strict dates, contiguous int seq, hash chain, family == file stem, holdout rules
  registry  declarations, lineage spends, spend copies
  history   along Git ancestry every version of every ledger file (registry included) extends its parent's version, the
            working tree extends HEAD's, and a version with no parent version is accepted only as a declared genesis:
            the registry's first line must hash to the committed pin `REGISTRY_GENESIS`, a family's first line to its
            registry `genesis`. With `base` (CI: the PR base), the base's version must be a prefix as well.

Holdout rules (Codex clarification 1, comment 6071140643; Codex R1 P1, comment 6071894926; Cowork 6072246284):
- the first `holdout_reveal` on a window spends it for the lineage; a later overlapping reveal fails;
- a `holdout_rerun` must repeat the reveal's identity (family, candidate_id, manifest_digest, window,
  detail.eval_digest, run_digest) and at most `max_reruns` times: MAX_RERUNS, or a lower int `detail.max_reruns`
  declared in the reveal;
- holdout-split data_access / variant / grid_point / baseline records belong to the atomic reveal (or to a rerun of it):
  they directly follow it with its run_digest, manifest_digest, window and eval_digest; any other record closes it;
- no variant / grid_point (tuning) on a revealed window afterwards, whatever its split label;
- (R3) where a `runs` directory sits beside the ledger directory, a holdout record's run_digest must be recomputed from
  its frozen `zb-research-run/1` envelope there (`recompute_run_digest`), identity included.

Usage: python tools/research/ledger.py check PATH [--base REV]     (PATH = a family file or the ledger directory)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

FORMAT = 'zb-ledger/1'
REG_FORMAT = 'zb-ledger-registry/1'
REGISTRY = 'REGISTRY.jsonl'
LOCK = '.ledger.lock'
GENESIS = '0' * 64
# Pinned first line of research_evidence/ledger/REGISTRY.jsonl (its explicit genesis). Never edit.
REGISTRY_GENESIS = '5663e516cfa30ea2e8b2b4df2f10c851a74fef8ee734382a1a8256061d3299cd'
MAX_RERUNS = 2
KINDS = ('data_access', 'variant', 'grid_point', 'baseline', 'holdout_reveal', 'holdout_rerun', 'window_spent')
SPLITS = ('calibration', 'train', 'walk_forward', 'holdout', 'development', 'forward')
TRIAL_KINDS = ('variant', 'grid_point', 'baseline')
COMPONENT_KINDS = ('data_access', 'variant', 'grid_point', 'baseline')
TUNING_KINDS = ('variant', 'grid_point')
SPEND_KINDS = ('holdout_reveal', 'window_spent')
KEYS = {'format', 'seq', 'prev', 'family', 'kind', 'candidate_id', 'split', 'window', 'manifest_digest', 'run_digest',
        'detail', 'author', 'cairo_date'}
REG_KEYS = {'format', 'seq', 'prev', 'kind', 'family', 'ref'}
FAMILY_RE = re.compile(r'^[a-z0-9][a-z0-9_]*$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
IDENTITY = ('family', 'candidate_id', 'manifest_digest', 'window', 'eval_digest', 'run_digest')
GROUP = ('manifest_digest', 'window', 'eval_digest', 'run_digest')


class LedgerError(ValueError):
    pass


def line_of(rec: dict) -> bytes:
    return json.dumps(rec, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode() + b'\n'


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _strict(s, fmt, width) -> bool:
    """Exact-width string that strptime accepts (no 2025-13-45, no 2026-02-30)."""
    try:
        return isinstance(s, str) and len(s) == width and datetime.strptime(s, fmt).strftime(fmt) == s
    except ValueError:
        return False


def _overlap(a, b) -> bool:
    return a['start'] < b['end'] and b['start'] < a['end']


def holdout_identity(r: dict) -> dict:
    """The canonical identity a `holdout_rerun` must repeat exactly."""
    return {k: (r['detail'].get(k) if k == 'eval_digest' else r[k]) for k in IDENTITY}


def recompute_run_digest(r: dict, runs_dir=None):
    """R3 hook: recompute `run_digest` from the frozen `zb-research-run/1` envelope stored as
    `<runs_dir>/<run_digest>.json` (report.recompute_run_digest): the record's identity (family, candidate, manifest,
    window, eval_digest, split) must equal the envelope's and the envelope must hash to the stored digest, so a missing
    envelope or a caller-supplied string fails. `runs_dir` = the `runs` directory beside the ledger directory; None (no
    such directory, e.g. a scratch ledger) = not checked."""
    if runs_dir is None:
        return None
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import report as R
    return R.recompute_run_digest(r, runs_dir)


def _runs_of(d: str):
    """The run-envelope store beside a ledger directory (`research_evidence/runs` for `research_evidence/ledger`)."""
    p = os.path.join(os.path.dirname(os.path.abspath(d)), 'runs')
    return p if os.path.isdir(p) else None


def _lines(data: bytes, what: str):
    if data and not data.endswith(b'\n'):
        raise LedgerError(f'{what}: file must end with a newline')
    prev = GENESIS
    for i, raw in enumerate(data.splitlines(keepends=True), 1):
        def need(ok, msg, i=i):
            if not ok:
                raise LedgerError(f'{what} line {i}: {msg}')
        try:
            r = json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        except ValueError:
            raise LedgerError(f'{what} line {i}: not strict JSON (NaN / Infinity refused)')
        need(isinstance(r, dict) and type(r.get('seq')) is int and r['seq'] == i, f'seq must be the int {i}')
        need(line_of(r) == raw, 'not canonical JSON (sorted keys, compact, ASCII)')
        need(r.get('prev') == prev, 'hash chain broken (prev != sha256 of the previous line)')
        prev = _sha(raw)
        yield r, need


def _parse_family(data: bytes, family: str, runs_dir=None) -> list[dict]:
    """Parse one family file's bytes. NOT a verification on its own (bytes cannot prove append-only): use verify()."""
    if not FAMILY_RE.match(family):
        raise LedgerError(f'family {family!r} must match {FAMILY_RE.pattern}')
    recs, reveals, spent, revealed, group = [], {}, [], [], None
    for r, need in _lines(data, family):
        need(set(r) == KEYS, f'keys must be exactly {sorted(KEYS)}')
        need(r['format'] == FORMAT, f'format must be {FORMAT}')
        need(r['family'] == family, f'family must be {family!r}')
        need(r['kind'] in KINDS, f'kind must be one of {KINDS}')
        need(r['split'] in SPLITS, f'split must be one of {SPLITS}')
        need(isinstance(r['candidate_id'], str) and r['candidate_id'], 'candidate_id must be a non-empty string')
        w = r['window']
        need(isinstance(w, dict) and set(w) == {'start', 'end'}
             and all(_strict(w[k], '%Y-%m-%dT%H:%M:%SZ', 20) for k in w) and w['start'] < w['end'],
             'window must be {start, end} valid UTC YYYY-MM-DDTHH:MM:SSZ with start < end')
        for k in ('manifest_digest', 'run_digest'):
            need(r[k] is None or (isinstance(r[k], str) and HEX64.match(r[k])), f'{k} must be null or 64 lowercase hex')
        need(isinstance(r['detail'], dict), 'detail must be an object')
        need(isinstance(r['author'], str) and r['author'], 'author must be a non-empty string')
        need(_strict(r['cairo_date'], '%Y-%m-%d', 10), 'cairo_date must be a valid YYYY-MM-DD date')
        kind, ident = r['kind'], holdout_identity(r)
        if kind in ('holdout_reveal', 'holdout_rerun'):
            need(r['split'] == 'holdout' and r['run_digest'] and r['manifest_digest']
                 and isinstance(r['detail'].get('eval_digest'), str) and HEX64.match(r['detail']['eval_digest']),
                 f'{kind} needs split=holdout, run_digest, manifest_digest and detail.eval_digest (64 hex)')
            rd = recompute_run_digest(r, runs_dir)
            need(rd is None or rd == r['run_digest'], 'run_digest does not match the frozen run envelope')
        if kind == 'holdout_reveal':
            cap = r['detail'].get('max_reruns', MAX_RERUNS)
            need(type(cap) is int and 0 <= cap <= MAX_RERUNS, f'detail.max_reruns must be an int 0..{MAX_RERUNS}')
            need(r['run_digest'] not in reveals, 'run_digest already revealed; a repeat must be a holdout_rerun')
            need(not any(_overlap(w, s) for s in spent), 'holdout window overlaps a window already spent by this family')
            spent.append(w)
            revealed.append(w)
            reveals[r['run_digest']] = [ident, cap]
            group = ident
        elif kind == 'holdout_rerun':
            orig = reveals.get(r['run_digest'])
            need(orig is not None, 'holdout_rerun must repeat the identical run_digest of an earlier reveal')
            diff = [k for k in IDENTITY if orig[0][k] != ident[k]]
            need(not diff, f'holdout_rerun must repeat the identical reveal identity; differs in {diff}')
            need(orig[1] > 0, 'holdout rerun cap reached for this reveal')
            orig[1] -= 1
            group = ident                     # R3: a rerun's data accesses belong to it like a reveal's
        elif r['split'] == 'holdout' and kind in COMPONENT_KINDS:
            need(group is not None and all(group[k] == ident[k] for k in GROUP),
                 f'holdout-split {kind} must belong to the atomic reveal (directly after it, same run_digest, '
                 'manifest_digest, window and eval_digest)')
        else:
            need(kind not in TUNING_KINDS or not any(_overlap(w, s) for s in revealed),
                 f'{kind} on a revealed holdout window: no tuning on the holdout after a reveal')
            group = None
            if kind == 'window_spent':
                spent.append(w)
        recs.append(r)
    return recs


def _spend_ref(r: dict) -> dict:
    return {'seq': r['seq'], 'kind': r['kind'], 'window': r['window'], 'run_digest': r['run_digest']}


def _check_files(reg: bytes, fams: dict[str, bytes], runs_dir=None) -> dict[str, list]:
    """Records + registry + lineage over one directory's bytes (no history)."""
    parent, genesis, copies = {}, {}, []
    for r, need in _lines(reg, REGISTRY):
        need(set(r) == REG_KEYS and r['format'] == REG_FORMAT and r['kind'] in ('family', 'spend')
             and isinstance(r['ref'], dict) and isinstance(r['family'], str) and FAMILY_RE.match(r['family']),
             f'registry record must be {sorted(REG_KEYS)} with format {REG_FORMAT}, kind family|spend, a family name')
        f, ref = r['family'], r['ref']
        if r['kind'] == 'family':
            need(f not in parent, f'family {f!r} declared twice')
            need(set(ref) == {'parent', 'genesis'} and (ref['parent'] is None or ref['parent'] in parent),
                 'family lineage: parent must be null (independent root) or an earlier declared family')
            need(isinstance(ref['genesis'], str) and HEX64.match(ref['genesis']), 'genesis must be 64 hex')
            parent[f], genesis[f] = ref['parent'], ref['genesis']
        else:
            need(f in parent, f'spend for undeclared family {f!r}')
            copies.append((f, ref))
    for f in parent:
        if f not in fams:
            raise LedgerError(f'declared family {f!r}: ledger file missing (a family file is never deleted or renamed)')
    out = {}
    for f, data in sorted(fams.items()):
        if f not in parent:
            raise LedgerError(f'family file {f!r} is not declared in {REGISTRY}')
        out[f] = _parse_family(data, f, runs_dir)
        if not out[f] or _sha(data.splitlines(keepends=True)[0]) != genesis[f]:
            raise LedgerError(f'{f}: first line does not match its declared genesis')
    own = sorted((f, json.dumps(_spend_ref(r), sort_keys=True)) for f in out for r in out[f] if r['kind'] in SPEND_KINDS)
    if own != sorted((f, json.dumps(ref, sort_keys=True)) for f, ref in copies):
        raise LedgerError(f'{REGISTRY} spend copies do not match the family files\' holdout_reveal/window_spent records')

    def root(f):
        while parent[f] is not None:
            f = parent[f]
        return f
    seen = []
    for f, ref in copies:
        if ref['kind'] == 'holdout_reveal' and any(root(g) == root(f) and _overlap(ref['window'], w) for g, w in seen):
            raise LedgerError(f'{f}: holdout window already spent in its lineage (root {root(f)!r})')
        seen.append((f, ref['window']))
    return out


def _dir_of(path: str) -> str:
    return path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))


def _read_dir(d: str):
    def rd(p):
        with open(p, 'rb') as fh:
            return fh.read()
    reg = rd(os.path.join(d, REGISTRY)) if os.path.exists(os.path.join(d, REGISTRY)) else b''
    fams = {n[:-6]: rd(os.path.join(d, n)) for n in os.listdir(d) if n.endswith('.jsonl') and n != REGISTRY}
    return reg, fams


def _check_state(path: str) -> dict[str, list]:
    d = _dir_of(path)
    return _check_files(*_read_dir(d), _runs_of(d))


def n_trials(recs) -> int:
    """Holm family size read from the ledger: every variant, grid point and baseline choice recorded."""
    return sum(r['kind'] in TRIAL_KINDS for r in recs)


def _next(data: bytes, rec: dict) -> bytes:
    lines = data.splitlines(keepends=True)
    rec['seq'], rec['prev'] = len(lines) + 1, _sha(lines[-1]) if lines else GENESIS
    return line_of(rec)


def append(path: str, *, kind, candidate_id, split, window, author, cairo_date, manifest_digest=None, run_digest=None,
           detail=None, lineage=None) -> dict:
    """Append one record. A new family needs `lineage`: 'root' (an independent hypothesis) or its parent family."""
    d = _dir_of(path)
    family = os.path.splitext(os.path.basename(path))[0]
    lock = os.path.join(d, LOCK)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise LedgerError(f'{lock} exists: another writer holds the ledger lock')
    try:
        os.close(fd)
        reg, fams = _read_dir(d)
        runs = _runs_of(d)
        _check_files(reg, fams, runs)
        old = fams.get(family, b'')
        rec = {'format': FORMAT, 'family': family, 'kind': kind, 'candidate_id': candidate_id, 'split': split,
               'window': window, 'manifest_digest': manifest_digest, 'run_digest': run_digest, 'detail': detail or {},
               'author': author, 'cairo_date': cairo_date}
        try:
            line = _next(old, rec)
        except ValueError:
            raise LedgerError('record is not strict JSON (NaN / Infinity refused)')
        add = b''
        if not old:
            if not (lineage == 'root' or (isinstance(lineage, str) and lineage in fams)):
                raise LedgerError(f'new family {family!r} needs lineage="root" or a declared parent family')
            add += _next(reg, {'format': REG_FORMAT, 'kind': 'family', 'family': family,
                               'ref': {'parent': None if lineage == 'root' else lineage, 'genesis': _sha(line)}})
        elif lineage is not None:
            raise LedgerError(f'family {family!r} already exists; lineage is declared once')
        if kind in SPEND_KINDS:
            add += _next(reg + add, {'format': REG_FORMAT, 'kind': 'spend', 'family': family, 'ref': _spend_ref(rec)})
        fams[family] = old + line
        _check_files(reg + add, fams, runs)
        if add:
            with open(os.path.join(d, REGISTRY), 'ab') as f:
                f.write(add)
        with open(os.path.join(d, os.path.basename(path)), 'ab') as f:
            f.write(line)
        return rec
    finally:
        os.remove(lock)


def _git(repo, *args) -> bytes:
    p = subprocess.run(['git', '-C', repo, *args], capture_output=True)
    if p.returncode:
        raise LedgerError(f'git {" ".join(args)} failed: {p.stderr.decode(errors="replace").strip()}')
    return p.stdout


def _blob(repo, rev, rel):
    p = subprocess.run(['git', '-C', repo, 'show', f'{rev}:{rel}'], capture_output=True)
    return p.stdout if p.returncode == 0 else None


def _history(repo: str, rel: str, data: bytes, genesis: str, base) -> int:
    """Every version of `rel` along HEAD's ancestry, then the working bytes, extends its parent's version; a version
    with no parent version must be the declared genesis. Returns the number of commits checked."""
    def is_genesis(b, where):
        if not b or _sha(b.splitlines(keepends=True)[0]) != genesis:
            raise LedgerError(f'{rel}: new without a parent version in {where} and its first line is not the declared '
                              'genesis (a ledger is never restarted)')
    commits = _git(repo, 'rev-list', '--reverse', '--full-history', 'HEAD', '--', rel).decode().split()
    for c in commits:
        new = _blob(repo, c, rel)
        if new is None:
            raise LedgerError(f'{rel} deleted in {c[:10]}')
        olds = [o for o in (_blob(repo, p, rel) for p in _git(repo, 'rev-list', '--parents', '-n', '1', c).decode()
                            .split()[1:]) if o is not None]
        if not olds:
            is_genesis(new, c[:10])
        for o in olds:
            if not new.startswith(o):
                raise LedgerError(f'{rel} not append-only: rewritten in {c[:10]}')
    head = _blob(repo, 'HEAD', rel) if commits else None
    if head is None:
        is_genesis(data, 'the working tree')
    elif not data.startswith(head):
        raise LedgerError(f'{rel} not append-only: the working tree does not extend HEAD')
    if base:
        _git(repo, 'rev-parse', '--verify', f'{base}^{{commit}}')
        old = _blob(repo, base, rel)
        if old is not None and not data.startswith(old):
            raise LedgerError(f'{rel} not append-only against the base {base}')
    return len(commits)


def verify(path: str, *, base=None, registry_genesis=None) -> dict[str, list]:
    """The single verification entry point: records, registry, lineage and Git history of every ledger file in the
    directory of `path`. Fails closed outside a Git work tree. Returns {family: records}."""
    d = _dir_of(path)
    reg, fams = _read_dir(d)
    out = _check_files(reg, fams, _runs_of(d))
    if not os.path.isdir(d) or subprocess.run(['git', '-C', d, 'rev-parse', '--is-inside-work-tree'],
                                              capture_output=True).returncode:
        raise LedgerError(f'{d}: not inside a git work tree; the append-only history cannot be checked')
    top = _git(d, 'rev-parse', '--show-toplevel').decode().strip()
    reld = os.path.relpath(os.path.realpath(d), os.path.realpath(top)).replace(os.sep, '/')
    gen = {f: _sha(fams[f].splitlines(keepends=True)[0]) for f in fams}
    gen[REGISTRY[:-6]] = registry_genesis or REGISTRY_GENESIS
    for name, data in [(REGISTRY, reg)] + [(f + '.jsonl', fams[f]) for f in sorted(fams)]:
        _history(top, f'{reld}/{name}' if reld != '.' else name, data, gen[name[:-6]], base)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='verify the zb-ledger/1 directory holding PATH')
    ap.add_argument('cmd', choices=['check'])
    ap.add_argument('path')
    ap.add_argument('--base', default=os.environ.get('ZB_LEDGER_BASE') or None,
                    help='also require the base revision\'s versions to be prefixes (CI: the PR base)')
    a = ap.parse_args(argv)
    try:
        out = verify(a.path, base=a.base)
    except LedgerError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    for f, recs in out.items():
        print(f'OK {f}: {len(recs)} records, n_trials={n_trials(recs)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
