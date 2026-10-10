"""R4-0 backtest readiness report (Codex #53 comment 6094720765). Reads no market data and opens no holdout.

One concise report: exact SHA, dataset / universe / classification / prereg digests, test counts, M3 parity,
accounting sample, determinism hashes, pilot result, limitations and a single READY / NOT READY. Seven items; READY
only when every item is PASS. Missing evidence = MISSING, never assumed.

`offline(repo)` computes what can be checked now from the committed tree alone: HEAD + clean tree, the R4 gate
(pins + ancestry, classification), the reproduction-only and archive manifest digests (recomputed), the universe
digest (recomputed) and its manifest binding, the prereg SHA-256, that the family ledger holds no holdout record
(holdout unread), and - with `--pytest` - the R4-0 test-suite counts. Everything the official Windows run must still
produce (archive re-verification against the store, classification v2, M3 parity, independent accounting sample,
two-process / relocated determinism, development pilot) stays MISSING and the verdict NOT READY.

  python tools/research/r4_readiness.py --out research_evidence/runs/r4/R4-0_readiness.json [--pytest]
         [--evidence r40_inputs.json]          (Windows-run evidence: m3_parity / accounting_sample / pilot /
                                                items / final_files, merged over the offline inputs)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import progress as PG                                                                       # noqa: E402

FORMAT = 'zb-r4-0-readiness/2'
ITEMS = {
    1: 'accepted foundation: #51 + final #52 (+ the #56 overlap follow-up) merged; #53 rebuilt on that exact master, '
       'clean tree',
    2: 'data identity / chronology on Windows (103,659-file manifest, universe digest, overlap/gap/malformed refusal, '
       'tz, listing/delisting, universe-as-of, symbol identity, funding alignment, PIT-dated classification)',
    3: 'no look-ahead known-answer tests (future-bar refusal, as-of membership, warm-up, purge/embargo, closed-trade '
       'truncation, perturbation); holdout unread',
    4: 'execution / accounting: representative long + short trades independently checked, exact reconciliation; '
       'legacy flat funding reproduction-only',
    5: 'strategy source/config bound by digest, rules == engine, M3 reproduced trade-for-trade',
    6: 'determinism: two fresh-process runs + relocated checkout identical; atomic, interruption-safe, '
       'secret-clean reports; status exposes operations only',
    7: 'small spent/development-only Windows pilot with independently calculated sample trades',
}
STATUSES = ('PASS', 'FAIL', 'MISSING')
DIGESTS = ('dataset', 'universe', 'classification', 'prereg')
RESULTS = ('m3_parity', 'accounting_sample', 'pilot')        # each {'status': PASS|FAIL|MISSING, ...}
TESTS = ('passed', 'failed')
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
R40_TESTS = ('tests/test_res01_manifest_ledger.py', 'tests/test_res02_universe.py', 'tests/test_res03_core.py',
             'tests/test_res04_prep.py', 'tests/test_res04_progress.py')
# Offline known-answer / guard checks already in the suite (names only; their pass/fail comes from the test counts).
OFFLINE_CHECKS = {
    3: ['test_res03_core: future-bar refusal, as-of membership, perturbation, purge/embargo (R3)',
        'test_res04_prep::test_warmup_known_answer',
        'test_res04_prep::test_truncating_the_future_never_changes_a_closed_trade',
        'test_res04_prep::test_m3_runs_only_in_the_sandbox_with_a_perturbed_attestation'],
    4: ['test_res04_prep::test_legacy_flat_funding_adapter_cannot_enter_primary_results',
        'test_res04_prep::test_harness_matches_an_independent_m3_order_simulator (synthetic)'],
    6: ['test_res04_prep::test_m3_reports_are_written_atomically',
        'test_res04_progress (atomic live snapshot, immutable sealed final, no outcome leak, wall excluded)'],
}
REMAINING = {
    2: 'Windows: re-verify the 103,659-file archive manifest and the universe digest against the store; '
       'classification v2 (sourced, PIT-dated, Cowork-verified, Codex-bound) and its new universe artifact',
    4: 'independent accounting calculator over representative long + short trades (Codex may assign to Cowork)',
    5: 'official Windows M3 reproduction (run_r4.py m3) = REPRODUCED for base and 2x',
    6: 'two fresh-process Windows runs + a relocated checkout with identical sealed finals',
    7: 'development pilot on a small spent slice with independently calculated sample trades',
}


class ReadinessError(ValueError):
    pass


def atomic_write(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    PG.atomic_replace(path, json.dumps(doc, indent=1, sort_keys=True).encode('utf-8') + b'\n')


# ------------------------------------------------------------------ determinism from sealed finals
def sealed_artifacts(path: str) -> tuple[str, dict]:
    """(final digest, {artifact path: sha256}) from a verified, sealed final record."""
    doc = PG.read(path)
    if doc is None or doc['kind'] != 'final' or doc['state'] != 'sealed':
        raise ReadinessError(f'{path}: not a sealed final record')
    return doc['digest'], {a['path']: a['sha256'] for a in doc['artifacts']}


def determinism(final_files) -> dict:
    """Item 6 (mechanical part): >= 2 sealed finals (fresh processes / relocated checkout) with the same record
    digest (identity, run id, stages) and identical artifact hashes."""
    if len(final_files) < 2:
        return {'status': 'MISSING', 'detail': 'need >= 2 sealed final records'}
    runs = [sealed_artifacts(p) for p in final_files]
    same = all(r == runs[0] for r in runs[1:])
    return {'status': 'PASS' if same else 'FAIL', 'runs': len(runs), 'final_digest': runs[0][0],
            'artifact_sha256': runs[0][1]}


def _result(v, what):
    if v is None:
        return {'status': 'MISSING'}
    if not isinstance(v, dict) or v.get('status') not in STATUSES:
        raise ReadinessError(f'{what}: needs a status in {STATUSES}')
    return dict(v)


def build(ev: dict) -> dict:
    """`ev` = {'head': 40-hex, 'digests': {dataset, universe, classification, prereg}, 'tests': {passed, failed},
    'm3_parity' / 'accounting_sample' / 'pilot': {'status', ...}, 'items': {n: {'status', ...}},
    'final_files': [...], 'limitations': [...]}. NOT READY whenever any field is missing or not PASS."""
    missing = []
    head = ev.get('head')
    if not (isinstance(head, str) and HEX40.match(head)):
        missing.append('head')
    dig = {k: (ev.get('digests') or {}).get(k) for k in DIGESTS}
    missing += [f'digest.{k}' for k, v in dig.items() if not (isinstance(v, str) and HEX64.match(v))]
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
    det = determinism(ev.get('final_files') or [])
    if items['6']['status'] == 'PASS' and det['status'] != 'PASS':
        items['6']['status'] = det['status']               # the hash check can only downgrade a claimed PASS
    items['6']['determinism'] = det
    missing += [f'item.{n}' for n, i in items.items() if i['status'] != 'PASS']
    return {'format': FORMAT, 'head': head, 'digests': dig, 'tests': tests, **res, 'items': items,
            'determinism_sha256': det.get('artifact_sha256', {}), 'limitations': list(ev.get('limitations', [])),
            'not_passing': missing, 'verdict': 'NOT READY' if missing else 'READY'}


# ------------------------------------------------------------------ offline inputs
def _git(repo, *a) -> str:
    return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


def pytest_counts(repo: str, files=R40_TESTS) -> dict:
    """Run the R4-0 suite once in a subprocess; parse its final summary line."""
    p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *files], cwd=repo,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    line = (p.stdout.strip().splitlines() or [''])[-1]
    n = lambda w: sum(int(x) for x in re.findall(rf'(\d+) {w}', line))
    return {'passed': n('passed'), 'failed': n('failed') + n('errors?'), 'summary': line, 'returncode': p.returncode}


def offline(repo: str, *, run_tests: bool = False) -> dict:
    """The inputs computable now from the committed tree, plus per-item offline detail."""
    import ledger as L                                                                      # noqa: E402
    import m3_repro as X                                                                    # noqa: E402
    import manifest as M                                                                    # noqa: E402
    import pit as P                                                                         # noqa: E402
    import run_r4 as G                                                                      # noqa: E402
    import universe as U                                                                    # noqa: E402
    head = _git(repo, 'rev-parse', 'HEAD')
    g = json.loads(G._load(repo, G.GATE))
    gate = G.gate(repo)
    pins = G.pin_failures(repo, g)
    detail, lim = {}, []
    repro = M.load(os.path.join(repo, X.REPRO_MANIFEST))
    P.Dataset(repro, repo, None)                            # metadata only: refuses overlapping coverage
    detail['repro_manifest'] = {'id': repro['manifest_id'], 'digest': repro['digest'],
                                'pinned': repro['digest'] == X.REPRO_DIGEST, 'files': len(repro['files']),
                                'overlaps': 0, 'promotion_eligible': M.promotion_eligible(repro)}
    pre_raw = G._load(repo, g['prereg'])
    pre = json.loads(pre_raw)
    prereg_sha = hashlib.sha256(pre_raw).hexdigest()
    arch = M.load(os.path.join(repo, 'research_evidence', 'manifests', 'binance-um-archive-v1.json.gz'))
    uni = json.loads(G._load(repo, G.UNIVERSE))
    u_ok = U.digest_of(uni) == uni['digest'] and uni['manifest_digest'] == arch['digest']
    detail['archive_manifest'] = {'digest': arch['digest'], 'files': len(arch['files']),
                                  'matches_prereg': arch['digest'] == pre['data']['manifest_digest']}
    detail['universe'] = {'id': uni['universe_id'], 'digest': uni['digest'], 'recomputed_ok': u_ok,
                          'matches_prereg': uni['digest'] == pre['data']['universe_digest']}
    cls = (g.get('classification') or {}).get('digest')
    led = os.path.join(repo, g['ledger'])
    recs = L._check_state(led).get(pre['family'], []) if os.path.exists(led) else []
    holdout_unread = not any(r.get('split') == 'holdout' for r in recs)
    registered = any(r['detail'].get('prereg_sha256') == prereg_sha for r in recs)
    tests = pytest_counts(repo) if run_tests else None
    clean = not any(f.startswith('G1') for f in gate)
    items = {}
    pin_only_unmerged = pins and all('not merged into' in f for f in pins)
    items['1'] = {'status': 'PASS' if clean and not pins else ('MISSING' if clean and pin_only_unmerged else 'FAIL'),
                  'pins': g['pins'], 'pin_failures': pins, 'clean_tree': clean}
    items['2'] = {'status': 'MISSING', 'offline': {k: detail[k] for k in ('repro_manifest', 'archive_manifest',
                                                                          'universe')},
                  'classification': cls, 'remaining': REMAINING[2]}
    t_ok = bool(tests) and tests['failed'] == 0 and tests['passed'] > 0
    items['3'] = {'status': 'PASS' if t_ok and holdout_unread else 'MISSING', 'holdout_unread': holdout_unread,
                  'offline_checks': OFFLINE_CHECKS[3]}
    for n in (4, 5, 6, 7):
        items[str(n)] = {'status': 'MISSING', 'remaining': REMAINING[n]}
        if n in OFFLINE_CHECKS:
            items[str(n)]['offline_checks'] = OFFLINE_CHECKS[n]
    if not registered:
        lim.append('preregistration v1 is UNREGISTERED (G3): no strategy run may start')
    if not (isinstance(cls, str) and HEX64.match(cls)):
        lim.append(f'classification digest {cls}: classification v2 not built (G5)')
    lim += [f'gate: {f}' for f in gate]
    return {'head': head, 'digests': {'dataset': arch['digest'], 'universe': uni['digest'],
                                      'classification': cls, 'prereg': prereg_sha},
            'tests': ({'passed': tests['passed'], 'failed': tests['failed']} if tests else None),
            'tests_summary': tests['summary'] if tests else 'not run (pass --pytest)',
            'items': items, 'limitations': lim, 'offline': detail}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='R4-0 readiness report')
    ap.add_argument('--out', required=True)
    ap.add_argument('--evidence', help='Windows-run evidence JSON merged over the offline inputs')
    ap.add_argument('--pytest', action='store_true', help='run the R4-0 test suite once for the counts')
    ap.add_argument('--repo', default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    a = ap.parse_args(argv)
    try:
        ev = offline(a.repo, run_tests=a.pytest)
        if a.evidence:
            with open(a.evidence, encoding='utf-8') as f:
                extra = json.load(f)
            items = dict(ev['items'])
            for k, v in (extra.pop('items', None) or {}).items():
                items[str(k)] = {**items.get(str(k), {}), **v}
            ev.update(extra, items=items)
        doc = build(ev)
        doc['tests_summary'], doc['offline'] = ev.get('tests_summary'), ev.get('offline')
    except (OSError, ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    atomic_write(a.out, doc)
    print(doc['verdict'])
    return 0 if doc['verdict'] == 'READY' else 1


if __name__ == '__main__':
    sys.exit(main())
