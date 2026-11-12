import json,hashlib,sys
from pathlib import Path
import pandas as pd,numpy as np
R=Path('F:/ZackBot2_data/RBR-INDEPENDENT-SIGNAL-20261112');D=Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean')
core=['BTC','ETH','ZEC','SOL','XRP','NEAR','DOGE','UNI','SUI','QNT','BNB','ENA','1000PEPE','WLD','ADA','AVAX','LINK'];syms=[x+'USDT' for x in core]
legacy=[x+'USDT' for x in ['BTC','ETH','ZEC','SOL','XRP','NEAR','DOGE','UNI','SUI']]
lo=pd.Timestamp('2022-08-26 06:00');hi=pd.Timestamp('2026-10-04 04:00')
freeze={'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'input_sha256':{s:hashlib.sha256((D/(s+'_1h.csv')).read_bytes()).hexdigest() for s in syms},'source_head':'ee808bdd3a1bdecc74b524a8a6c4574dcc7f4247','role':'own full math and eligibility before frozen signal reference import; no economic admission reproduction'}
(R/'independent-freeze.json').write_text(json.dumps(freeze,indent=2))
frames={};rawframes={}
for s in syms:
 d=pd.read_csv(D/(s+'_1h.csv'));d['t']=pd.to_datetime(d.t,unit='ms');d=d[d.t<=hi]
 b=d.set_index('t').resample('4h').agg(o=('o','first'),h=('h','max'),l=('l','min'),c=('c','last'),v=('v','sum'),n=('o','size'));b=b[b.n==4].reset_index()
 tr=pd.concat([b.h-b.l,(b.h-b.c.shift()).abs(),(b.l-b.c.shift()).abs()],axis=1).max(axis=1);b['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
 diff=b.c.diff();up=diff.clip(lower=0).ewm(alpha=.5,adjust=False).mean();down=(-diff.clip(upper=0)).ewm(alpha=.5,adjust=False).mean()
 b['rsi2']=100-100/(1+up/down.replace(0,np.nan))
 daily=d.groupby(d.t.dt.floor('D')).agg(q=('qv','sum'),n=('t','size'));q=daily.q.where(daily.n==24).reindex(pd.date_range(daily.index.min(),daily.index.max(),freq='D'));med=q.rolling(30,min_periods=30).median().shift(1)
 continuous=d.groupby(d.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount()+1
 eligible=(d.t.dt.floor('D').map(med)>=1e7)&(continuous>=720);lookup=pd.Series(eligible.to_numpy(),index=d.t)
 b['eligible']=(b.t+pd.Timedelta(hours=3)).map(lookup).fillna(False).astype(bool);frames[s]=b;d['eligible']=eligible;rawframes[s]=d
def calc(names):
 returns={}
 for s in names:
  b=frames[s];c=pd.Series(b.c.to_numpy(),index=b.t);prior=c.reindex(c.index-pd.Timedelta(hours=168)).to_numpy()
  returns[s]=pd.Series(c.to_numpy()/prior-1,index=c.index)
 panel=pd.DataFrame(returns).sort_index();rank=panel.rank(axis=1,ascending=False,method='average');n=panel.notna().sum(axis=1);breadth=panel.gt(0).sum(axis=1)/n.replace(0,np.nan)
 outputs={};entries=set()
 for s in names:
  b=frames[s];times=pd.DatetimeIndex(b.t);rk=pd.Series(rank[s].reindex(times).to_numpy(),index=b.index).fillna(999);nn=pd.Series(n.reindex(times).to_numpy(),index=b.index).fillna(0);br=pd.Series(breadth.reindex(times).to_numpy(),index=b.index)
  se=(nn>=8)&(rk<=np.ceil(.2*nn))&(br>=.65)&(b.rsi2<70)&(b.rsi2.shift()>=70)&(b.c<b.o)&(b.v>=.8*b.v.rolling(42,min_periods=42).median().shift())&(1.5*b.atr/b.c>=5*2*(.0005+.0002))
  sx=(rk>=np.ceil(.5*nn))|(br<.45)
  good=b.eligible&(b.t+pd.Timedelta(hours=3)>=lo)&(b.t+pd.Timedelta(hours=3)<=hi)
  for t in b.loc[se&good,'t']:entries.add((s,str(t+pd.Timedelta(hours=4)),-1))
  outputs[s]=(se.fillna(False).to_numpy(),sx.fillna(False).to_numpy(),dict(rank7=rk,rank7_n=nn,breadth7=br))
 return entries,outputs
own,outputs=calc(syms)
pd.DataFrame(sorted(own),columns=['symbol','decision_utc','side']).to_csv(R/'independent_core17_signals.csv',index=False)
sys.path.insert(0,str(R/'research/rbrwide'));import strategies_646ecc79 as SF
params=dict(rank_frac=.2,breadth_low=.35,breadth_high=.65,rsi_low=30.,rsi_high=70.,volume_floor=.8,cost_multiple=5.)
ref=set();raw_disagreements=0;exit_disagreements=0
for s in syms:
 b=frames[s];se,sx,ctx=outputs[s];rs=SF.sig_rank_breadth_reversal_4h(b,params,ctx)
 raw_disagreements+=int(np.count_nonzero(se!=rs['se'].fillna(False).to_numpy()));exit_disagreements+=int(np.count_nonzero(sx!=rs['sx'].fillna(False).to_numpy()))
 good=b.eligible&(b.t+pd.Timedelta(hours=3)>=lo)&(b.t+pd.Timedelta(hours=3)<=hi)
 for t in b.loc[rs['se'].fillna(False)&good,'t']:ref.add((s,str(t+pd.Timedelta(hours=4)),-1))
old,_=calc(legacy)
stage=lambda ss:{x for x in ss if pd.Timestamp('2024-10-14')<=pd.Timestamp(x[1])<pd.Timestamp('2025-04-01')}
newstage=stage(own);oldstage=stage(old);common_symbols_new={x for x in newstage if x[0] in legacy}
summary={'review_head':'ee808bdd3a1bdecc74b524a8a6c4574dcc7f4247','independent_core17_count':len(own),'reference_count':len(ref),'matched':len(own&ref),'missing':len(ref-own),'extra':len(own-ref),'raw_short_flag_disagreements':raw_disagreements,'raw_exit_flag_disagreements':exit_disagreements,'stageA_canonical_core17_eligible_signals':len(newstage),'stageA_canonical_legacy9_eligible_signals':len(oldstage),'stageA_shared9_signal_symmetric_difference_due_only_to_rank_universe':len(common_symbols_new^oldstage),'stageA_raw_signal_comparison_not_trade_count_or_pnl':True,'legacy9_note':'Actual oldpacket ranking universe, not claimed exact oldprice/portfolio reproduction','qualification':'No independent economic admission/return reproduction; parity establishes frozen signal implementation only'}
(R/'independent-summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
assert summary['matched']==1431 and not raw_disagreements and not exit_disagreements and not summary['missing'] and not summary['extra']

# Compare the complete author context+aggregation+eligibility path AFTER independent outputs.
sys.path.insert(0,str(R/'research/rbrwide'))
import run_rbrwide as RP
raw={}
for sym,df in rawframes.items():
 raw[sym]={k:df[k].to_numpy() for k in ['t','o','h','l','c','v']}
 raw[sym]['elig']=df.eligible.to_numpy()
 raw[sym]['v4']=pd.Series(frames[sym].v.to_numpy(),index=pd.DatetimeIndex(frames[sym].t))
prepared=RP.prepare(raw,syms)
actual={(sym,str(pd.Timestamp(dec)),int(sd)) for sym,c in prepared.items() for dec,sd,_,_ in c['dec']}
summary['full_author_prepare_count']=len(actual);summary['full_prepare_missing']=len(actual-own);summary['full_prepare_extra']=len(own-actual)
(R/'independent-summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps({'full_prepare_matched':len(actual&own),'missing':len(actual-own),'extra':len(own-actual)}))
assert not(actual^own)
