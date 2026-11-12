"""Frozen 1h signals. UNIVERSE records the fixed-survivor raw mapping keys."""
import numpy as np
import pandas as pd
import strategies as S
BTC='BTCUSDT'
PANEL={}
UNIVERSE=()
def set_panel(raw):
    global PANEL,UNIVERSE
    PANEL={}; UNIVERSE=tuple(sorted(s for s in raw if s!=BTC))
    if BTC not in raw or not UNIVERSE: return UNIVERSE
    frames={}
    for s,d in raw.items():
        d=d.copy(); d['t']=pd.to_datetime(d.t,utc=True)
        if d.t.duplicated().any(): raise ValueError('duplicate timestamp: '+s)
        d=d.set_index('t').sort_index()
        if len(d) and ((d.index.minute!=0)|(d.index.second!=0)|(d.index.microsecond!=0)).any(): raise ValueError('non-hourly timestamp: '+s)
        frames[s]=d
    if any(d.empty for d in frames.values()): return UNIVERSE
    grid=pd.date_range(min(d.index.min() for d in frames.values()),max(d.index.max() for d in frames.values()),freq='h',tz='UTC')
    frames={s:d.reindex(grid) for s,d in frames.items()}
    c=pd.DataFrame({s:frames[s].c for s in UNIVERSE}).replace([np.inf,-np.inf],np.nan)
    valid=c.notna().all(axis=1).rolling(25,min_periods=25).sum().eq(25)
    ret=c.div(c.shift(24)).sub(1).replace([np.inf,-np.inf],np.nan).where(valid,axis=0)
    mom=ret.sub(ret.median(axis=1),axis=0)
    b=frames[BTC]; bc=b.c.replace([np.inf,-np.inf],np.nan)
    bv=bc.notna().rolling(25,min_periods=25).sum().eq(25)
    residual=ret.sub(bc.div(bc.shift(24)).sub(1),axis=0).replace([np.inf,-np.inf],np.nan).where(bv,axis=0)
    decision=pd.Series(grid.hour%4==0,index=grid)
    def winner(x):
        ok=x.notna().all(axis=1)&decision
        out=pd.Series(None,index=grid,dtype=object); out.loc[ok]=x.loc[ok].abs().idxmax(axis=1)
        return out
    wm,wr=winner(mom),winner(residual)
    btc6=bc.div(bc.shift(6)).sub(1)
    hi,lo=b.h.rolling(12,min_periods=12).max().shift(),b.l.rolling(12,min_periods=12).min().shift()
    up=(bc>hi)&(bc.shift()<=hi.shift()); down=(bc<lo)&(bc.shift()>=lo.shift())
    vol=b.v>=1.5*b.v.rolling(24,min_periods=24).median().shift()
    bok=b[['c','h','l','v']].replace([np.inf,-np.inf],np.nan).notna().all(axis=1).rolling(26,min_periods=26).sum().eq(26)
    for s in UNIVERSE:
        a=frames[s]; ac=a.c; e50,e200=S.ema(ac,50),S.ema(ac,200); rsi=S.rsi(ac,14)
        ah,al=a.h.rolling(12,min_periods=12).max().shift(),a.l.rolling(12,min_periods=12).min().shift()
        aok=a[['c','h','l']].replace([np.inf,-np.inf],np.nan).notna().all(axis=1).rolling(13,min_periods=13).sum().eq(13)
        inside=(ac<ah)&(ac>al)&aok
        z=pd.DataFrame(index=grid)
        z['u1_le']=decision&(wm==s)&(mom[s]>=.02)&(e50>e200)&(ac>e50)
        z['u1_se']=decision&(wm==s)&(mom[s]<=-.02)&(e50<e200)&(ac<e50)
        z['u2_le']=decision&(wr==s)&(residual[s]<=-.03)&(btc6.abs()<.02)&(rsi<35)&(ac>a.o)
        z['u2_se']=decision&(wr==s)&(residual[s]>=.03)&(btc6.abs()<.02)&(rsi>65)&(ac<a.o)
        z['u3_le']=up&vol&bok&inside&(ac>e50)&(e50>e200)
        z['u3_se']=down&vol&bok&inside&(ac<e50)&(e50<e200)
        PANEL[s]=z.fillna(False).astype(bool)
    return UNIVERSE

def _signal(d,ctx,prefix):
    t=pd.DatetimeIndex(pd.to_datetime(d.t,utc=True)); z=PANEL.get(ctx.get('symbol'))
    if z is None: le=se=pd.Series(False,index=d.index)
    else:
        x=z.reindex(t).fillna(False)
        le=pd.Series(x[prefix+'_le'].to_numpy(dtype=bool),index=d.index); se=pd.Series(x[prefix+'_se'].to_numpy(dtype=bool),index=d.index)
    no=pd.Series(False,index=d.index)
    return dict(le=le,se=se,lx=no,sx=no.copy())
def u1(d,p,ctx): return _signal(d,ctx,'u1')
def u2(d,p,ctx): return _signal(d,ctx,'u2')
def u3(d,p,ctx): return _signal(d,ctx,'u3')
def _cfg(name,fn,tp,bars):
    return dict(name=name,desc='Research-only fixed-survivor hourly candidate; no demonstrated edge.',research_only=True,style='research',fn=fn,sides='both',tf='1h',params={},mgmt=dict(stop_atr=3.,tp_r=tp,max_bars=bars,be_r=0,trail_atr=0))
CANDIDATES={'c1h_u1_xs_momentum':_cfg('Cross-sectional momentum',u1,3.,12),'c1h_u2_residual_fade':_cfg('BTC residual fade',u2,2.,12),'c1h_u3_btc_volume_catchup':_cfg('BTC volume lead catchup',u3,3.,24)}
def register(): S.STRATEGIES.update(CANDIDATES)

