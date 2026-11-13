import json,hashlib,math
from pathlib import Path
import numpy as np,pandas as pd
P=Path('F:/ZackBot2_data/SVS-IMP-REVIEW-20261113');roots={'SPOTFLOW-D01-20261112':Path('F:/ZackBot2_data/SPOTFLOW-D01-20261112'),'h5_wide_d01_20261112':Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112')};out={};cache={}
for st in ('svs1h','impretest1h'):
 src=P/'research'/st;f=json.loads((src/'freeze.json').read_text());fh=hashlib.sha256((src/'freeze.json').read_bytes()).hexdigest();assert fh==(src/'freeze.sha256').read_text().strip()
 for rel,h in f['files'].items():
  first,tail=rel.split('/',1);q=src/tail if first=='SELF' else roots[first]/tail;assert hashlib.sha256(q.read_bytes()).hexdigest()==h,rel
 result=json.loads((P/f'docs/research/{st}/results/results.json').read_text());assert result['freeze_sha256']==fh
 checks=[]
 raw=pd.read_csv(P/f'docs/research/{st}/results/raw_signals.csv');raw['scheduled_entry']=pd.to_datetime(raw['scheduled_entry']);rawmap={(r['sym'],pd.Timestamp(r['scheduled_entry']),int(r['side'])):r['dist'] for _,r in raw.dropna(subset=['scheduled_entry']).iterrows()};rules=json.loads((roots['SPOTFLOW-D01-20261112']/'harness/data/exchange_rules_testnet.json').read_text())['symbols']
 for bp in (7,15,23):
  tr=pd.read_csv(P/f'docs/research/{st}/results/causal.{bp}bp.order0.trades.csv');selected=set()
  for col,vals in [('side',[1,-1]),('why',['stop','target','time','TERMINAL_OPEN'])]:
   for v in vals:
    ix=tr.index[tr[col]==v]
    if len(ix):selected.add(int(ix[0]))
  selected.add(len(tr)-1)
  for ix in sorted(selected):
   r=tr.loc[ix]; s=r['sym'];sd=int(r['side']);q=float(r['qty']);e=float(r.e_ref)*(1+sd*.0002);x=float(r.x_ref)*(1-sd*.0002); gross=sd*(x-e)*q;fees=q*(e+x)*(bp-2)/1e4;slip=sd*((e-r.e_ref)+(r.x_ref-x))*q
   assert r['why']!='stop_gap'
   dist=rawmap[(s,pd.Timestamp(r.t_in),sd)];step=rules[s]['step']; expected_qty=min(np.floor(8/dist/step+1e-9),np.floor(3333.33/e/step+1e-9))*step;assert abs(expected_qty-q)<1e-9;assert abs(q*dist-r.risk)<1e-7
   if s not in cache:
    d=pd.read_csv(roots['h5_wide_d01_20261112']/f'clean/{s}_1h.csv',usecols=['t','c']);d.t=pd.to_datetime(d.t,unit='ms');cache[s]=d
   d=cache[s];tin=pd.Timestamp(r.t_in);tout=pd.Timestamp(r.t_out);fund=q*d.loc[(d.t>=tin)&(d.t<tout),'c'].sum()*.0001/8;pnl=gross-fees-fund
   values={'entry_fill':(e,r.e),'exit_fill':(x,r.x),'gross':(gross,r.gross),'fees':(fees,r.fees),'slip':(slip,r.slip),'fund':(fund,r.fund),'pnl':(pnl,r.pnl),'risk_R':(pnl/8,r.R_budget)}
   err={k:abs(a-b) for k,(a,b) in values.items()};assert max(err.values())<1e-7,(st,bp,ix,err)
   checks.append({'bp':bp,'index':ix,'sym':s,'side':sd,'entry':str(tin),'exit':str(tout),'reason':r['why'],'max_abs_error':max(err.values())})
 out[st]={'freeze_sha256':fh,'matched_source_input_pins':len(f['files']),'causal_cash_checks':checks,'max_abs_error':max(z['max_abs_error'] for z in checks),'scope':'selected order0 causal lots across all3costs; mirrored sides and available stop/target/time/terminal reasons; no4h diagnostic or whole40order independent portfolio rerun'}
(P/'independent_cash_check.json').write_text(json.dumps(out,indent=2));print(json.dumps({k:{'pins':v['matched_source_input_pins'],'lots_checked':len(v['causal_cash_checks']),'max_error':v['max_abs_error']} for k,v in out.items()},indent=2))
