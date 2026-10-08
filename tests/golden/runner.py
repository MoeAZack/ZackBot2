"""Golden-case runner CLI (review aid only - it never writes into cases/, MANIFEST.json or CORRECTIONS.json).

    python tests/golden/runner.py                      # every case x every legacy adapter: PASS / KNOWN / FAIL
    python tests/golden/runner.py G-TIME-L-01 --adapter legacy_engine --trace
    python tests/golden/runner.py --propose            # traces + diffs to dev_out/golden_proposals/ for review

There is deliberately no regenerate-and-overwrite command (design section 6, rule 4): an `expect` block changes only by
hand, through a CORRECTIONS.json entry.
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from goldenlib import REPO_ROOT, adapters, compare, schema  # noqa: E402

LEGACY = ('legacy_backtest', 'legacy_engine')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('ids', nargs='*')
    ap.add_argument('--adapter', choices=LEGACY)
    ap.add_argument('--trace', action='store_true')
    ap.add_argument('--propose', action='store_true')
    a = ap.parse_args(argv)
    os.chdir(REPO_ROOT)
    cases = [c for c in schema.load_all() if not a.ids or c['id'] in a.ids]
    out_dir = os.path.join(REPO_ROOT, 'dev_out', 'golden_proposals')
    bad = 0
    for c in cases:
        for ad in ([a.adapter] if a.adapter else LEGACY):
            st = schema.status(c, ad)
            if st == 'not_applicable':
                print(f"{c['id']:<26} {ad:<16} n/a"); continue
            t0 = time.perf_counter()
            tr = adapters.get(ad).run(c)
            mm = compare.compare(c, tr, ad)
            kd = next((d for d in c['known_divergences'] if d['adapter'] == ad), None)
            if not mm:
                verdict = 'PASS' if st == 'required' else 'XPASS (known divergence fixed: remove it + ledger entry)'
            elif kd and compare.same_mismatches(c, kd['observed'], mm):
                verdict = f"KNOWN {kd['ticket']}/{kd['finding']}"
            else:
                verdict = 'FAIL'
            bad += verdict.startswith(('FAIL', 'XPASS'))
            print(f"{c['id']:<26} {ad:<16} {verdict}  ({time.perf_counter() - t0:.1f}s)")
            if a.trace or verdict == 'FAIL':
                print(compare.report(c, ad, mm, tr) if mm else '  ' + json.dumps(tr.as_dict(), default=float))
            if a.propose:
                os.makedirs(out_dir, exist_ok=True)
                with open(os.path.join(out_dir, f"{c['id']}.{ad}.json"), 'w') as f:
                    json.dump(dict(trace=tr.as_dict(), mismatches=mm), f, indent=1, default=float)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
