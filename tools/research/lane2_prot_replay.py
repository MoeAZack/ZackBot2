"""Fixed-book PROT replay. Research only; never imports workstation settings.

Run from this checkout after staging pinned ref with git archive into .git/prot_replay_source:
 python tools/research/lane2_prot_replay.py OUT.jsonl --families path4h,worst4h,h1
All timestamps are UTC candle opens, not inferred intrabar execution times.
"""
import argparse
import csv
import copy
import hashlib
import gzip
import json
import math
import sys
import subprocess
import types
import zlib
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.git' / 'prot_replay_source'
INPUT = ROOT / 'docs' / 'research' / 'lane2_funding_audit_20261116' / 'inputs'
REF = 'ecaff93d272deca7fa80eb9decc1620c23286ed0'
CHARGE = "eq -= p['qty'] * c * FPB; p['realized'] -= p['qty'] * c * FPB"
BAR_MS = 4 * 60 * 60 * 1000


def instrument(source):
    """Insert a recording hook in memory only, failing closed on source drift."""
    if source.count(CHARGE) != 1:
        raise ValueError('funding charge site changed or is ambiguous')
    return source.replace(CHARGE, "_replay_hook(sl, s, p, i, c, FPB); " + CHARGE)


def timestamp(value):
    import pandas as pd
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        t = t.tz_localize('UTC')
    return int(t.value // 1_000_000)


def serialize_trade(row, fills, charges, times, job_id):
    key = (row.sleeve, row.sym, int(row.i_in))
    fs, cs = fills[key], charges[key]
    if not fs or fs[0]['kind'] != 'entry' or fs[-1]['kind'] != 'exit':
        raise ValueError(f'missing complete fills for {key}')
    risk = float(fs[0]['risk'])
    if risk <= 0 or not math.isclose(float(row.pnl) / risk, float(row.R), rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError(f'inconsistent risk for {key}')
    quantity = 0.0
    out_fills = []
    for f in fs:
        qty = float(f['qty'])
        quantity += qty if f['kind'] in ('entry', 'add', 'dca') else -qty
        out_fills.append({**f, 'time_ms': timestamp(times[f['i']]), 'signed_qty': int(row.side) * qty * (1 if f['kind'] in ('entry', 'add', 'dca') else -1)})
    if not math.isclose(quantity, 0.0, abs_tol=1e-8):
        raise ValueError(f'fills do not close {key}: {quantity}')
    return dict(trade_id=f'{job_id}|{row.sym}|{row.i_in}', sym=row.sym, side=int(row.side),
                i_in=int(row.i_in), i_out=int(row.i_out), t_in_ms=timestamp(times[row.i_in]),
                t_out_ms=timestamp(times[row.i_out]), risk=risk, baseline_pnl=float(row.pnl),
                baseline_R=float(row.R), proxy_cash_paid=sum(c['cash_paid'] for c in cs),
                fills=out_fills, proxy_bars=cs)


def verify_source():
    listing = subprocess.check_output(['git', '-C', str(ROOT), 'ls-tree', '-r', '-z', REF])
    tracked = {}
    for item in listing.split(b'\0'):
        if not item: continue
        meta, name = item.split(b'\t', 1)
        path = name.decode('utf-8')
        if path.endswith('.py'):
            tracked[path] = meta.split()[2].decode('ascii')
    actual = {str(p.relative_to(SOURCE)).replace('\\', '/'): p for p in SOURCE.rglob('*.py')}
    if not tracked or set(tracked) != set(actual):
        raise ValueError('staged Python source file list differs from pinned ref')
    hashes = {}
    for name, blob in tracked.items():
        raw = actual[name].read_bytes()
        object_id = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if object_id != blob: raise ValueError(f'staged Python source differs from pinned ref: {name}')
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return hashes


def setup():
    verify_source()
    sys.path[:0] = [str(SOURCE / 'tools' / 'research'), str(SOURCE)]
    path = SOURCE / 'backtest.py'
    raw = path.read_bytes()
    module = types.ModuleType('backtest')
    module.__file__ = str(path)
    sys.modules['backtest'] = module
    events = []
    def hook(sl, sym, pos, i, close, fpb):
        events.append((sl['cfg']['key'], sym, int(pos['i']), int(i), int(pos['side']),
                       float(pos['qty']), float(close), float(pos['qty'] * close * fpb)))
    module.__dict__['_replay_hook'] = hook
    exec(compile(instrument(raw.decode('utf-8')), str(path), 'exec'), module.__dict__)
    import prot_compare as pc
    import prot_path_check as ph
    return module, pc, ph, events, hashlib.sha256(raw).hexdigest()


def replay(bt, pc, ph, events, published, family, strategy, sides, variant, mgmt, cost, scope, provenance):
    import pandas as pd
    events.clear()
    bt.FEE = cost / 1e4 - bt.SLIP
    pc._pin_buffer('live')
    if family == 'h1':
        ph._install(scope)
        book, t0 = ph._books()
        kwargs = dict(t0=t0)
    else:
        ph._install('path')
        bk, t0, t1 = pc.periods()[scope]
        book = pc.books()[bk]
        kwargs = dict(t0=t0, t1=t1, pessimistic='path' if family == 'path4h' else 'worst')
    fills = []
    trades, _ = bt.run(book, [dict(key=strategy, share=1.0, risk=0.02, max_pos=4,
                                  sides=sides, mgmt=mgmt)], start=4000.0, fill_log=fills, **kwargs)
    job_id = f'{family}|{scope}|{strategy}|{variant}|{cost}'
    grouped_fills, grouped_charges = defaultdict(list), defaultdict(list)
    for f in fills:
        grouped_fills[(f['sleeve'], f['sym'], f['pos_i'])].append(f)
    for sleeve, sym, pos_i, i, side, qty, close, paid in events:
        grouped_charges[(sleeve, sym, pos_i)].append(dict(i=i, time_ms=timestamp(book.t.iloc[i]),
            qty=qty, signed_qty=side * qty, close=close, cash_paid=paid))
    result = [] if trades is None else [serialize_trade(r, grouped_fills, grouped_charges, book.t, job_id)
                                       for r in trades.itertuples()]
    n, sum_r = len(result), sum(t['baseline_R'] for t in result)
    expected = published[(strategy, variant, str(cost), scope)]
    matches = n == int(expected['n']) and math.isclose(sum_r, float(expected['sumR']), rel_tol=1e-10, abs_tol=1e-9)
    return dict(job_id=job_id, strategy=strategy, scope=scope, family=family, variant=variant,
        cost_bp=cost, source_ref=REF, provenance=provenance, bar_ms=BAR_MS,
        timing='timestamps are UTC 4h candle opens; intrabar fill times and boundary eligibility are unknown',
        proxy_semantics='charge occurs before bar management; ordinary open entries charged on entry bar; trailing entries skipped',
        parity=dict(n=n, sumR=sum_r, published_n=int(expected['n']), published_sumR=float(expected['sumR']), matches=matches),
        trades=result)



def read_records(directory, exclude=None, diagnostics=None):
    """Yield complete records and byte offsets, including intact prefixes of interrupted files."""
    for path in sorted(directory.iterdir()):
        if path == exclude or not (path.name.endswith('.jsonl') or path.name.endswith('.jsonl.gz')):
            continue
        opener = gzip.open if path.suffix == '.gz' else open
        try:
            with opener(path, 'rt', encoding='utf-8') as stream:
                while True:
                    offset = stream.tell()
                    line = stream.readline()
                    if not line: break
                    if not line.endswith('\n'):
                        if diagnostics is not None: diagnostics.append({'file': str(path), 'reason': 'incomplete final line'})
                        break
                    try: record = json.loads(line)
                    except json.JSONDecodeError:
                        if diagnostics is not None: diagnostics.append({'file': str(path), 'reason': 'invalid JSON line'})
                        break
                    yield path, offset, record
        except (EOFError, OSError, UnicodeDecodeError, zlib.error) as exc:
            if diagnostics is not None: diagnostics.append({'file': str(path), 'reason': type(exc).__name__})
            continue


def published_matrix():
    matrix = {}
    for family in ('path4h', 'worst4h', 'h1'):
        with (INPUT / f'prot_{family}_rows.csv').open(newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                scope = row['mode' if family == 'h1' else 'period']
                job = f"{family}|{scope}|{row['strategy']}|{row['variant']}|{row['cost_bp']}"
                if job in matrix: raise ValueError(f'duplicate published job {job}')
                matrix[job] = row
    return matrix


@lru_cache(maxsize=1)
def expected_provenance():
    meta = json.loads((INPUT / 'prot_path4h_meta.json').read_text())
    h1_meta = json.loads((INPUT / 'prot_h1_meta.json').read_text())
    return {'backtest_sha256': hashlib.sha256((SOURCE / 'backtest.py').read_bytes()).hexdigest(),
            'data': {'data_sha256': meta['data_sha256'], 'data_long_sha256': meta['data_long_sha256'],
                     'data_long_1h_sha256': h1_meta['data_long_1h_sha256']}}


def validate_record(record, expected):
    try:
        trades = record['trades']; parity = record['parity']
        n = len(trades); total = sum(t['baseline_R'] for t in trades)
        provenance = expected_provenance()
        return (record['source_ref'] == REF and record['provenance']['backtest_sha256'] == provenance['backtest_sha256']
                and record['provenance']['data'] == provenance['data'] and parity['matches'] is True
                and n == parity['n'] == int(expected['n'])
                and math.isclose(total, float(expected['sumR']), rel_tol=1e-10, abs_tol=1e-9)
                and math.isclose(total, parity['sumR'], rel_tol=1e-10, abs_tol=1e-9))
    except (KeyError, TypeError, ValueError):
        return False


def semantic_key(job, pc, strategies):
    family, scope, key, variant, cost = job.split('|')
    cohort = {k: (sides, base) for k, sides, base in pc.COHORT}
    sides, base = cohort[key]
    management = strategies.merge_mgmt(key, pc.variants(key, base)[variant])
    return json.dumps([family, scope, key, sides, int(cost), management], sort_keys=True, separators=(',', ':'))


def reuse_record(record, job, expected):
    """Copy an equivalent book, relabel identities and independently check published parity."""
    reused = copy.deepcopy(record)
    reused['reused_from_job'] = record['job_id']
    family, scope, strategy, variant, cost = job.split('|')
    reused.update(job_id=job, family=family, scope=scope, strategy=strategy, variant=variant, cost_bp=int(cost))
    for trade in reused['trades']:
        trade['trade_id'] = f"{job}|{trade['sym']}|{trade['i_in']}"
    total = sum(t['baseline_R'] for t in reused['trades'])
    n = len(reused['trades'])
    reused['parity'] = dict(n=n, sumR=total, published_n=int(expected['n']),
        published_sumR=float(expected['sumR']),
        matches=n == int(expected['n']) and math.isclose(total, float(expected['sumR']), rel_tol=1e-10, abs_tol=1e-9))
    if not reused['parity']['matches']: raise ValueError(f'reused tape does not match published job {job}')
    return reused


def consolidate(directory, output):
    """Write valid complete records once; inventory missing/invalid IDs explicitly."""
    matrix = published_matrix(); seen = set(); invalid = []; diagnostics = []; duplicates = 0
    with gzip.open(output, 'wt', encoding='utf-8') as out:
        for path, _, record in read_records(directory, exclude=output, diagnostics=diagnostics):
            job = record.get('job_id', '<missing>')
            if job not in matrix or not validate_record(record, matrix[job]):
                invalid.append({'file': str(path), 'job_id': job}); continue
            if job in seen: duplicates += 1; continue
            seen.add(job)
            out.write(json.dumps(record, allow_nan=False, separators=(',', ':')) + '\n')
    missing = sorted(set(matrix) - seen)
    inventory = dict(expected=len(matrix), actual=len(seen), missing=missing, invalid=invalid, incomplete_files=diagnostics, duplicate_records=duplicates,
                     complete=not missing and not invalid)
    inventory_path = Path(str(output) + '.inventory.json')
    inventory_path.write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    return inventory

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('output', type=Path)
    ap.add_argument('--consolidate', type=Path, help='write canonical gzip and inventory from this directory; output is canonical gzip path')
    ap.add_argument('--families', default='path4h,worst4h,h1')
    ap.add_argument('--variants', default='BASE,P1_own')
    ap.add_argument('--strategies', default='')
    ap.add_argument('--costs', default='7,15,23')
    ap.add_argument('--skip-existing', type=Path, help='skip valid complete records in existing JSONL and JSONL.gz files under this directory')
    args = ap.parse_args()
    if args.consolidate:
        result = consolidate(args.consolidate, args.output)
        print(json.dumps({k: v for k, v in result.items() if k not in ('missing', 'invalid')}))
        return
    bt, pc, ph, events, source_hash = setup()
    import strategies
    families, variants, wanted = args.families.split(','), args.variants.split(','), args.strategies.split(',')
    # Published dataset fingerprints must match before replay; never silently compare a different book.
    meta = json.loads((INPUT / 'prot_path4h_meta.json').read_text())
    h1_meta = json.loads((INPUT / 'prot_h1_meta.json').read_text())
    actual = {'data_sha256': pc._hash(str(SOURCE / 'data' / '*_4h.csv')),
              'data_long_sha256': pc._hash(str(SOURCE / 'data_long' / '4h' / '*_4h.csv')),
              'data_long_1h_sha256': pc._hash(str(SOURCE / 'data_long' / '1h' / '*_1h.csv'))}
    expected = {k: meta[k] for k in ('data_sha256', 'data_long_sha256')}
    expected['data_long_1h_sha256'] = h1_meta['data_long_1h_sha256']
    if actual != expected: raise ValueError(f'published data hashes do not match: {actual}')
    py_hashes = verify_source()
    provenance = {'backtest_sha256': source_hash, 'source_python_manifest_sha256': hashlib.sha256(json.dumps(py_hashes, sort_keys=True).encode()).hexdigest(), 'data': actual}

    matrix = published_matrix()
    completed, equivalent = set(), {}
    if args.skip_existing:
        for previous, offset, record in read_records(args.skip_existing, exclude=args.output):
            job = record.get('job_id')
            if job not in matrix or not validate_record(record, matrix[job]): continue
            completed.add(job)
            try:
                key = semantic_key(job, pc, strategies)
            except (KeyError, ValueError):
                continue
            equivalent.setdefault(key, (previous, offset, job))
    # New books get individual compressed cache files, so later equivalent variants can reuse them
    # without retaining whole books in memory or waiting for the current output gzip to close.
    cache_dir = ROOT / '.git' / 'prot_replay_cache'
    cache_dir.mkdir(exist_ok=True)
    opener = gzip.open if args.output.suffix == '.gz' else open
    with opener(args.output, 'wt', encoding='utf-8') as out:
        for family in families:
            if family not in ('path4h', 'worst4h', 'h1'): raise ValueError(f'unknown family: {family}')
            with (INPUT / f'prot_{family}_rows.csv').open(newline='', encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
            scope_field = 'mode' if family == 'h1' else 'period'
            published = {(r['strategy'], r['variant'], r['cost_bp'], r[scope_field]): r for r in rows}
            for strategy, sides, base in pc.COHORT:
                if args.strategies and strategy not in wanted: continue
                for variant, mgmt in pc.variants(strategy, base).items():
                    if args.variants != 'all' and variant not in variants: continue
                    for cost in map(int, args.costs.split(',')):
                        for scope in (('path', 'h1', 'h1worst') if family == 'h1' else ('A', 'B', 'L')):
                            key = (strategy, variant, str(cost), scope)
                            if f'{family}|{scope}|{strategy}|{variant}|{cost}' in completed: continue
                            if key not in published: continue
                            job = f'{family}|{scope}|{strategy}|{variant}|{cost}'
                            sem = semantic_key(job, pc, strategies)
                            candidate = equivalent.get(sem)
                            record = None
                            if candidate:
                                path, offset, source_job = candidate
                                source_expected = matrix[source_job]
                                if (int(source_expected['n']) == int(published[key]['n']) and
                                    math.isclose(float(source_expected['sumR']), float(published[key]['sumR']), rel_tol=1e-10, abs_tol=1e-9)):
                                    reader = gzip.open if path.suffix == '.gz' else open
                                    with reader(path, 'rt', encoding='utf-8') as source:
                                        source.seek(offset)
                                        prior = json.loads(source.readline())
                                    if not validate_record(prior, source_expected): raise ValueError(f'invalid reused source {source_job}')
                                    record = reuse_record(prior, job, published[key])
                            if record is None:
                                record = replay(bt, pc, ph, events, published, family, strategy, sides, variant, mgmt, cost, scope, provenance)
                                cache = cache_dir / (hashlib.sha256(sem.encode()).hexdigest() + '.jsonl.gz')
                                with gzip.open(cache, 'wt', encoding='utf-8') as saved:
                                    saved.write(json.dumps(record, allow_nan=False, separators=(',', ':')) + '\n')
                                equivalent[sem] = (cache, 0, job)
                            out.write(json.dumps(record, allow_nan=False, separators=(',', ':')) + '\n'); out.flush()
                            print(record['job_id'], record['parity'], flush=True)
                            if not record['parity']['matches']: raise RuntimeError('published parity mismatch')


if __name__ == '__main__': main()
