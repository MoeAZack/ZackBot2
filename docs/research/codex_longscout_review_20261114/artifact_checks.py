"""Check frozen blobs, raw-input hashes and claimed gates from committed all-order cash tables."""
from pathlib import Path
import subprocess,json,hashlib
import pandas as pd
import numpy as np
R=Path(r'F:\ZackBot2_data\LONGSCOUT-REVIEW-20261114')
D=R/'docs/research/longscout_v1'; O=Path(r'F:\ZackBot2_data\LONGSCOUT-REVIEW-20261114-evidence')
def blob(ref,path): return subprocess.check_output(['git','show',f'{ref}:{path}'],cwd=R)
checks={}
for ref,path in [('a4e3b7591dcb7538ff9eb5aeeb1a07d82b6276e2','research/longscout_v1.py'),('465808a2289863beca314766f3cd55038004174b','research/extra31_screen/longscout_v1_cases.json')]:
    a,b=blob(ref,path),blob('0a54ad4c',path); assert a==b
    checks[path]=dict(freeze=ref,sha256=hashlib.sha256(a).hexdigest(),unchanged=True)
cases=json.loads(blob('0a54ad4c','research/extra31_screen/longscout_v1_cases.json'))['cases']
rows={}
for name in cases:
    if name.startswith('exp_trend'): continue
    t=pd.read_csv(D/f'{name}_all_orders_trades.csv.gz',parse_dates=['t_in']); t=t[t.scen=='path']
    nums=[]
    for order in range(40):
        g=t[t.order==order]; r=g.pnl15/8
        best=g.groupby('sym').pnl15.sum().idxmax() if len(g) else None
        nums.append(dict(n=len(g),meanR15=r.mean(),meanR23=(g.pnl23/8).mean(),early=r[g.t_in<pd.Timestamp('2025-01-01')].mean(),late=r[g.t_in>=pd.Timestamp('2025-01-01')].mean(),ex_best=(g.loc[g.sym!=best,'pnl15']/8).mean()))
    m=pd.DataFrame(nums).median().to_dict(); z=t[t.order==0]
    gates=dict(trades=m['n']>=100,meanR15=m['meanR15']>0,meanR23=m['meanR23']>=0,early=m['early']>0,late=m['late']>0,ex_best_coin=m['ex_best']>0,order0_30_per_half=bool((z.t_in<pd.Timestamp('2025-01-01')).sum()>=30 and (z.t_in>=pd.Timestamp('2025-01-01')).sum()>=30))
    ref=json.loads((D/f'{name}_result.json').read_text()); assert gates==ref['gates'],(name,gates,ref['gates'])
    rows[name]=dict(medians=m,gates=gates,pass_all=all(gates.values()))
out=dict(frozen_blobs=checks,gate_tables=rows,passed=sum(x['pass_all'] for x in rows.values()),limits='Gate arithmetic checked on supplied cash tables for all eight; independent raw-price replay only LC5, regime review separately. Not independent economics validation of all eight.')
(O/'artifact_checks.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
print(json.dumps(dict(freeze_unchanged=True,cases_checked=len(rows),pass_count=out['passed'],volume=rows['lc_volume_accum_1d']),indent=2))
