import subprocess,json,io,hashlib
from pathlib import Path
import pandas as pd,numpy as np
repo=Path('F:/ZackBot2_data/CP4-independent-review-20261112'); head='eb4d108ff820fb269ddd59622e5855b438165b3c'
out=Path('F:/ZackBot2_data/H5-WIDE-PARITY-20261112');out.mkdir(exist_ok=True)
src='docs/research/h5wide_d01/v3/'
r=subprocess.check_output(['git','show',head+':'+src+'d01v3_result.json'],cwd=repo);(out/'claude_d01v3_result.json').write_bytes(r)
res={'review_head':head,'independent_evidence_head':'7d4618852492ca0df254cc8091e335ceaf35385c','canonical_manifest':'99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95','arms':{}}
cache={}
def bars(sym):
 if sym not in cache:
  d=pd.read_csv(Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean')/(sym+'_1h.csv'))
  cache[sym]=d
 return cache[sym]
for arm in ['original9','extra31','union40']:
 b=subprocess.check_output(['git','show',head+':'+src+arm+'.15bp.order0.trades.csv'],cwd=repo);(out/(arm+'.claude15.csv')).write_bytes(b)
 c=pd.read_csv(io.BytesIO(b)); terminal=int((c.why=='TERMINAL_OPEN').sum());c=c[c.why!='TERMINAL_OPEN'].copy()
 n=pd.read_csv(Path('F:/ZackBot2_data/H5-WIDE-INDEPENDENT-20261112/v3')/(arm+'_trades.csv'))
 c['key']=c.sym+'|'+pd.to_datetime(c.t_in,utc=True).astype(str)
 n['key']=n.symbol+'|'+pd.to_datetime(n.time,utc=True).astype(str)
 assert c.key.is_unique and n.key.is_unique
 a=c.set_index('key');z=n.set_index('key');common=sorted(set(a.index)&set(z.index))
 diffs={}
 for left,right in [('qty','qty'),('e','entry'),('x','exit_price'),('risk','risk'),('fund','funding'),('pnl','net'),('R_budget','budget_R')]:
  diffs[left]=float(np.max(np.abs(a.loc[common,left].to_numpy()-z.loc[common,right].to_numpy()))) if common else None
 cash=[];funderr=[];fillerr=[]
 for row in c.itertuples():
  e=row.e_ref*1.0002;x=row.x_ref*.9998;fee=(e+x)*row.qty*.0013
  cash.append(abs((x-e)*row.qty-fee-row.fund-row.pnl));fillerr.extend([abs(e-row.e),abs(x-row.x)])
  d=bars(row.sym);start=int(pd.Timestamp(row.t_in,tz='UTC').timestamp()*1000);exitms=int(pd.Timestamp(row.t_out,tz='UTC').timestamp()*1000)
  mask=(d.t>=start)&(d.t<=exitms if row.why=='stop_gap' else d.t<exitms)
  funding=float(d.loc[mask,'c'].sum()*row.qty*.0001/8)
  funderr.append(abs(funding-row.fund))
 res['arms'][arm]={'matched':len(common),'missing_from_independent':len(set(a.index)-set(z.index)),'extra_independent':len(set(z.index)-set(a.index)),'terminal_open_not_closed':terminal,'max_abs_field_difference':diffs,'max_cash_reprice_error':max(cash,default=0),'max_fill_error':max(fillerr,default=0),'max_canonical_funding_reprice_error':max(funderr,default=0),'all_stop_risk_le8':bool((c.risk<=8+1e-8).all())}
(out/'parity.json').write_text(json.dumps(res,indent=2));print(json.dumps(res))
assert all(x['missing_from_independent']==x['extra_independent']==0 and x['max_cash_reprice_error']<1e-8 and x['max_canonical_funding_reprice_error']<1e-8 and all(v<1e-8 for v in x['max_abs_field_difference'].values()) for x in res['arms'].values())
