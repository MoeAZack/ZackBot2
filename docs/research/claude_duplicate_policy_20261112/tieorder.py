# Robustness: the same-bar tie order between coins is arbitrary. Re-run every policy under 40 random coin orders.
import sys; sys.path.insert(0, '/tmp/claude-0/work')
import overlay_v2_corrected as V, numpy as np, pandas as pd, os
V.O = os.environ.get('TAPES', V.O); TAG = os.environ.get('TAG', 'path')
rows = []
for book, src in [('A4_4h6','15'),('A4_4h6','long'),('A1','15'),('A1','long'),('A2_OLD3','15'),('A2_OLD3','long')]:
    cfg = V.BOOKS[book]; r = cfg['risk']
    for bp in (7, 15, 23):
        tr = V.load(cfg['sleeves'], src, bp); tr = tr[tr.t_in < '2026-09-28']
        syms = sorted(tr.sym.unique())
        for seed in range(40):
            perm = dict(zip(syms, np.random.default_rng(seed).permutation(len(syms)))) if seed else {s: i for i, s in enumerate(syms)}
            t2 = tr.assign(sym_rank=tr.sym.map(perm))
            for pn, pol, cap in [('stack','stack',None),('refuse','refuse',None),('cap1.5x','cap',1.5*r),('cap2x','cap',2*r)]:
                s = V.sim(t2, cfg['sleeves'], r, cfg['max_pos'], pol, cap)
                rows.append(dict(book=book, data=src, bp=bp, seed=seed, policy=pn, ret=s['ret_pct'], dd=s['maxdd_pct'], trades=s['trades']))
df = pd.DataFrame(rows); df.to_csv(f'tieorder_{TAG}.csv', index=False)
p = df.pivot_table(index=['book','data','bp','seed'], columns='policy', values='ret')
for c in ['refuse','cap1.5x','cap2x']: p[c+'-stack'] = p[c] - p['stack']
g = p.groupby(level=[0,1,2])
out = pd.concat({'stack_med': g['stack'].median(), 'stack_p10': g['stack'].quantile(.1), 'stack_p90': g['stack'].quantile(.9),
                 **{f'{c}-stack_med': g[c+'-stack'].median() for c in ['refuse','cap1.5x','cap2x']},
                 **{f'{c}_wins%': g[c+'-stack'].apply(lambda x: (x > 0).mean()*100) for c in ['refuse','cap2x']}}, axis=1).round(1)
pd.set_option('display.width', 250); print(out.to_string())
d = df.pivot_table(index=['book','data','bp','seed'], columns='policy', values='dd').groupby(level=[0,1,2]).median().round(1); print(d.to_string())
