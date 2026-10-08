"""Golden pack worker: runs every runnable case x legacy adapter ONCE and writes the canonical traces + wall times as JSON.

    python tests/golden/pack_worker.py OUT.json

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

from goldenlib import REPO_ROOT, adapters, schema  # noqa: E402

LEGACY = ('legacy_backtest', 'legacy_engine')
RUNNABLE = ('required', 'known_divergence')


def _trace(ad, case):
    tr = adapters.get(ad).run(case)
    return dict(trades=tr.trades, final=tr.final)


def main(argv):
    out_path = argv[0]
    os.chdir(REPO_ROOT)
    t_all = time.perf_counter()
    runs = []
    for c in schema.load_all():
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
        json.dump(dict(runs=runs, wall=time.perf_counter() - t_all), f, default=float)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
