"""Frozen artifact and all-order gate reconciliation; retirement record. No outcomes rerun/tuned."""
from pathlib import Path
import hashlib,json,subprocess
import pandas as pd
import numpy as np
R=Path(r'F:\ZackBot2_data\C1H-20261112\review-branch')
E=Path(r'F:\ZackBot2_data\LONGSCOUT-FRESH-REVIEW-20261114')
O=E/'independent'
head='f1ee3f229511f7d60d8a2ccf7328eced631b5115'
def blob(ref,path): return subprocess.check_output(['git','show',f'{ref}:{path}'],cwd=R)
path='research/extra31_screen/longscout_fresh_v1.json'
freeze=blob('200943c3',path)
assert freeze==blob(head,path)
assert hashlib.sha256(freeze).hexdigest()=='8df402e3740419763bbcec0e177c8d6d54dfb0d2efd02c9003683b2b710fdcf5'
assert blob(head,'research/longscout_v1.py')==blob('a4e3b759','research/longscout_v1.py')
arms={}
for arm in ('fresh','pooled'):
    D=E/'docs/research/longscout_fresh_v1'/arm
    t=pd.read_csv(D/'lc_volume_accum_1d_all_orders_trades.csv.gz',parse_dates=['t_in','t_out'])
    t=t[t.scen=='path']; rows=[]
    for k in range(40):
        g=t[t.order==k]; best=g.groupby('sym').pnl15.sum().idxmax()
        rows.append(dict(n=len(g),meanR15=g.pnl15.mean()/8,meanR23=g.pnl23.mean()/8,meanR15_early=g.loc[g.t_in<pd.Timestamp('2025-01-01'),'pnl15'].mean()/8,meanR15_late=g.loc[g.t_in>=pd.Timestamp('2025-01-01'),'pnl15'].mean()/8,meanR15_ex_best=g.loc[g.sym!=best,'pnl15'].mean()/8))
    m=pd.DataFrame(rows).median().to_dict(); z=t[t.order==0]; counts=[int((z.t_in<pd.Timestamp('2025-01-01')).sum()),int((z.t_in>=pd.Timestamp('2025-01-01')).sum())]
    gates=dict(trades=m['n']>=100,meanR15=m['meanR15']>0,meanR23=m['meanR23']>=0,early=m['meanR15_early']>0,late=m['meanR15_late']>0,ex_best_coin=m['meanR15_ex_best']>0,order0_30_per_half=bool(min(counts)>=30))
    ref=json.loads((D/'lc_volume_accum_1d_result.json').read_text()); assert gates==ref['gates']
    for key,value in m.items(): assert np.isclose(value,ref['medians'][key],atol=1e-12,rtol=1e-12),(arm,key)
    curve=z.sort_values('t_out').pnl15.cumsum()/8
    arms[arm]=dict(medians=m,gates=gates,meanR15_p10=float(np.percentile([r['meanR15'] for r in rows],10)),order0_n=len(z),order0_half_counts=counts,order0_coins=int(z.sym.nunique()),order0_positive_coins=int((z.groupby('sym').pnl15.sum()>0).sum()),order0_closed_dd_R=float((curve.cummax()-curve).max()),failed_gates=[k for k,v in gates.items() if not v])
out=dict(head=head,freeze_sha256=hashlib.sha256(freeze).hexdigest(),rules_unchanged=True,arms=arms,scope='Independent full order-0 signal/quantity/execution/cash replay on fresh coins in volume_result.json. All-order/pooled gate reconciliation here uses supplied trade tapes, not 40 new execution replays. Survivorship and consumed calendar remain disclosed.')
(O/'artifact_checks.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
retirement=dict(id='lc_volume_accum_1d',state='RETIRED',rule_commit='a4e3b7591dcb7538ff9eb5aeeb1a07d82b6276e2',result_head=head,study='LONGSCOUT-FRESH-V1',reason='Fails fresh-coin economics and sample gates; late half and ex-best negative. Pooled secondary evidence does not rescue fresh FAIL.',prior_evidence='LONGSCOUT-V1 extra31 positive but insufficient sample; retained, not withdrawn or erased.',changes='Research disposition only. No registry, runnable rule, runtime, account or scheduler change. No tuning or weakened gate.')
(O/'retirement.json').write_text(json.dumps(retirement,indent=2)+'\n',encoding='utf-8')
print(json.dumps(out,indent=2))
