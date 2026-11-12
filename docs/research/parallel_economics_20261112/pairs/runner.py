import json,math,hashlib
import pandas as pd,numpy as np
from pathlib import Path
D=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112\clean')
O=Path(r'F:\ZackBot2_data\PAIR-CANDIDATES-20261112')
pairs=[('ETH','BTC'),('SOL','ETH'),('LTC','BCH'),('LINK','UNI'),('DOT','ADA')]
steps=dict(BTC=.001,ETH=.001,SOL=.1,LTC=.001,BCH=.001,LINK=.01,UNI=.1,DOT=.1,ADA=1)
end=pd.Timestamp('2026-09-28',tz='UTC')
def sig(a,b):
 x=np.log(b.c); y=np.log(a.c); R=lambda q:q.rolling(168).mean().shift(1)
 mx=R(x); my=R(y); vx=R(x*x)-mx*mx; cov=R(x*y)-mx*my; vy=R(y*y)-my*my
 beta=cov/vx; sd=np.sqrt((vy-cov*cov/vx).clip(lower=0)); z=(y-my-beta*(x-mx))/sd
 ok=(a.qv.rolling(720).sum().shift(1)>=1e7)&(b.qv.rolling(720).sum().shift(1)>=1e7)&a.t.diff().eq(3600000).rolling(720).sum().shift(1).eq(720)&(beta>.2)&(beta<5)&(sd>1e-8)
 return pd.DataFrame(dict(z=z,beta=beta,sd=sd,ok=ok))
def load():
 d={}
 for sym in set(sum(([a,b] for a,b in pairs),[])):
  f=pd.read_csv(D/(sym+'USDT_1h.csv')); f=f[f.t<end.value//10**6]; f.index=pd.to_datetime(f.t,unit='ms',utc=True); d[sym]=f
 out={}
 for a,b in pairs:
  ix=d[a].index.intersection(d[b].index); x=d[a].loc[ix]; y=d[b].loc[ix]; out[(a,b)]=(x,y,sig(x,y))
 return out
def run(data,thr,cost):
 times=sorted(set().union(*(set(a.index) for a,b,s in data.values())))
 maps={p:{t:i for i,t in enumerate(a.index)} for p,(a,b,s) in data.items()}
 pos=None; pending=None; equity=peak=dd=0.; rows=[]; skips=0
 for t in times:
  if pending:
   p,side,sg=pending; pending=None; a,b,s=data[p]; i=maps[p].get(t)
   if i and t-a.index[i-1]==pd.Timedelta(hours=1):
    pa=float(a.o.iloc[i]); pb=float(b.o.iloc[i]); beta=sg.beta; wy=1/(1+beta); wx=1-wy
    adv=max(wy*np.expm1((4-abs(sg.z))*sg.sd),wx*np.expm1((4-abs(sg.z))*sg.sd/beta))
    gross=min(3330,8/(adv+.0052)); qa=math.floor(gross*wy/pa/steps[p[0]])*steps[p[0]]; qb=math.floor(gross*wx/pb/steps[p[1]])*steps[p[1]]
    if qa*pa>=5 and qb*pb>=5:
     c=(qa*pa+qb*pb)*cost/10000; pos=dict(p=p,side=side,pa=pa,pb=pb,qa=qa,qb=qb,entry=t,bars=0,fund=0,base=equity,c=c); equity-=c
    else: skips+=1
  if pos:
   p=pos['p']; a,b,s=data[p]; i=maps[p].get(t)
   if i is None: raise RuntimeError('missing live leg')
   ca=float(a.c.iloc[i]); cb=float(b.c.iloc[i]); gp=pos['side']*(pos['qa']*(ca-pos['pa'])-pos['qb']*(cb-pos['pb']))
   fund=(pos['qa']*ca+pos['qb']*cb)*.0001/8; equity-=fund; pos['fund']+=fund; pos['bars']+=1
   mtm=equity+gp; peak=max(peak,mtm); dd=max(dd,peak-mtm); z=s.z.iloc[i]
   stop=gp-pos['c']-pos['fund']<=-8 or (np.isfinite(z) and abs(z)>=4)
   if stop or (np.isfinite(z) and z*pos['side']>=-.5) or pos['bars']>=48:
    if i+1<len(a) and a.index[i+1]==t+pd.Timedelta(hours=1):
     na=float(a.o.iloc[i+1]); nb=float(b.o.iloc[i+1]); exitcost=(pos['qa']*na+pos['qb']*nb)*cost/10000
     gp=pos['side']*(pos['qa']*(na-pos['pa'])-pos['qb']*(nb-pos['pb'])); equity+=gp-exitcost
     rows.append(dict(pair='/'.join(p),entry=str(pos['entry']),exit=str(a.index[i+1]),gross=gp,net=equity-pos['base'],cost=pos['c']+exitcost,funding=pos['fund'],hours=pos['bars'],reason='stop' if stop else 'exit'))
     peak=max(peak,equity); dd=max(dd,peak-equity); pos=None
     continue
    pos=None
  if pos is None:
   for p in pairs:
    i=maps[p].get(t)
    if i is None: continue
    sg=data[p][2].iloc[i]
    if sg.ok and np.isfinite(sg.z) and thr<=abs(sg.z)<4: pending=(p,-1 if sg.z>0 else 1,sg); break
 return pd.DataFrame(rows),dd,skips
def summary(r,dd=None):
 if r.empty:return dict(trades=0)
 g={p:dict(n=len(v),net_R=round(v.net.sum()/8,3),mean_R=round(v.net.mean()/8,4)) for p,v in r.groupby('pair')}
 best=max(g,key=lambda p:g[p]['net_R']); win=r.loc[r.net>0,'net'].sum(); loss=-r.loc[r.net<0,'net'].sum()
 return dict(trades=len(r),net_R=round(r.net.sum()/8,3),mean_R=round(r.net.mean()/8,4),PF=round(win/loss,3) if loss else None,drawdown_R=round(dd/8,3) if dd is not None else None,per_pair=g,best=best,ex_best_net_R=round((r.net.sum()-r.loc[r.pair==best,'net'].sum())/8,3),trades_per_month=round(len(r)/((pd.Timestamp(r.exit.max())-pd.Timestamp(r.entry.min())).days/30.4375),2))
data=load()
for a,b,s in data.values():
 k=min(4000,len(a)); pd.testing.assert_frame_equal(s.iloc[:k],sig(a.iloc[:k],b.iloc[:k]),check_exact=False,rtol=1e-8,atol=1e-8)
assert math.isclose((100*2+200*2)*7/10000,.42)
assert min(3330,8/(.01+.0052))*(.01+.0052)<=8.000001
out={}
for thr in [2.,2.5]:
 for cost in [7,15,23]:
  r,dd,sk=run(data,thr,cost); key=f'P{thr}_cost{cost}'; r.to_csv(O/(key+'_trades.csv'),index=False)
  if len(r):
   dt=pd.to_datetime(r.entry,utc=True); early=r[dt<pd.Timestamp('2025-01-01',tz='UTC')]; later=r[dt>=pd.Timestamp('2025-01-01',tz='UTC')]
   out[key]=dict(all=summary(r,dd),early=summary(early),later=summary(later),skips=sk)
  else:out[key]=dict(all=summary(r),skips=sk)
(O/'results.json').write_text(json.dumps(out,indent=2))
