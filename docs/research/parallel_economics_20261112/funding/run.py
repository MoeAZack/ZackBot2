import json,hashlib,pathlib,math,collections,sys
import numpy as np,pandas as pd
P=pathlib.Path(r'F:\ZackBot2_data\FUNDING-CANDIDATES-20261112'); CLEAN=pathlib.Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112\clean')
C=json.loads((P/'prereg.json').read_text()); H=pd.Timedelta(hours=1); T0=pd.Timestamp('2022-08-26 06:00'); END=pd.Timestamp('2026-10-04 05:00'); SPLIT=pd.Timestamp('2025-01-01')
def day(t): return t.tz_localize('UTC').tz_convert('Africa/Cairo').date()
def cross(r,prev): return abs(r)>=.0003 and abs(prev)<.0003
def settle_pnl(side,qty,mark,rate): return -side*qty*mark*rate
def size(dist,entry,step): return min(math.floor(8/dist/step+1e-9),math.floor(3333.3333/entry/step+1e-9))*step
def timing(t): return t.ceil('h')+H if t!=t.floor('h') else t+H
def fee_pnl(side,e,x,q,fee): return side*(x-e)*q-(e+x)*q*fee
def test():
 assert timing(pd.Timestamp('2024-01-01 08:00:00.005'))==pd.Timestamp('2024-01-01 10:00')
 assert timing(pd.Timestamp('2024-01-01 08:00'))==pd.Timestamp('2024-01-01 09:00')
 assert cross(.0003,.0002) and not cross(.0004,.00031) and cross(-.0003,0)
 assert settle_pnl(1,2,100,.001)==-.2 and settle_pnl(-1,2,100,.001)==.2
 assert size(3,100,.1)<=8/3 and fee_pnl(1,100,100,1,.001)<0 and fee_pnl(-1,100,90,1,.001)>0
 assert day(pd.Timestamp('2025-01-01 22:30')) != day(pd.Timestamp('2025-01-01 21:30'))
 print('PRE-ECON TESTS PASSED: timing, crossing, long/short settlement sign, floor risk, fees, Cairo day',flush=True)
def load():
 info=json.loads((P/'exchangeInfo.json').read_text(encoding='utf-8')); rules={}
 for s in info['symbols']:
  if s['symbol'] in C['universe']:
   f={x['filterType']:x for x in s['filters']}; rules[s['symbol']]=(float(f['LOT_SIZE']['stepSize']),float(f['LOT_SIZE']['minQty']),float(f.get('MIN_NOTIONAL',{}).get('notional',0)))
 data={}; candidates=[]; audit={}
 for sym in C['universe']:
  raw=json.loads((P/(sym+'.json')).read_text()); f=pd.DataFrame(raw); f['t']=pd.to_datetime(f.fundingTime,unit='ms'); f['r']=f.fundingRate.astype(float); f['mark']=pd.to_numeric(f.markPrice,errors='coerce'); m=pd.DataFrame(json.loads((P/(sym+'_mark8h.json')).read_text())); m['mt']=pd.to_datetime(m[0],unit='ms'); m['proxy']=pd.to_numeric(m[1]); f=pd.merge_asof(f,m[['mt','proxy']],left_on='t',right_on='mt',direction='backward'); f['proxy_used']=f['mark'].isna(); f['proxy_lag_hours']=(f.t-f.mt).dt.total_seconds()/3600; f['mark']=f['mark'].fillna(f.proxy); assert f['mark'].notna().all()
  assert not f.t.duplicated().any() and f.t.is_monotonic_increasing and len(f)>1000
  d=pd.read_csv(CLEAN/(sym+'_1h.csv')); d['t']=pd.to_datetime(d.t,unit='ms'); assert not d.t.duplicated().any() and d.t.is_monotonic_increasing
  d=d[d.t<END].reset_index(drop=True); tr=pd.concat([d.h-d.l,(d.h-d.c.shift()).abs(),(d.l-d.c.shift()).abs()],axis=1).max(axis=1); d['atr']=tr.ewm(alpha=1/14,adjust=False).mean(); d['e50']=d.c.ewm(span=50,adjust=False).mean(); d['e200']=d.c.ewm(span=200,adjust=False).mean()
  day0=d.t.dt.floor('D'); daily=d.groupby(day0).qv.sum().where(d.groupby(day0).size()==24); daily=daily.reindex(pd.date_range(daily.index.min(),daily.index.max())); med=daily.rolling(30,min_periods=30).median().shift(1); run=d.groupby(d.t.diff().ne(H).cumsum()).cumcount()+1; d['eligible']=(med.reindex(day0).values>=1e7)&(run>=720)
  idx=pd.Index(d.t); fs=[]
  for j in range(1,len(f)):
   row=f.iloc[j]; prev=f.iloc[j-1];
   if not cross(row.r,prev.r): continue
   ent=timing(row.t); i=idx.get_indexer([ent])[0]-1
   if i<0 or i+1>=len(d) or d.t.iloc[i+1]!=ent or d.t.iloc[i]+H!=ent or d.t.iloc[i]<row.t or ent<T0 or ent>=END or not d.eligible.iloc[i]: continue
   fs.append((ent,sym,i,float(row.r),row.t))
  candidates.extend(fs); data[sym]=(d,f,rules[sym]); delta=f.t.diff().dropna().dt.total_seconds()/3600
  audit[sym]={'rows':len(f),'first':str(f.t.iloc[0]),'last':str(f.t.iloc[-1]),'interval_hours_counts':delta.round(3).value_counts().to_dict(),'sha256':hashlib.sha256((P/(sym+'.json')).read_bytes()).hexdigest(),'price_sha256':hashlib.sha256((CLEAN/(sym+'_1h.csv')).read_bytes()).hexdigest(),'proxy_marks':int(f.proxy_used.sum()),'max_proxy_lag_hours':float(f.loc[f.proxy_used,'proxy_lag_hours'].max()),'mark8h_sha256':hashlib.sha256((P/(sym+'_mark8h.json')).read_bytes()).hexdigest(),'raw_crossings_eligible':len(fs)}
 return data,sorted(candidates),audit
