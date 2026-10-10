"""R4-0 backtest readiness report (Codex #53 comment 6094720765) - SKELETON, not run on any data.

One concise report: SHA, digests, test counts, M3 parity, accounting sample, determinism hashes, pilot result,
limitations, READY / NOT READY. Seven items; READY only when every item is PASS. Missing evidence = MISSING, never
assumed. Items are filled from evidence files the official Windows run produces; this module computes only what can
be checked mechanically from them (determinism across runs from sealed progress records) and refuses to guess the
rest.

  python tools/research/r4_readiness.py --evidence r40_inputs.json --out research_evidence/runs/r4/R4-0_readiness.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import progress as PG                                                                       # noqa: E402

FORMAT = 'zb-r4-0-readiness/1'
ITEMS = {
    1: 'merge base: #51 + final #52 merged; #53 rebuilt on that exact master',
    2: 'data identity / chronology on Windows (103,659-file manifest, universe digest, overlap/gap/malformed refusal, '
       'tz, listing/delisting, universe-as-of, symbol identity, funding alignment, PIT-dated classification)',
    3: 'no look-ahead known-answer tests; holdout unread',
    4: 'execution / accounting: representative long + short trades, exact reconciliation; flat funding repro-only',
    5: 'strategy source/config bound by digest, rules == engine, M3 reproduced trade-for-trade',
    6: 'determinism: two fresh-process runs + relocated checkout identical; atomic, interruption-safe, '
       'secret-clean reports; status exposes operations only',
    7: 'small spent/development-only Windows pilot with independently calculated sample trades',
}
STATUSES = ('PASS', 'FAIL', 'MISSING')


class ReadinessError(ValueError):
    pass


def sealed_artifacts(path: str) -> dict:
    """{artifact path: sha256} from a verified, sealed progress file."""
    recs = PG.verify(path)
    if not recs or recs[-1]['event'] != 'run_sealed':
        raise ReadinessError(f'{path}: progress record is not sealed')
    return {a['path']: a['sha256'] for a in recs[-1]['artifacts']}


def determinism(progress_files) -> dict:
    """Item 6 (mechanical part): >= 2 sealed runs (fresh processes / relocated checkout) with the same run id and
    identical artifact hashes."""
    if len(progress_files) < 2:
        return {'status': 'MISSING', 'detail': 'need >= 2 sealed progress files'}
    runs = [(os.path.basename(p), sealed_artifacts(p)) for p in progress_files]
    ids = {r for r, _ in runs}
    same = len(ids) == 1 and all(a == runs[0][1] for _, a in runs[1:])
    return {'status': 'PASS' if same else 'FAIL', 'runs': len(runs), 'run_files': sorted(ids),
            'artifact_sha256': runs[0][1]}


DIGESTS = ('dataset', 'universe', 'classification')
RESULTS = ('m3_parity', 'accounting_sample', 'pilot')        # each {'status': PASS|FAIL|MISSING, 'evidence': ...}
TESTS = ('passed', 'failed')


def _result(v, what):
    if v is None:
        return {'status': 'MISSING'}
    if not isinstance(v, dict) or v.get('status') not in STATUSES:
        raise ReadinessError(f'{what}: needs a status in {STATUSES}')
    return dict(v)


def build(ev: dict) -> dict:
    """`ev` = {'head': 40-hex, 'digests': {dataset, universe, classification}, 'tests': {passed, failed},
    'm3_parity' / 'accounting_sample' / 'pilot': {'status', ...}, 'items': {n: {'status', ...}},
    'progress_files': [...], 'limitations': [...]}. NOT READY whenever any field is missing or not PASS."""
    missing = []
    head = ev.get('head')
    if not (isinstance(head, str) and PG.HEX40.match(head)):
        missing.append('head')
    dig = {k: (ev.get('digests') or {}).get(k) for k in DIGESTS}
    missing += [f'digest.{k}' for k, v in dig.items() if not (isinstance(v, str) and PG.HEX64.match(v))]
    tests = {k: (ev.get('tests') or {}).get(k) for k in TESTS}
    if any(type(v) is not int or v < 0 for v in tests.values()) or tests['failed'] != 0 or not tests['passed']:
        missing.append('tests')
    res = {k: _result(ev.get(k), k) for k in RESULTS}
    missing += [k for k, v in res.items() if v['status'] != 'PASS']
    items = {str(n): {'item': ITEMS[n], 'status': 'MISSING'} for n in ITEMS}
    for k, v in (ev.get('items') or {}).items():
        if str(k) not in items:
            raise ReadinessError(f'item {k!r}: unknown item')
        items[str(k)].update(_result(v, f'item {k}'))
    det = determinism(ev.get('progress_files') or [])
    if items['6']['status'] == 'PASS' and det['status'] != 'PASS':
        items['6']['status'] = det['status']               # the hash check can only downgrade a claimed PASS
    items['6']['determinism'] = det
    missing += [f'item.{n}' for n, i in items.items() if i['status'] != 'PASS']
    return {'format': FORMAT, 'head': head, 'digests': dig, 'tests': tests, **res, 'items': items,
            'determinism_sha256': det.get('artifact_sha256', {}), 'limitations': list(ev.get('limitations', [])),
            'not_passing': missing, 'verdict': 'NOT READY' if missing else 'READY'}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='R4-0 readiness report (skeleton)')
    ap.add_argument('--evidence', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    try:
        with open(a.evidence, encoding='utf-8') as f:
            doc = build(json.load(f))
    except (OSError, ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    tmp = a.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, a.out)                                 # atomic
    print(doc['verdict'])
    return 0 if doc['verdict'] == 'READY' else 1


if __name__ == '__main__':
    sys.exit(main())
