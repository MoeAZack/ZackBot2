import pandas as pd,numpy as np,json,gzip,collections,math
from pathlib import Path
B=Path('F:/ZackBot2_data/C1H-VENUE-RECHECK-20261112'); I=B/'independent_signals'; O=Path(__file__).parent
out=[]; summary={}
def pnl(e,x,cl,bps):
 a=e['ref']*1.0002; z=x['ref']*.9998; return e['qty']*(z-a-(a+z)*(bps-2)/10000-sum(cl)*.0000125)
assert abs(pnl({'qty':2,'ref':100},{'ref':110},[101,102],15)-(2*(109.978-100.02)-2*(109.978+100.02)*.0013-2*203*.0000125))<1e-12
assert pnl({'qty':3,'ref':100},{'ref':90},[95],7)<pnl({'qty':2,'ref':100},{'ref':90},[95],7)
for ds in ['cohort9_1h','long8_1h']:
 data={}
 for p in (B/'inputs'/ds).glob('*.csv'):
  d=pd.read_csv(p,parse_dates=['t']); d['atr']=pd.concat([d.h-d.l,(d.h-d.c.shift()).abs(),(d.l-d.c.shift()).abs()],axis=1).max(axis=1).ewm(alpha=1/14,adjust=False).mean(); data[p.stem]=d
 own=pd.read_csv(I/(ds+'_own.csv')); own=own.loc[own.side=='long'].copy(); signals=set(zip(own.symbol,pd.to_datetime(own.decision_candle_utc,utc=True))); times=next(iter(data.values())).t
 for case in ['7bps_path','15bps_worst','23bps_worst']:
  bps=int(case.split('bps')[0]); p=B/'results'/ds/'c1h_h5_s3_t4_b48_long'/case; t=pd.read_csv(p/'trades.csv'); fills=json.load(gzip.open(p/'fills.json.gz','rt')); m=json.loads((p/'result.json').read_text()); fs=collections.defaultdict(list)
  for f in fills: fs[(f['sym'],f['pos_i'],f['side'])].append(f)
  errs=collections.Counter(); maxp=maxr=turnover=gaps=0; intervals=[]
  for a in t.itertuples(index=False):
   f=fs[(a.sym,a.i_in,a.side)]; en=[x for x in f if x['kind']=='entry']; ex=[x for x in f if x['kind']=='exit']
   if len(en)!=1 or len(ex)!=1 or len(f)!=2: errs['fill_structure']+=1;continue
   e,x=en[0],ex[0]; i,j=a.i_in,a.i_out; d=data[a.sym]; stop=e['ref']*1.0002-3*d.atr.iloc[i-1]; target=e['ref']*1.0002+12*d.atr.iloc[i-1]
   if e['qty']!=x['qty'] or e['qty']<=0: errs['quantity']+=1
   if (e['side'],x['side'],e['action'],x['action'],e['liq'],x['liq'])!=(1,1,'buy','sell','taker','taker'): errs['side']+=1
   if e['i']!=i or x['i']!=j or not math.isclose(e['ref'],d.o.iloc[i],rel_tol=1e-12,abs_tol=1e-10): errs['entry']+=1
   if not math.isclose(e['stop_dist'],3*d.atr.iloc[i-1],rel_tol=1e-10,abs_tol=1e-9): errs['atr']+=1
   if (a.sym,pd.Timestamp(d.t.iloc[i-1],tz='UTC')) not in signals or d.t.iloc[i]-d.t.iloc[i-1]!=pd.Timedelta(hours=1): errs['signal']+=1
   if a.holding_bars!=j-i or not 0<=j-i<=48: errs['hold']+=1
   expected=None
   for k in range(i,j+1):
    bar=d.iloc[k]
    if bar.o<=stop: expected=(k,'stop',bar.o,True); break
    if bar.l<=stop: expected=(k,'stop',stop,False); break
    if bar.h>=target: expected=(k,'tp',target,False); break
    if k-i>=48: expected=(k,'time',bar.c,False); break
   if expected is None: errs['no_exit']+=1
   else:
    k,why,ref,gap=expected
    if k!=j or why!=a.why or why!=x['why'] or not math.isclose(ref,x['ref'],rel_tol=1e-10,abs_tol=1e-8) or gap!=x['gap']: errs['exit']+=1
   v=pnl(e,x,d.c.iloc[i:j+1],bps); dp=abs(v-a.pnl); dr=abs(v/e['risk']-a.R);maxp=max(dp,maxp);maxr=max(dr,maxr)
   if dp>1e-8: errs['pnl']+=1
   if dr>1e-8: errs['R']+=1
   tv=sum(z['qty']*z['ref'] for z in f);turnover+=tv;gaps+=int(x['gap']);intervals.append((i,j));out.append(dict(dataset=ds,case=case,sym=a.sym,i_in=i,i_out=j,qty=e['qty'],turnover=tv,ledger_pnl=a.pnl,repriced=v,diff=v-a.pnl,ledger_R=a.R,repriced_R=v/e['risk']))
  intervals.sort()
  for a,b in zip(intervals,intervals[1:]):
   if b[0]<=a[1]: errs['overlap']+=1
  entries=[x for x in fills if x['kind']=='entry']; ids=set(zip(t.sym,t.i_in,t.side)); terminal=[x for x in entries if (x['sym'],x['pos_i'],x['side']) not in ids]
  for e in terminal:
   if (e['sym'],pd.Timestamp(data[e['sym']].t.iloc[e['i']-1],tz='UTC')) not in signals: errs['terminal_signal']+=1
   if intervals and e['i']<=intervals[-1][1]: errs['terminal_overlap']+=1
  start=times.iloc[220]; end=times.iloc[-1]; periods=[]
  for y in range(start.year,end.year+1):
   for h in (1,2):
    lo=pd.Timestamp(y,1 if h==1 else 7,1); hi=pd.Timestamp(y,7,1) if h==1 else pd.Timestamp(y+1,1,1)
    if start<=lo and end>=hi-pd.Timedelta(hours=1): periods.append(f'{y}-H{h}')
  coins=t.groupby('sym').R.sum(); best=coins.idxmax(); curve=pd.read_csv(p/'curve.csv'); v=np.r_[4000.,curve.iloc[:,1].to_numpy()]; cr=t.R.cumsum(); q=m['metrics']
  summary[ds+'/'+case]=dict(n=len(t),errors=dict(errs),max_pnl_diff=maxp,max_R_diff=maxr,entries=len(entries),raw_long_signals=len(signals),terminal=len(terminal),reasons=t.why.value_counts().to_dict(),gaps=gaps,turnover=turnover,mean_R=t.R.mean(),splits=t.groupby('period_dev').R.agg(['size','mean']).to_dict('index'),complete_halfyears={z:float(t.loc[t.halfyear==z,'R'].sum()) for z in periods},ex_best_coin=best,ex_best_mean_R=t.loc[t.sym!=best,'R'].mean(),closes_per_week=len(t)/((end-start).total_seconds()/604800),median_hold=t.holding_bars.median(),monthly=t.groupby(t.exit_time_utc.str[:7]).size().to_dict(),liquidations=q['liquidations'],input_gaps=m['coverage']['internal_gaps'],mark_dd=float((1-v/np.maximum.accumulate(v)).max()),R_dd=float((cr.cummax().clip(lower=0)-cr).max()),underwater_hours=q['mark_to_market_max_underwater_duration_bars'],start=str(start),end=str(end))
pd.DataFrame(out).to_csv(O/'h5_trade_repricing.csv',index=False); (O/'h5_execution_summary.json').write_text(json.dumps(summary,indent=2,default=str));print(json.dumps(summary,default=str))