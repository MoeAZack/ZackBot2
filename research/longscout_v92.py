"""LONGSCOUT V92: preregistered breadth and intraday-path hypotheses.

L92 market participation thrust: for the fixed fresh134+extra31 peer universe,
eligible peers have 31 consecutive completed days and prior30 median daily
quote volume >=$10m. Breadth=fraction with positive10-day close return,
at least50 eligible peers. First day crossing breadth from <.65 to >=.65
(both days >=50 peers). Buy a positive10-day coin in R1 rising/sideways.
Unlike prior joint BTC/coin triggers, the timing input is peer participation.

R92 range path settlement: among six chronological complete4h bars in day j,
earliest daily LOW occurs in first2 bars, close>daily midrange and close<open
->long; mirror earliest HIGH first2, close<midrange and close>open ->short.
First day of each full path condition, R1 sideways. Prior OHLC-only candles
do not encode when the day's extreme occurred. Not a V91 regime-slice rescue.

Both next-day00 signal bar, modeled fill on next04 bar; stop2.5ATR, target2R,
max30bars. All thresholds fixed before outcomes; research-only default OFF.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc
_BREADTH = None


def daily(d):
    t = pd.to_datetime(d.t)
    f = pd.DataFrame({k: d[k].to_numpy() for k in ('o','h','l','c','v')})
    f['day'] = t.dt.normalize().to_numpy()
    f['hour'] = t.dt.hour.to_numpy()
    f['qv'] = d.qv.to_numpy() if 'qv' in d else np.nan
    g = f.groupby('day', sort=True)
    D = g.agg(o=('o','first'),h=('h','max'),l=('l','min'),c=('c','last'),v=('v','sum'),qv=('qv',lambda x:x.sum(min_count=6)))
    full = g.hour.apply(lambda x:list(x)==[0,4,8,12,16,20])
    D = D[full]
    D['low_pos'] = g.l.apply(lambda x:int(np.argmin(x.to_numpy()))).reindex(D.index)
    D['high_pos'] = g.h.apply(lambda x:int(np.argmax(x.to_numpy()))).reindex(D.index)
    return D


def breadth(panel, p):
    returns = {}
    for sym,d in sorted(panel.items()):
        if sym == 'BTCUSDT': continue
        D = daily(d)
        ok = L27._consec(D,30) & (D.qv.shift().rolling(30,min_periods=30).median()>=p['liq'])
        returns[sym] = (D.c/D.c.shift(10)-1).where(ok)
    R = pd.DataFrame(returns)
    n = R.notna().sum(axis=1)
    b = (R>0).sum(axis=1)/n.replace(0,np.nan)
    hit = (b>=p['breadth']) & (b.shift()<p['breadth']) & (n>=p['min_peers']) & (n.shift()>=p['min_peers'])
    consecutive = hit.index.to_series().diff().eq(pd.Timedelta(days=1))
    return hit & consecutive


def set_panel(panel):
    global _BREADTH
    _BREADTH = breadth(panel, PARAMS)


def path_conditions(D):
    mid = (D.h+D.l)/2
    lo = (D.low_pos<2) & (D.c>mid) & (D.c<D.o)
    hi = (D.high_pos<2) & (D.c<mid) & (D.c>D.o)
    ready = L27._consec(D,1)
    return dict(long=(lo & ~lo.shift(fill_value=False) & ready).fillna(False),
                short=(hi & ~hi.shift(fill_value=False) & ready).fillna(False))


def empty(d):
    return {k:pd.Series(False,index=d.index) for k in ('le','se','lx','sx')}


def sig_breadth(d,p,ctx):
    if _BREADTH is None: raise RuntimeError('set_panel required')
    D = daily(d)
    positive = (D.c>D.c.shift(10)) & L27._consec(D,10)
    hit = _BREADTH.reindex(D.index,fill_value=False) & positive
    out = empty(d)
    out['le'] = S.X(pd.Series(L27._next_day(hit,D,d) & np.isin(L1.coin_regime(d),(0.,1.)),index=d.index))
    return out


def sig_path(d,p,ctx):
    D = daily(d)
    flags = path_conditions(D)
    out = empty(d)
    for side,key in [('le','long'),('se','short')]:
        out[side] = S.X(pd.Series(L27._next_day(flags[key],D,d) & (L1.coin_regime(d)==0),index=d.index))
    return out


PARAMS = dict(liq=1e7,breadth=.65,min_peers=50)
MGMT = dict(stop_atr=2.5,tp_r=2.,max_bars=30)
CANDIDATES = {
 'l92_market_breadth_thrust_4h':dict(name='[RESEARCH] L92 eligible-peer breadth thrust long',style='momentum',fn=sig_breadth,sides='long',tf='4h',research_only=True,params=dict(PARAMS),mgmt=dict(MGMT)),
 'r92_intraday_extreme_timing_4h':dict(name='[RESEARCH] R92 early-extreme range settlement fade',style='range',fn=sig_path,sides='both',tf='4h',research_only=True,params={},mgmt=dict(MGMT)),
}


def register():
    for k,v in CANDIDATES.items(): S.STRATEGIES[k]=dict(v,desc=v['name'])
