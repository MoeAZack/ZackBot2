"""SPOTFLOW-D01: one frozen consumed-DEV economics study, no runtime/account access."""
import csv, hashlib, io, json, os, pathlib, sys, zipfile
import numpy as np, pandas as pd
from spotflow_signals import signal
P=pathlib.Path(__file__).resolve().parent; H=lambda p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
receipt=json.loads((P/'economic_freeze.json').read_text())
for name,digest in receipt['sha256'].items():
 if H(P/name)!=digest: raise SystemExit('freeze hash mismatch '+name)
V=json.loads((P/'data_validation.json').read_text()); M=json.loads((P/'data_manifest_frozen.json').read_text())
if len(V['archives'])!=225 or any(not x['checksum_ok'] or x['errors'] or x['missing_hours'] for x in V['archives']): raise SystemExit('incomplete archive validation')
for a in V['archives']:
 name=f"{a['symbol']}-1h-{a['month']}.zip"
 if H(P/name)!=a['sha256']: raise SystemExit('archive hash mismatch '+name)
for joins in V['joins'].values():
 for k in ('native','canonical'):
  if H(joins[k]['path'])!=joins[k]['sha256'] or joins[k]['futures_without_spot'] or joins[k]['spot_without_futures']: raise SystemExit('input hash or coverage mismatch')
sys.path.insert(0,str(P/'harness/research/rsr1h'))
import run_rsr1h as R
X,D=R.X,R.D
T0=pd.Timestamp('2024-10-11 17:00'); T1=pd.Timestamp('2026-09-27 23:00'); SPLIT=pd.Timestamp('2025-10-01')
D.T0=X.T0=T0; D.T1=X.T1=T1; D.SPLIT=X.SPLIT=SPLIT
syms=M['symbols']; rules=json.loads((P/'harness/data/exchange_rules_testnet.json').read_text())['symbols']
raw={s:D.load('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean',s,rules) for s in syms}
if any(raw[s]['rule'] is None for s in syms): raise SystemExit('missing exchange rule')
for s,a in raw.items():
 n=len(a['t']); mask=a['t']<=np.datetime64(T1)
 for key,value in list(a.items()):
  if isinstance(value,np.ndarray) and len(value)==n: a[key]=value[mask]
 if not len(a['t']) or a['t'][-1]>np.datetime64(T1): raise SystemExit('price cutoff failed '+s)
