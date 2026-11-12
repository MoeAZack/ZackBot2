import pathlib,json,hashlib,sys
import numpy as np,pandas as pd
import spotflow_oracle as O
P=pathlib.Path(__file__).parent;D=pathlib.Path('F:/ZackBot2_data/SPOTFLOW-D01-20261112');V=json.loads((D/'data_validation.json').read_text());frames={};spots={};native={};own=[]
# Uses only the separately authored pre-frozen oracle, not candidate code.
for s in O.COINS:
 f=pd.read_csv(V['joins'][s]['canonical']['path']);f.t=pd.to_datetime(f.t,unit='ms');f=f.set_index('t').loc[:O.T_TO];frames[s]=f
 n=pd.read_csv(V['joins'][s]['native']['path'],parse_dates=['t']).set_index('t').rename(columns={'tbqv':'tbq'});native[s]=n.loc[:O.T_TO]
 spots[s]=O.read_klines(sorted(D.glob(s+'-1h-*.zip')))
for s in O.COINS:
 z=O.signals(frames[s],native[s],spots[s],frames['BTCUSDT'])
 for t,row in z.iterrows():own.append(dict(sym=s,t=str(t),side=int(row.side),atr=float(row.atr)))
out=pd.DataFrame(own);out.to_csv(P/'independent_raw_signals.csv',index=False)
ref=pd.read_csv(D/'results/raw_signals.csv');keys=lambda f:set(zip(f.sym,pd.to_datetime(f.t),f.side.astype(int)))
a,b=keys(out),keys(ref);report={'oracle_head':'3e09a7961c1a69561cfe93f22f38f77e144358db','oracle_sha256':hashlib.sha256((P/'spotflow_oracle.py').read_bytes()).hexdigest(),'independent_signal_count':len(a),'reference_count':len(b),'matched':len(a&b),'missing':len(b-a),'extra':len(a-b),'imports_candidate':False,'cash_checks':{}}
for cb in [7,15,23]:
 tr=pd.read_csv(D/f'results/causal.{cb}bp.order0.trades.csv');rules=json.loads((D/'harness/data/exchange_rules_testnet.json').read_text())['symbols'];maxerr=0;diff=[]
 for row in tr.itertuples():
  s=row.sym;sig=pd.Timestamp(row.t_in)-O.H;atr=float(O.wilder_atr(frames[s]).loc[sig]);q=O.oracle_lot(frames[s],sig,int(row.side),atr,rules[s],cb)
  for k in ['qty','gross','fees','fund','pnl']:
   err=abs(float(q[k])-float(getattr(row,k)));maxerr=max(maxerr,err)
   if err>1e-8:diff.append({'sym':s,'field':k,'error':err})
  if pd.Timestamp(q['t_out'])!=pd.Timestamp(row.t_out) or q['why']!=row.why:diff.append({'sym':s,'reason':'exit-time-or-reason'})
 report['cash_checks'][str(cb)]={'lots':len(tr),'max_abs_error':maxerr,'mismatches':diff}
(P/'independent_comparison.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
