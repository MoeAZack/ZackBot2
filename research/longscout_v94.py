"""LONGSCOUT V94 draft: peer attention rotation and dispersion-shock range.

Eligible fixed fresh134+extra31 peers:31 complete consecutive days and prior30
median daily quote volume >=$10m; BTC never a traded peer; >=50 peers each day.
L94 peer attention rotation: own daily volume/prior20 median volume strictly
above eligible-peer80th percentile, own10-day return above peer median; first
day of full statistical condition, R1 rising/sideways.3ATR/3R/60bars.
R94 dispersion shock: population sd of peer daily log returns >2x prior30-day
median dispersion (all31 days >=50 peers). Coin return below peer10th percentile
->long, above90th ->short, first day of full condition; R1 sideways.
2.5ATR/2R/30bars. Percentiles use linear interpolation, strict comparisons:
ties at the threshold do not enter. No arbitrary future symbol tie-break.

New peer-relative attention/dispersion inputs, not own-volume spike or own-price
band retuning. Both decisions next-day00, modeled04 fill. Research-only OFF.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v92 as H

set_btc = L1.set_btc
_PEERS = None


def features(panel):
    rr,rv,mom = {},{},{}
    for sym,d in sorted(panel.items()):
        if sym=='BTCUSDT': continue
        D = H.daily(d)
        ready = L27._consec(D,30) & (D.qv.shift().rolling(30,min_periods=30).median()>=1e7)
        rr[sym] = np.log(D.c/D.c.shift()).where(ready)
        rv[sym] = (D.v/D.v.shift().rolling(20,min_periods=20).median()).where(ready)
        mom[sym] = (D.c/D.c.shift(10)-1).where(ready)
    R,V,M = pd.DataFrame(rr),pd.DataFrame(rv),pd.DataFrame(mom)
    n = R.notna().sum(axis=1)
    F = pd.DataFrame(dict(n=n,vol80=V.quantile(.8,axis=1,interpolation='linear'),
                         mom50=M.quantile(.5,axis=1,interpolation='linear'),
                         r10=R.quantile(.1,axis=1,interpolation='linear'),
                         r90=R.quantile(.9,axis=1,interpolation='linear')))
    sd = R.std(axis=1,ddof=0).where(n>=50)
    F['shock'] = (sd>2*sd.shift().rolling(30,min_periods=30).median()) & L27._consec(F,30)
    return F.where(n>=50)


def set_panel(panel):
    global _PEERS
    _PEERS = features(panel)


def daily_flags(D,F,rotation):
    F = F.reindex(D.index)
    eligible = L27._consec(D,30) & (D.qv.shift().rolling(30,min_periods=30).median()>=1e7) & (F.n>=50)
    if rotation:
        q = D.v/D.v.shift().rolling(20,min_periods=20).median()
        hit = eligible & (q>F.vol80) & (D.c/D.c.shift(10)-1>F.mom50)
        return dict(long=(hit & ~hit.shift(fill_value=False)).fillna(False))
    r = np.log(D.c/D.c.shift())
    lo = eligible & F.shock.eq(True) & (r<F.r10)
    hi = eligible & F.shock.eq(True) & (r>F.r90)
    return dict(long=(lo & ~lo.shift(fill_value=False)).fillna(False),
                short=(hi & ~hi.shift(fill_value=False)).fillna(False))


def entries(d,rotation):
    if _PEERS is None: raise RuntimeError('set_panel required')
    D = H.daily(d)
    flags = daily_flags(D,_PEERS,rotation)
    cr = L1.coin_regime(d)
    out = {k:pd.Series(False,index=d.index) for k in ('le','se','lx','sx')}
    gate = np.isin(cr,(0.,1.)) if rotation else cr==0
    for side,key in [('le','long'),('se','short')]:
        if key in flags: out[side]=S.X(pd.Series(L27._next_day(flags[key],D,d) & gate,index=d.index))
    return out


def sig_rotation(d,p,ctx): return entries(d,True)
def sig_dispersion(d,p,ctx): return entries(d,False)


CANDIDATES={
 'l94_peer_attention_rotation_4h':dict(name='[RESEARCH] L94 peer relative volume attention rotation',style='momentum',fn=sig_rotation,sides='long',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=3.,tp_r=3.,max_bars=60)),
 'r94_peer_dispersion_shock_fade_4h':dict(name='[RESEARCH] R94 peer dispersion shock range fade',style='range',fn=sig_dispersion,sides='both',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
}


def register():
    for k,v in CANDIDATES.items(): S.STRATEGIES[k]=dict(v,desc=v['name'])
