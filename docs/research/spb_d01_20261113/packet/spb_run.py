import csv,hashlib,io,json,pathlib,sys,zipfile
import numpy as np,pandas as pd
from spb_signals import signal,spot_row,decision
P=pathlib.Path(__file__).resolve().parent; S=pathlib.Path('F:/ZackBot2_data/SPOTFLOW-D01-20261112'); H=lambda p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
F=json.loads((P/'freeze.json').read_text())
for p,h in F['files'].items():
 if H(p)!=h: raise SystemExit('freeze mismatch '+p)
M=json.loads((S/'data_manifest_frozen.json').read_text()); V=json.loads((S/'data_validation.json').read_text())
sys.path.insert(0,str(S/'harness/research/rsr1h'))
import run_rsr1h as R
X,D=R.X,R.D
T0=pd.Timestamp('2024-10-11 17:00'); T1=pd.Timestamp('2026-09-27 23:00'); X.T0=D.T0=T0; X.T1=D.T1=T1; X.SPLIT=D.SPLIT=pd.Timestamp('2025-10-01')
syms=M['symbols']; rules=json.loads((S/'harness/data/exchange_rules_testnet.json').read_text())['symbols']; coins={}; raw_signals=[]; counts={}; masks=[]
for s in syms:
 a=D.load('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean',s,rules)
 n=len(a['t']); mask=a['t']<=np.datetime64(T1)
 for k,v in list(a.items()):
  if isinstance(v,np.ndarray) and len(v)==n: a[k]=v[mask]
 f=pd.read_csv(f'C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean/{s}_1h.csv',usecols=['t','c','qv']); f.t=pd.to_datetime(f.t,unit='ms'); f=f.set_index('t').asfreq('h').loc[:T1]
 rows=[]
 for m in M['months']:
  z=zipfile.ZipFile(S/f'{s}-1h-{m}.zip'); factor=1 if m<'2025-01' else 1000
  with z.open(z.namelist()[0]) as zf:
   rows.extend(spot_row(r,factor) for r in csv.reader(io.TextIOWrapper(zf,encoding='utf8')))
 sp=pd.DataFrame(rows,columns=['t','c','qv']); sp.t=pd.to_datetime(sp.t,unit='ms'); sp=sp.set_index('t').asfreq('h')
 sg,z,b,mean=signal(sp,f); dec=[]
 for t,sd in sg.loc[T0:T1].items():
  if not sd: continue
  d=decision(a,t,sd)
  if d is None: continue
  dec.append(d); raw_signals.append(dict(sym=s,t=str(t),side=sd,z=z[t],basis=b[t],prior_mean=mean[t],fill=str(pd.Timestamp(d[0])),dist=d[3]))
 coins[s]={'a':a,'dec':dec}; counts[s]={'long':sum(x[1]>0 for x in dec),'short':sum(x[1]<0 for x in dec)}
 masks.append(pd.Series(a['elig'],index=pd.DatetimeIndex(a['t'])).reindex(pd.date_range(T0,T1,freq='h'),fill_value=False).to_numpy())
out=P/'results'; out.mkdir(exist_ok=True); pd.DataFrame(raw_signals).to_csv(out/'raw_signals.csv',index=False)
orders=[sorted(syms)]+[list(np.random.default_rng(20261112+k).permutation(sorted(syms))) for k in range(1,40)]; weeks=(T1-T0).total_seconds()/604800; ew=np.any(masks,axis=0).sum()/168
res={'id':'SPB-D01','freeze_sha256':H(P/'freeze.json'),'signal_counts':counts,'orders':orders,'paths':{},'limitations':'consumed DEV, fixed survivor universe, asynchronous hourly closes, funding proxy, current rules, no live parity'}
for mode in ('causal','worst'):
 res['paths'][mode]={}
 for cb in (7,15,23):
  rr=[]
  for k,order in enumerate(orders):
   tr,sk=R.simulate(coins,syms,order,cb,mode)
   if any(t['t_in']>T1 or t['t_out']>T1+pd.Timedelta(hours=1) for t in tr): raise SystemExit('cutoff')
   sm=X.summarize(tr,sk,weeks,ew); sm['order']=k; rr.append(sm)
   if k==0: pd.DataFrame([{kk:v for kk,v in t.items() if kk not in ('marks','cash')} for t in tr]).to_csv(out/f'{mode}.{cb}bp.order0.trades.csv',index=False)
  df=pd.DataFrame(rr)
  def q(col):
   if col not in df or not df[col].notna().any(): return None
   a=df[col]; return {'median':float(a.median()),'p10':float(a.quantile(.1)),'p90':float(a.quantile(.9)),'min':float(a.min()),'max':float(a.max())}
  cols=('closes','closes_per_cal_week','closes_per_elig_week','meanR_budget','meanR_actual','early_meanR','later_meanR','exbest_meanR','closedR_maxdd','marked_dd_R','longest_underwater_days')
  res['paths'][mode][f'{cb}bp']={'order0':rr[0],'across_orders':{c:q(c) for c in cols},'all_orders':rr}
  (out/'results.json').write_text(json.dumps(res,indent=2,default=str)); print(mode,cb,flush=True)
res['gates']=X.gates(res['paths']['causal'],(res['paths']['worst']['15bp']['across_orders']['meanR_budget'] or {}).get('median'))
res['gates']['median_3_closes_per_week']=(res['paths']['causal']['15bp']['across_orders']['closes_per_cal_week'] or {}).get('median',0)>=3
res['gates']['PASS']=all(v for k,v in res['gates'].items() if k!='PASS')
(out/'results.json').write_text(json.dumps(res,indent=2,default=str)); print('COMPLETE',flush=True)
