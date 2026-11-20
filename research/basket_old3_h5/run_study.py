"""Driver for the single 720-cell BASKET-OLD3-H5 study. Usage:
python -I run_study.py <cohort9_1h dir> <input_manifest.json> <exchange_rules.json> <out dir>
Verifies every pinned hash before any strategy count; writes tapes, cells and the descriptive label."""
import json, os, sys, time, hashlib, pickle
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import basket as K

MANIFEST_SHA = 'c11ed2566727c38d0d9236d7290a01aabb4a53a0f3b15388ea8fed7ba8909351'
RULES_SHA = 'd1ef2a9c60db9858774d68903648c334ca807a66c23dc85cce13fd8add96d2af'


def q(v, p): return float(np.quantile(v, p)) if len(v) else None


def main(data, manifest, rules_p, out):
    os.makedirs(out, exist_ok=True)
    if K.sha(manifest) != MANIFEST_SHA: raise SystemExit('manifest hash mismatch')
    if K.sha(rules_p) != RULES_SHA: raise SystemExit('rules hash mismatch')
    man = json.load(open(manifest, encoding='utf-8'))['cohort9_1h']
    for s in K.COINS:
        if K.sha(os.path.join(data, f'{s}.csv')) != man[s]['input_sha256']: raise SystemExit(f'{s} data hash mismatch')
    rules = json.load(open(rules_p, encoding='utf-8'))
    raw1 = K.load_1h(data); raw4 = {s: K.to_4h(d) for s, d in raw1.items()}
    facts = K.check_window(raw1, raw4)
    closes1h = {s: dict(zip(d.t, d.c)) for s, d in raw1.items()}
    hours = list(pd.date_range(K.START, K.END - K.H1, freq='h'))
    for s in K.COINS:
        missing = [h for h in hours if h not in closes1h[s]]
        if missing: facts.setdefault('missing_1h_in_window', {})[s] = [str(m) for m in missing]
    json.dump(dict(facts=facts, start=str(K.START), end=str(K.END), split=str(K.SPLIT), half=str(K.HALF)),
              open(os.path.join(out, 'window.json'), 'w', encoding='utf-8'), indent=1)
    if 'missing_1h_in_window' in facts: raise SystemExit('1h hours missing inside the window; see window.json')
    K.register_h5()
    tapes, tape_meta = {}, []
    for sl in K.SLEEVES:
        raw = raw1 if sl[3] == '1h' else raw4
        for c in K.COSTS:
            for p in K.PATHS:
                t0 = time.time()
                lots, feas, _ = K.native_tape(raw, sl, c, p, rules, sl[3])
                tapes[(sl[0], c, p)] = lots
                tf_fund = sum(l['native_funding'] for l in lots if l['closed'])
                tg_fund = sum(-a for l in lots if l['closed'] for _, k, a in K.target_cash_events(l, closes1h, c) if k == 'funding')
                tape_meta.append(dict(sleeve=sl[0], cost=c, path=p, lots=len(lots), closed=sum(l['closed'] for l in lots),
                                      open_at_end=sum(not l['closed'] for l in lots),
                                      partial_lots=sum(len(l['exits']) > 1 for l in lots),
                                      exchange_skips=sum(feas['skipped'].values()) if isinstance(feas.get('skipped'), dict) else None,
                                      native_funding_closed=tf_fund, target_funding_closed=tg_fund,
                                      native_pnl_closed=sum(l['native_pnl'] for l in lots if l['closed']),
                                      first_entry=str(min(l['t_in'] for l in lots)) if lots else None, secs=round(time.time() - t0)))
                print('tape', tape_meta[-1], flush=True)
    pickle.dump(tapes, open(os.path.join(out, 'tapes.pkl'), 'wb'))
    pd.DataFrame(tape_meta).to_csv(os.path.join(out, 'tapes.csv'), index=False, encoding='utf-8')
    cells, rows = {}, []
    for c in K.COSTS:
        for p in K.PATHS:
            lots = [l for sl in K.SLEEVES for l in tapes[(sl[0], c, p)]]
            for seed in range(K.N_ORDERS):
                for book in K.BOOKS:
                    r = K.replay(lots, book, seed, closes1h, c, hours)
                    recs = r.pop('records')
                    if seed == 0:
                        pd.DataFrame(recs).to_csv(os.path.join(out, f'records_{book}_{c}_{p}_seed0.csv'), index=False, encoding='utf-8')
                    cells[(book, c, p, seed)] = r
                    rows.append(dict(book=book, cost=c, path=p, seed=seed, order=json.dumps(K.orders(seed)),
                                     **{k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in r.items()}))
                if seed == 0: print('seed0', c, p, {b: round(cells[(b, c, p, 0)]['net_usd'], 2) for b in K.BOOKS}, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(out, 'cells.csv'), index=False, encoding='utf-8')
    inc = []
    for c in K.COSTS:
        for p in K.PATHS:
            for base in ('OLD3', 'H5'):
                d = [(cells[('COMBINED', c, p, s)]['net_usd'] - cells[(base, c, p, s)]['net_usd']) / K.CAPITAL * 100
                     for s in range(K.N_ORDERS)]
                inc.append(dict(cost=c, path=p, vs=base, wins=sum(x > 0 for x in d), median_pp=q(d, .5), p10_pp=q(d, .1),
                                p90_pp=q(d, .9), min_pp=min(d), max_pp=max(d)))
    label, detail = K.lead_label(cells)
    summ = []
    for b in K.BOOKS:
        for c in K.COSTS:
            for p in K.PATHS:
                v = [cells[(b, c, p, s)] for s in range(K.N_ORDERS)]
                summ.append(dict(book=b, cost=c, path=p, seed0_net_pct=v[0]['net_pct'],
                                 median_net_pct=q([x['net_pct'] for x in v], .5), p10_net_pct=q([x['net_pct'] for x in v], .1),
                                 p90_net_pct=q([x['net_pct'] for x in v], .9),
                                 median_marked_dd=q([x['marked_dd_pct'] for x in v], .5),
                                 median_entries_week=q([x['entries_per_week'] for x in v], .5),
                                 median_closes=q([x['closes'] for x in v], .5)))
    json.dump(dict(label=label, label_detail=detail, incremental=inc, summary=summ, tapes=tape_meta),
              open(os.path.join(out, 'result.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print(label, json.dumps(detail), flush=True)


if __name__ == '__main__':
    main(*sys.argv[1:5])