def spot(s):
 rows=[]
 for m in M['months']:
  z=zipfile.ZipFile(P/f'{s}-1h-{m}.zip'); factor=1 if m<'2025-01' else 1000
  with z.open(z.namelist()[0]) as zf:
   for r in csv.reader(io.TextIOWrapper(zf,encoding='utf8')): rows.append((int(r[0])//factor,float(r[7]),float(r[10])))
 d=pd.DataFrame(rows,columns=['t','qv','tbqv']); d.t=pd.to_datetime(d.t,unit='ms'); return d.set_index('t')
btc_a=raw['BTCUSDT']; btc=pd.DataFrame({'c':btc_a['c']},index=pd.DatetimeIndex(btc_a['t'])).asfreq('h')
coins={}; counts={}; boundaries=[]; raw_signals=[]
for s in syms:
 a=raw[s]; ix=pd.DatetimeIndex(a['t']); price=pd.DataFrame({k:a[k] for k in ('o','h','l','c')},index=ix).asfreq('h')
 perp=pd.read_csv(V['joins'][s]['native']['path'],parse_dates=['t']).set_index('t')[['qv','tbqv']].asfreq('h')
 atr=pd.Series(a['atr'],index=ix); spotdata=spot(s); sg,_=signal(spotdata,perp,price,btc,atr)
 ix=price.index; sp=spotdata.reindex(ix); pp=perp.reindex(ix); b=btc.reindex(ix)
 si=2*sp.tbqv/sp.qv-1; pi=2*pp.tbqv/pp.qv-1; ret=np.log(price.c/price.c.shift(3)); br=np.log(b.c/b.c.shift(3)); ap=atr.reindex(ix)/price.c
 near=((si.abs()-.2).abs()<1e-9)|((pi.abs()-.2).abs()<1e-9)|((br.abs()-.015).abs()<1e-9)
 near &= (ix>=T0)&(ix<=T1)
 for t in ix[near.fillna(False)]: boundaries.append(dict(sym=s,t=str(t),spotI=si.loc[t],perpI=pi.loc[t],ret3=ret.loc[t],atr_close=ap.loc[t],btc_ret3=br.loc[t],spot_qv=sp.qv.loc[t],spot_tbqv=sp.tbqv.loc[t],perp_qv=pp.qv.loc[t],perp_tbqv=pp.tbqv.loc[t]))
 ix=pd.DatetimeIndex(a['t'])
 eligibility=pd.Series(a['elig'],index=ix).reindex(price.index,fill_value=False)
 sg=sg.where(eligibility,0); sg=sg.loc[(sg.index>=T0)&(sg.index<=T1)]; pos=pd.Series(np.arange(len(ix)),index=ix)
 dec=[]
 for t,side in sg[sg!=0].items():
  if t not in pos: continue
  i=int(pos[t]); dist=3*float(a['atr'][i]); dec.append((t.to_datetime64()+X.HOUR,int(side),i,dist))
  raw_signals.append(dict(sym=s,t=str(t),side=int(side),spotI=si.loc[t],perpI=pi.loc[t],ret3=ret.loc[t],atr_close=ap.loc[t],btc_ret3=br.loc[t],spot_qv=sp.qv.loc[t],spot_tbqv=sp.tbqv.loc[t],perp_qv=pp.qv.loc[t],perp_tbqv=pp.tbqv.loc[t],dist=dist))
 coins[s]={'a':a,'dec':dec}; counts[s]={'long':sum(x[1]>0 for x in dec),'short':sum(x[1]<0 for x in dec)}
base=sorted(syms); orders=[base]+[list(np.random.default_rng(20261112+k).permutation(base)) for k in range(1,40)]
weeks=(T1-T0).total_seconds()/604800; grid=pd.date_range(T0,T1,freq='h'); eligible=np.zeros(len(grid),bool)
for s in syms: eligible |= pd.Series(raw[s]['elig'],index=pd.DatetimeIndex(raw[s]['t'])).reindex(grid,fill_value=False).to_numpy()
elig_weeks=eligible.sum()/168
out=P/'results'; out.mkdir(exist_ok=True)
pd.DataFrame(boundaries,columns=['sym','t','spotI','perpI','ret3','atr_close','btc_ret3','spot_qv','spot_tbqv','perp_qv','perp_tbqv']).to_csv(out/'boundary_diagnostics.csv',index=False)
pd.DataFrame(raw_signals,columns=['sym','t','side','spotI','perpI','ret3','atr_close','btc_ret3','spot_qv','spot_tbqv','perp_qv','perp_tbqv','dist']).to_csv(out/'raw_signals.csv',index=False)
res={'id':'SPOTFLOW-D01','data_validation_sha256':H(P/'data_validation.json'),'freeze_sha256':H(P/'economic_freeze.json'),'signal_counts':counts,'orders':orders,'paths':{},'note':'consumed DEV; funding proxy; fixed survivor universe; no activation'}
for mode in ('causal','worst'):
 PTH=res['paths'][mode]={}
 for cb in (7,15,23):
  rows=[]
  for k,order in enumerate(orders):
   tr,sk=R.simulate(coins,syms,order,cb,mode)
   if any(t['t_in']>=T1+pd.Timedelta(hours=1) or t['t_out']>T1+pd.Timedelta(hours=1) for t in tr): raise SystemExit('lot exceeded frozen price cutoff')
   sm=X.summarize(tr,sk,weeks,elig_weeks); sm['order']=k; rows.append(sm)
   if k==0: pd.DataFrame([{**{kk:v for kk,v in t.items() if kk not in ('marks','cash')},'dist':t['risk']/t['qty']} for t in tr]).to_csv(out/f'{mode}.{cb}bp.order0.trades.csv',index=False)
  df=pd.DataFrame([{key:val for key,val in row.items() if not isinstance(val,dict)} for row in rows])
  def q(col):
   if col not in df or not df[col].notna().any(): return None
   a=df[col]; return {'median':round(float(a.median()),4),'p10':round(float(a.quantile(.1)),4),'p90':round(float(a.quantile(.9)),4),'min':round(float(a.min()),4),'max':round(float(a.max()),4)}
  cols=('closes','closes_per_cal_week','closes_per_elig_week','meanR_budget','meanR_actual','early_meanR','later_meanR','exbest_meanR','closedR_maxdd','marked_dd_R','longest_underwater_days')
  PTH[f'{cb}bp']={'order0':rows[0],'across_orders':{col:q(col) for col in cols},'all_orders':rows}
  (out/'results.json').write_text(json.dumps(res,indent=1,default=str)+'\n'); print(mode,cb,'complete',flush=True)
res['gates']=X.gates(res['paths']['causal'],(res['paths']['worst']['15bp']['across_orders']['meanR_budget'] or {}).get('median'))
(out/'results.json').write_text(json.dumps(res,indent=1,default=str)+'\n'); print('COMPLETE',flush=True)
