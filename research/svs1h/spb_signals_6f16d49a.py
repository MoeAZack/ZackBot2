import numpy as np, pandas as pd
def spot_row(row, factor):
 raw,end=int(row[0]),int(row[6]); t=raw//factor
 if raw%factor or end%factor!=factor-1 or end//factor!=t+3599999: raise ValueError('timestamp')
 c,q=float(row[4]),float(row[7])
 if not np.isfinite(c) or c<=0 or not np.isfinite(q) or q<0: raise ValueError('spot fields')
 return t,c,q

def eligibility(q, paired):
 q=q.where(paired & np.isfinite(q) & (q>=0))
 daily=q.groupby(q.index.floor('D')).sum(min_count=24).asfreq('D')
 med=daily.rolling(30,min_periods=30).median().shift(1)
 return (med.reindex(q.index.floor('D')).to_numpy()>=1e7)&(paired.rolling(720,min_periods=720).sum().to_numpy()==720)

def signal(spot, fut):
 ix=fut.index; s=spot.reindex(ix); f=fut
 paired=np.isfinite(s.c)&(s.c>0)&np.isfinite(f.c)&(f.c>0)&np.isfinite(s.qv)&(s.qv>=0)&np.isfinite(f.qv)&(f.qv>=0)
 b=np.log(f.c.where(paired)/s.c.where(paired)); prior=b.shift(1).rolling(168,min_periods=168)
 mean=prior.mean(); std=prior.std(ddof=1); z=(b-mean)/std.where(std>0)
 ok=eligibility(s.qv,paired)&eligibility(f.qv,paired)&((b-mean).abs()>=.003)
 sig=(ok&(z<=-3)&(z.shift(1)>-3)).astype(int)-(ok&(z>=3)&(z.shift(1)<3)).astype(int)
 return sig,z,b,mean

def decision(a,t,side):
 ts=pd.Timestamp(t).to_datetime64(); ix=np.searchsorted(a['t'],ts)
 if ix>=len(a['t']) or a['t'][ix]!=ts or ix+2>=len(a['t']): return None
 if a['t'][ix+1]-ts!=np.timedelta64(1,'h') or a['t'][ix+2]-ts!=np.timedelta64(2,'h'): return None
 dist=3*float(a['atr'][ix])
 if not np.isfinite(dist) or dist<=0: return None
 return (a['t'][ix+2],int(side),ix+1,dist)
