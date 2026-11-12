import pathlib,json,hashlib,subprocess,base64
import pandas as pd,numpy as np
P=pathlib.Path(__file__).parent
C=pathlib.Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean')
V=pathlib.Path('C:/Dev/ZackBot_cowork_data/edge01_20261023/cache_15m')
ct=json.loads((P/'contract.json').read_text());syms=ct['signal']['coins']
rules=json.loads(pathlib.Path('F:/ZackBot2_data/X2-WIDE-MINIMAL-REVIEW-20261112/data/exchange_rules_testnet.json').read_text())['symbols']
sigs=pd.read_csv('F:/ZackBot2_data/X2-INDEPENDENT-SIGNAL-20261112/independent_eligible_signals.csv');sigs['decision_utc']=pd.to_datetime(sigs.decision_utc)
sigs=sigs[sigs.symbol.isin(syms)&sigs.decision_utc.between('2024-10-11 20:00','2026-09-27 20:00')]
rows=[]
for sym in syms:
 raw=pd.read_csv(C/(sym+'_1h.csv'));raw['t']=pd.to_datetime(raw.t,unit='ms');g=raw.set_index('t').resample('4h');b=g.agg({'o':'first','h':'max','l':'min','c':'last'});b=b[g.size()==4]
 tr=pd.concat([b.h-b.l,(b.h-b.c.shift()).abs(),(b.l-b.c.shift()).abs()],axis=1).max(axis=1);b['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
 v=pd.read_csv(V/(sym+'_15m.csv'));v['t']=pd.to_datetime(v.t,unit='ms');v=v.set_index('t');r=rules[sym]
 for z in sigs[sigs.symbol==sym].itertuples():
  dec=z.decision_utc;sd=z.side;bar=b.loc[dec-pd.Timedelta(hours=4)];dist=2.5*bar.atr;tick=r['tick'];k=(bar.c-sd*.25*bar.atr)/tick;L=(np.floor(k+1e-9) if sd==1 else np.ceil(k-1e-9))*tick
  qty=min(np.floor(8/dist/r['step']+1e-9),np.floor(3333.33/L/r['step']+1e-9))*r['step'];post=dec+pd.Timedelta(minutes=15);seg=v.loc[post:post+pd.Timedelta(minutes=45)]
  status='unfilled';fill=None;gap=False
  if len(seg)!=4 or qty<r['min_qty'] or qty*L<r['min_notional']:status='refused'
  elif sd*(seg.iloc[0].o-L)<=0:status='postonly_cross'
  else:
   for t,x in seg.iterrows():
    extreme=x.l if sd==1 else x.h
    if sd*(extreme-(L-sd*2*tick))<=0 and x.v>=100*qty:
     status='filled';fill=str(t);gap=sd*(x.o-(L-sd*dist))<=0;break
  rows.append(dict(sym=sym,t_sig=str(dec),side=sd,maker=status,fill_time=fill,fill_open_beyond_stop=bool(gap)))
out=pd.DataFrame(rows);out.to_csv(P/'own_fill_classifications.csv',index=False)
summary={'independent_signal_count':len(out),'status_counts':out.maker.value_counts().to_dict(),'gap_fill_count':int(out.fill_open_beyond_stop.sum()),'code_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'imports_author_code':False}
(P/'own_fill_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
# Author comparison only AFTER independent classification saved.
r=subprocess.run(['gh','api','repos/MoeAZack/ZackBot2/contents/docs/research/mpe01/signal_level.m15.base.csv?ref=ac2304e11e8cf76493bb51be6d3bc969a73a0c46'],capture_output=True,text=True,check=True)
ref_bytes=base64.b64decode(json.loads(r.stdout)['content']);(P/'reference_signal_level.csv').write_bytes(ref_bytes);ref=pd.read_csv(P/'reference_signal_level.csv');ref['t_sig']=pd.to_datetime(ref.t_sig).astype(str)
x=out.merge(ref,on=['sym','t_sig','side'],how='outer',suffixes=('_own','_ref'),indicator=True)
summary['compared_head']='ac2304e11e8cf76493bb51be6d3bc969a73a0c46';summary['matched_keys']=int((x._merge=='both').sum());summary['missing_keys']=int((x._merge=='right_only').sum());summary['extra_keys']=int((x._merge=='left_only').sum());summary['classification_mismatches']=int(((x._merge=='both')&(x.maker_own!=x.maker_ref)).sum());(P/'own_fill_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
