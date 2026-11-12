import json,hashlib,sys
from pathlib import Path
import numpy as np,pandas as pd
R=Path('F:/ZackBot2_data/X2-INDEPENDENT-SIGNAL-20261112');R.mkdir(exist_ok=True)
D=Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean')
syms=sorted(p.stem.removesuffix('_1h') for p in D.glob('*_1h.csv'))
def ownbar(sym):
 raw=pd.read_csv(D/(sym+'_1h.csv'))
 raw['t']=pd.to_datetime(raw.t,unit='ms')
 count=raw.groupby(raw.t.dt.floor('4h')).size()
 b=raw.set_index('t').resample('4h',label='left',closed='left').agg({'o':'first','h':'max','l':'min','c':'last','v':'sum'})
 b=b.loc[count[count==4].index].reset_index()
 b['e50']=b.c.ewm(span=50,adjust=False).mean();b['e200']=b.c.ewm(span=200,adjust=False).mean()
 tr=pd.concat([b.h-b.l,(b.h-b.c.shift(1)).abs(),(b.l-b.c.shift(1)).abs()],axis=1).max(axis=1)
 b['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
 daily=raw.groupby(raw.t.dt.floor('D')).agg(qv=('qv','sum'),n=('t','size'))
 dq=daily.qv.where(daily.n==24).reindex(pd.date_range(daily.index.min(),daily.index.max(),freq='D'))
 med=dq.rolling(30,min_periods=30).median().shift(1)
 streak=raw.groupby(raw.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount()+1
 eligible=(raw.t.dt.floor('D').map(med)>=1e7)&(streak>=720)
 lookup=pd.Series(eligible.to_numpy(),index=raw.t)
 b['eligible']=(b.t+pd.Timedelta(hours=3)).map(lookup).fillna(False).astype(bool)
 return raw,b
def independent(b,btc):
 ratio=b.c/b.t.map(btc.set_index('t').c)
 upper=ratio.rolling(60,min_periods=60).max().shift(1);lower=ratio.rolling(60,min_periods=60).min().shift(1)
 le=(ratio>upper)&(ratio.shift(1)<=upper.shift(1))&(b.c>b.e200)&(b.e50>b.e200)
 se=(ratio<lower)&(ratio.shift(1)>=lower.shift(1))&(b.c<b.e200)&(b.e50<b.e200)
 return le.fillna(False),se.fillna(False)
freeze={'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'inputs':{s:hashlib.sha256((D/(s+'_1h.csv')).read_bytes()).hexdigest() for s in syms},'reference_head':'de27e871feb676123bb5130d6ebf9dbba2eb70cd','purpose':'independent signal and closed-bar eligibility, not economic selection/portfolio acceptance'}
(R/'freeze.json').write_text(json.dumps(freeze,indent=2))
_,btc=ownbar('BTCUSDT'); frames={};ownset=set()
lo=pd.Timestamp('2022-08-26 06:00');hi=pd.Timestamp('2026-10-04 04:00')
for sym in syms:
 if sym=='BTCUSDT':continue
 raw,b=ownbar(sym);le,se=independent(b,btc)
 good=b.eligible&(b.t+pd.Timedelta(hours=3)>=lo)&(b.t+pd.Timedelta(hours=3)<=hi)
 for sd,z in [(1,le),(-1,se)]:
  for t in b.loc[z&good,'t']:ownset.add((sym,str(t+pd.Timedelta(hours=4)),sd))
 # Prefix causality check: values before truncation agree, including moving extremes and EMAs.
 cut=min(1200,len(b)-1)
 if cut>100:
  l2,s2=independent(b.iloc[:cut].copy(),btc[btc.t<=b.t.iloc[cut-1]])
  assert np.array_equal(le.iloc[:cut],l2) and np.array_equal(se.iloc[:cut],s2)
 frames[sym]=(raw,b)
pd.DataFrame(sorted(ownset),columns=['symbol','decision_utc','side']).to_csv(R/'independent_eligible_signals.csv',index=False)
# Reference imported only AFTER independent output exists.
sys.path.insert(0,'F:/ZackBot2_data/X2-WIDE-MINIMAL-REVIEW-20261112/research/x2wide')
import run_x2wide as X
refset=set();bar_mismatches={}
for sym,(raw,b) in frames.items():
 a={'t':raw.t.to_numpy(),'o':raw.o.to_numpy(),'h':raw.h.to_numpy(),'l':raw.l.to_numpy(),'c':raw.c.to_numpy()}
 rb=X.to4h(a)
 assert np.array_equal(rb.t.to_numpy(),b.t.to_numpy())
 assert np.allclose(rb[['o','h','l','c']].to_numpy(),b[['o','h','l','c']].to_numpy())
 rl,rs,_=X.signals(b,btc.set_index('t'))
 good=b.eligible&(b.t+pd.Timedelta(hours=3)>=lo)&(b.t+pd.Timedelta(hours=3)<=hi)
 for sd,z in [(1,rl),(-1,rs)]:
  for t in b.loc[np.asarray(z)&good,'t']:refset.add((sym,str(t+pd.Timedelta(hours=4)),sd))
summary={'independent_count':len(ownset),'reference_count':len(refset),'matched':len(ownset&refset),'missing':len(refset-ownset),'extra':len(ownset-refset),'long':sum(x[2]==1 for x in ownset),'short':sum(x[2]==-1 for x in ownset),'symbol_count':len(frames),'prefix_checks':'39 symbols first1200 closed4h bars orfullavailable','agg_parity':'all39 complete4h timestamp/OHLC exact','limitations':'same frozen input data; independently constructed math, shared reference signal used only forcomparison; excludesBTCcontext; no outcomes'}
(R/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
assert not summary['missing'] and not summary['extra']
