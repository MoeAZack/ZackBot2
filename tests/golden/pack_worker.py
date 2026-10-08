"""Golden pack worker: runs every runnable case x legacy adapter ONCE and writes the canonical traces + wall times as JSON.

    python tests/golden/pack_worker.py OUT.json [--tier core|extended]

--tier runs only the cases TIERS.json puts in that tier (goldenlib.tiers; a tier manifest that does not place every case in
exactly one tier makes the worker fail). Without --tier: the whole pack.

test_golden.py starts it through goldenlib.deadline.run with the HARD_STOP_S deadline (the whole tree is killed on expiry),
so no adapter code runs in the pytest process and a hung adapter costs at most the hard stop. Adapters only ever see
adapters.blind(case) (no expectation, no known divergences); legacy_backtest is cheap, so it also runs on the full case and
both traces are reported for the isolation test. Transport only: no comparison happens here, non-finite floats are written
as NaN / Infinity tokens and judged by compare.py in the parent.
"""
import json, os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from goldenlib import REPO_ROOT, adapters, schema, tiers  # noqa: E402

LEGACY = ('legacy_backtest', 'legacy_engine')
RUNNABLE = ('required', 'known_divergence')


def _trace(ad, case):
    tr = adapters.get(ad).run(case)
    return dict(trades=tr.trades, final=tr.final)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--tier', choices=tiers.TIERS)
    a = ap.parse_args(argv)
    out_path = os.path.abspath(a.out)
    os.chdir(REPO_ROOT)
    t_all = time.perf_counter()
    runs = []
    cases = schema.load_all()
    if a.tier:
        tier_of = tiers.load([c['id'] for c in cases])
        cases = [c for c in cases if tier_of[c['id']] == a.tier]
    for c in cases:
        for ad in LEGACY:
            if schema.status(c, ad) not in RUNNABLE:
                continue
            rec = dict(id=c['id'], adapter=ad)
            t0 = time.perf_counter()
            try:
                rec['trace'] = _trace(ad, adapters.blind(c))
                if ad == 'legacy_backtest':
                    rec['trace_full'] = _trace(ad, c)
            except Exception as e:                     # reported per pair; the parent fails that pair's test with it
                rec['error'] = f'{type(e).__name__}: {e}\n' + traceback.format_exc()[-2000:]
            rec['seconds'] = time.perf_counter() - t0
            runs.append(rec)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(dict(runs=runs, tier=a.tier, wall=time.perf_counter() - t_all), f, default=float)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