def lot(d,f,rule,i,side,fee,stress):
 dist=3*d.atr.iloc[i]; j=i+1; entryt=d.t.iloc[j]; e=d.o.iloc[j]*(1+side*.0002); step,minq,minn=rule; q=size(dist,e,step)
 if q<minq or q*e<minn or not np.isfinite(q): return None
 stop=e-side*dist; target=e+side*2*dist; n=len(d); k=j; why='TERMINAL_OPEN'
 while k<n:
  row=d.iloc[k]; gap=k>j and side*(row.o-stop)<=0; hit=side*(row.l-stop)<=0 if side==1 else side*(row.h-stop)<=0; tp=side*(row.h-target)>=0 if side==1 else side*(row.l-target)>=0
  if gap: ref=row.o; why='stop_gap'; out=row.t; break
  if hit: ref=stop; why='stop'; out=row.t+H; break
  if tp: ref=target; why='target'; out=row.t+H; break
  if row.t+H>=entryt+24*H: ref=row.c; why='time'; out=row.t+H; break
  if k+1>=n or d.t.iloc[k+1]!=row.t+H: ref=row.c; why='data_end_or_gap'; out=row.t+H; break
  k+=1
 else: raise RuntimeError()
 x=ref*(1-side*.0002); ff=f[(f.t>entryt)&(f.t<out)]; funding=sum(settle_pnl(side,q,r.mark,r.r) for r in ff.itertuples()); stresscost=sum(q*float(v)*.0001/8 for v in d.c.iloc[j:k+1]) if stress else 0
 pnl=fee_pnl(side,e,x,q,fee)+funding-stresscost
 return {'entry':entryt,'exit':out,'pnl':pnl,'entry_fee':e*q*fee,'risk':q*dist,'side':side,'funding':funding,'stress':stresscost,'why':why,'held':(out-entryt)/H,'qty':q,'entryprice':e,'exitprice':x,'settlements':len(ff)}
def simulate(data,signals,mode,fee,stress):
 trades=[]; realized=collections.defaultdict(float); until=pd.Timestamp.min; refused=collections.Counter()
 for ent,sym,i,r,ft in signals:
  d,f,rule=data[sym]; side=(-1 if r>0 else 1) if mode=='FC1' else (1 if r>0 else -1)
  if mode=='FC2' and side*(d.e50.iloc[i]-d.e200.iloc[i])<=0: continue
  if ent<until: refused['occupied']+=1; continue
  if realized[day(ent)]<=-120: refused['daily_halt']+=1; continue
  z=lot(d,f,rule,i,side,fee,stress)
  if z is None: refused['filters']+=1; continue
  assert ent>ft and z['risk']<=8.000001
  realized[day(ent)]-=z['entry_fee']; realized[day(z['exit'])]+=z['pnl']+z['entry_fee']; z.update(symbol=sym,signal_funding_time=ft,signal_rate=r); trades.append(z); until=z['exit']
 return trades,dict(refused)
def summary(trades):
 if not trades: return {'n':0}
 d=pd.DataFrame(trades); out={'n':len(d),'pnl':float(d.pnl.sum()),'mean_R_budget':float(d.pnl.mean()/8),'win_rate':float((d.pnl>0).mean()),'funding_pnl':float(d.funding.sum()),'trades_per_week':float(len(d)/((END-T0).days/7)),'median_holding_hours':float(d.held.median()),'max_risk':float(d.risk.max()),'per_symbol':d.groupby('symbol').pnl.sum().to_dict(),'exit_reasons':d.why.value_counts().to_dict()}
 v=d.groupby('symbol').pnl.sum(); best=v.idxmax(); out['best_coin']=best; out['excluding_best_coin_pnl']=float(d[d.symbol!=best].pnl.sum()); cum=d.pnl.cumsum(); out['realized_max_drawdown']=float((cum.cummax().clip(lower=0)-cum).max()); return out
if __name__=='__main__':
 test(); data,signals,audit=load(); results={}
 for mode in ['FC1','FC2']:
  for bps,fee in [(7,.0005),(15,.0013),(23,.0021)]:
   for stress in [False,True]:
    trades,refused=simulate(data,signals,mode,fee,stress); name=f'{mode}_{bps}bp_stress{int(stress)}'; pd.DataFrame(trades).to_csv(P/(name+'_trades.csv'),index=False)
    results[name]={'all':summary(trades),'early_consumed_dev':summary([x for x in trades if x['entry']<SPLIT]),'later_consumed_dev':summary([x for x in trades if x['entry']>=SPLIT]),'refused':refused}
    print(name,json.dumps(results[name]['all']),flush=True)
 (P/'results.json').write_text(json.dumps({'audit':audit,'results':results,'limitations':['Realized-only drawdown; marked-to-market drawdown not yet computed.','All history is consumed development, not independent validation.','REST fundingTime is settlement timestamp; publication latency not independently proven.','Hourly stop/target collisions use stop first; funding uses Binance reported settlement mark where supplied and earlier mark-candle-open proxy otherwise.','Funding stress is one adverse bp per eight hours, using each held hourly close.']},indent=2,default=str))


