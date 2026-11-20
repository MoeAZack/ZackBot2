"""Driver: 24 admission-aware control cases. Usage:
python run_controls.py <cohort9_1h dir> <input_manifest.json> <exchange_rules.json> <basket results dir> <out dir>"""
import json, os, sys, time
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controls as C
K = C.K
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'basket_old3_h5'))
import run_study as RS


def main(data, manifest, rules_p, basket_dir, out):
    os.makedirs(out, exist_ok=True)
    if K.sha(manifest) != RS.MANIFEST_SHA or K.sha(rules_p) != RS.RULES_SHA: raise SystemExit('hash mismatch')
    man = json.load(open(manifest, encoding='utf-8'))['cohort9_1h']
    for s in K.COINS:
        if K.sha(os.path.join(data, f'{s}.csv')) != man[s]['input_sha256']: raise SystemExit(f'{s} hash mismatch')
    rules = json.load(open(rules_p, encoding='utf-8'))
    raw1 = K.load_1h(data); raw4 = {s: K.to_4h(d) for s, d in raw1.items()}
    K.check_window(raw1, raw4)
    closes1h = {s: dict(zip(d.t, d.c)) for s, d in raw1.items()}
    hours = list(pd.date_range(K.START, K.END - K.H1, freq='h'))
    K.register_h5()
    rows, cmp_rows = [], []
    for sl in C.CONTROLS:
        raw = raw1 if sl[3] == '1h' else raw4
        for c in K.COSTS:
            for p in K.PATHS:
                for oname, order in C.ORDERS.items():
                    t0 = time.time()
                    lots, cv = C.native_capacity_run(raw, sl, c, p, rules, order, guards=True)
                    ulots, _ = C.native_capacity_run(raw, sl, c, p, rules, order, guards=False)
                    rep, recs = C.case_report(sl[0], lots, cv, c, closes1h, hours, ulots)
                    pd.DataFrame(recs).to_csv(os.path.join(out, f'records_{sl[0]}_{c}_{p}_{oname}.csv'), index=False, encoding='utf-8')
                    row = dict(sleeve=sl[0], cost=c, path=p, order=oname, **{k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v) for k, v in rep.items()})
                    rows.append(row)
                    if oname == 'alpha':
                        book = 'H5' if sl[0] == 'H5' else 'OLD3'
                        cm = C.compare_overlay(sl[0], recs, os.path.join(basket_dir, f'records_{book}_{c}_{p}_seed0.csv'))
                        cmp_rows.append(dict(sleeve=sl[0], cost=c, path=p, **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in cm.items()}))
                    print(sl[0], c, p, oname, round(rep['net_usd'], 2), rep['admitted'], 'nonparity', rep['nonparity_refusals'],
                          'guard_changed', rep['guard_changed_entries'], f'{time.time() - t0:.0f}s', flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(out, 'controls.csv'), index=False, encoding='utf-8')
    pd.DataFrame(cmp_rows).to_csv(os.path.join(out, 'overlay_compare.csv'), index=False, encoding='utf-8')


if __name__ == '__main__':
    main(*sys.argv[1:6])
