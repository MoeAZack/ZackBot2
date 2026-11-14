"""Exact parity of the speed-patched runner against the frozen de27e871 outputs for the completed original8 arm.
Usage: python parity_speedpatch.py <clean_dir> <frozen_result_json> <frozen_trades_dir>"""
import sys, os, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_x2wide as X

dirp, ref_json, ref_dir = sys.argv[1:4]
ref = json.load(open(ref_json, encoding='utf-8'))['arms']['original8']['paths']
raw, coins, excluded = X.load_all(dirp)
weeks = (X.T1 - X.T0).total_seconds() / 604800
syms = X.ARMS['original8']; base = sorted(syms)
orders = [base] + [list(np.random.default_rng(20261112 + k).permutation(base)) for k in range(1, X.N_ORDERS)]
grid = pd.date_range(X.T0, X.T1, freq='h').values
anyel = np.zeros(len(grid), bool)
for s in syms: anyel |= pd.Series(raw[s]['elig'], index=raw[s]['t']).reindex(grid).fillna(False).values.astype(bool)
ew = anyel.sum() / 168
bad = 0; checked = 0
for mode in ('causal', 'worst'):
    for cb in (7, 15, 23):
        rows = []
        for k, od in enumerate(orders):
            tr, sk = X.simulate(coins, syms, od, cb, mode)
            sm = json.loads(json.dumps(X.summarize(tr, sk, weeks, ew), default=str)); rows.append(sm)
            if k == 0:
                new0 = pd.DataFrame([{kk: v for kk, v in t.items() if kk not in ('marks', 'cash')} for t in tr])
                old0 = pd.read_csv(os.path.join(ref_dir, f'original8.{mode}.{cb}bp.order0.trades.csv'))
                new0.to_csv('/tmp/_p.csv', index=False); new0 = pd.read_csv('/tmp/_p.csv')
                if not new0.equals(old0): bad += 1; print('TRADES DIFFER', mode, cb)
                sm0 = dict(sm); sm0['order'] = 0
                if sm0 != ref[mode][f'{cb}bp']['order0']: bad += 1; print('ORDER0 SUMMARY DIFFERS', mode, cb)
            checked += 1
        m = pd.DataFrame([{k: v for k, v in r.items() if not isinstance(v, dict)} for r in rows])
        for col, q in ref[mode][f'{cb}bp']['across_orders'].items():
            if q is None: continue
            if round(float(m[col].median()), 4) != q['median'] or round(float(m[col].quantile(.1)), 4) != q['p10']:
                bad += 1; print('ACROSS DIFFERS', mode, cb, col)
print('checked runs', checked, 'differences', bad)
