"""LONGSCOUT V93 preregistration draft: downside beta and half-day reversal.

L93 downside-conditional beta rebound: prior60 DAILY simple-return pairs,
using ONLY days BTC return<0; >=15 such days, coin/BTC covariance divided
by BTC variance >=1.2. Current completed day BTC return<=-2%, coin<=-4%;
coin R1 rising/sideways. Next-day00 signal, modeled04 fill;2.5ATR/2R/30bars.
New stress-conditional co-movement family, not ordinary beta trend following.

R93 half-day range reversal: at the08-open4h bar's close (12:00 UTC), all
00/04/08 bars complete and contiguous; log(close08/open00)<=-.03 ->long,
>=+.03 ->short. Last available coin R1 sideways. Next12-open fill; both
signal exits on20-openbar close (00:00 UTC);2ATR/no target,6bar safety cap.
This is reversal conditional on morning displacement, not V47's unconditional
regime session hold. Continuous crypto session, NOT an overnight gap.

All fixed before market outcomes, research-only/default OFF; no parameter search.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v92 as H

set_btc = L1.set_btc


def conditional_beta(J,p):
    # Both response and regressor are prior-only, down-day mask included.
    x = J.rb.shift()
    y = J.rc.shift()
    take = (x<0) & x.notna() & y.notna()
    n = take.astype(int).rolling(p['n'],min_periods=p['n']).sum()
    sx = x.where(take,0.).rolling(p['n'],min_periods=p['n']).sum()
    sy = y.where(take,0.).rolling(p['n'],min_periods=p['n']).sum()
    sxx = (x*x).where(take,0.).rolling(p['n'],min_periods=p['n']).sum()
    sxy = (x*y).where(take,0.).rolling(p['n'],min_periods=p['n']).sum()
    denominator = sxx-sx*sx/n.replace(0,np.nan)
    beta = (sxy-sx*sy/n.replace(0,np.nan))/denominator.where(denominator>0)
    return beta.where(n>=p['min_down'])


def downside_flags(D,J,p):
    beta = conditional_beta(J,p)
    return ((beta>=p['beta']) & (J.rb<=p['btc_drop']) & (J.rc<=p['coin_drop'])
            & J.rb.notna().rolling(p['n']+1,min_periods=p['n']+1).min().eq(1)
            & J.rc.notna().rolling(p['n']+1,min_periods=p['n']+1).min().eq(1)
            & L27._consec(D,p['n']+1)).fillna(False)


def empty(d):
    return {k:pd.Series(False,index=d.index) for k in ('le','se','lx','sx')}


def joint(d):
    D = H.daily(d)
    B = H.daily(L1._BTC['f'])
    J = pd.DataFrame(dict(c=D.c,b=B.c.reindex(D.index)),index=D.index)
    step = J.index.to_series().diff().eq(pd.Timedelta(days=1))
    J['rc'] = J.c.pct_change(fill_method=None).where(step)
    J['rb'] = J.b.pct_change(fill_method=None).where(step)
    return D,J


def sig_downside(d,p,ctx):
    D,J = joint(d)
    flags = downside_flags(D,J,p)
    out = empty(d)
    out['le'] = S.X(pd.Series(L27._next_day(flags,D,d) & np.isin(L1.coin_regime(d),(0.,1.)),index=d.index))
    return out


def session_flags(d,p):
    t = pd.to_datetime(d.t)
    ready = t.dt.hour.eq(8) & t.diff().eq(pd.Timedelta(hours=4)) & t.diff(2).eq(pd.Timedelta(hours=8))
    ready &= t.dt.normalize().eq(t.shift(2).dt.normalize())
    move = np.log(d.c/d.o.shift(2))
    return dict(long=(ready & (move<=-p['move'])).fillna(False),
                short=(ready & (move>=p['move'])).fillna(False))


def sig_session(d,p,ctx):
    flags = session_flags(d,p)
    cr = L1.coin_regime(d)
    out = empty(d)
    out['le'] = S.X(flags['long'] & (cr==0))
    out['se'] = S.X(flags['short'] & (cr==0))
    out['lx'] = out['sx'] = pd.to_datetime(d.t).dt.hour.eq(20)
    return out


IP = dict(n=60,min_down=15,beta=1.2,btc_drop=-.02,coin_drop=-.04)
CANDIDATES = {
 'l93_downside_beta_rebound_4h':dict(name='[RESEARCH] L93 downside-beta stress rebound long',style='reversal',fn=sig_downside,sides='long',tf='4h',research_only=True,params=dict(IP),mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
 'r93_halfday_displacement_reversal_4h':dict(name='[RESEARCH] R93 morning displacement range reversal',style='range',fn=sig_session,sides='both',tf='4h',research_only=True,params=dict(move=.03),mgmt=dict(stop_atr=2.,max_bars=6)),
}


def register():
    for k,v in CANDIDATES.items(): S.STRATEGIES[k]=dict(v,desc=v['name'])
