"""CX2: sequential standardized CUSUM change detection (new family).

At completed day j, z=log(c_j/c_{j-1}) / sample SD of the PREVIOUS
20 daily log returns. P=max(0,P+z-.5), M=min(0,M+z+.5).
P>=5 emits long and resets P to zero; M<=-5 emits short and resets M.
Both states start/reset at zero on missing days or invalid/zero prior SD.
Only events with matching daily R1 are admitted; next-day first 4h signal.
No trend/breakout level is fitted. Fixed 2.5 ATR stop, 2R target, 30-bar cap.
All constants frozen a priori. Research only; separate long/short trials.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc
IP = dict(n=20, drift=0.5, barrier=5.0)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)


def daily_events(D, p):
    r = np.log(D.c / D.c.shift())
    sd = r.shift().rolling(p['n'], min_periods=p['n']).std()
    valid = L27._consec(D, p['n']+1).to_numpy() & np.isfinite(sd) & (sd > 0)
    out = np.zeros((len(D), 2), dtype=bool)
    pos = neg = 0.0
    for j, z in enumerate((r / sd).to_numpy()):
        if not valid.iloc[j] or not np.isfinite(z):
            pos = neg = 0.0
            continue
        pos = max(0.0, pos+z-p['drift'])
        neg = min(0.0, neg+z+p['drift'])
        if pos >= p['barrier']:
            out[j, 0] = True; pos = 0.0
        if neg <= -p['barrier']:
            out[j, 1] = True; neg = 0.0
    return pd.DataFrame(out, index=D.index, columns=['long', 'short'])


def signal(d, p, up):
    D = L27._days_v(d)
    hit = L27._next_day(daily_events(D, p)['long' if up else 'short'], D, d)
    hit = pd.Series(hit & (L1.coin_regime(d) == (1 if up else -1)), index=d.index)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(hit) if up else z, se=z if up else S.X(hit), lx=z, sx=z)


def sig_long(d,p,ctx): return signal(d,p,True)
def sig_short(d,p,ctx): return signal(d,p,False)
CANDIDATES = {
    'cx003_cusum_long_4h': dict(name='[RESEARCH] CX003 standardized CUSUM long',style='momentum',fn=sig_long,sides='long',tf='4h',research_only=True,params=dict(IP),mgmt=dict(MG)),
    'cx004_cusum_short_4h': dict(name='[RESEARCH] CX004 standardized CUSUM short',style='momentum',fn=sig_short,sides='short',tf='4h',research_only=True,params=dict(IP),mgmt=dict(MG)),
}
def register():
    for k,v in CANDIDATES.items(): S.STRATEGIES[k]=dict(v,desc=v['name'])
