import sys; sys.path.insert(0,'cx'); sys.path.insert(0,'.')
import overlay_v2_corrected as V, pandas as pd
from overlay_sim import simulate
def rows(book,src,bp):
    cfg=V.BOOKS[book]; tr=V.load(cfg['sleeves'],src,bp); fr=cfg['risk']/4000
    out=[]
    for r in tr.itertuples(index=False):
        a=pd.Timestamp(r.t_in).tz_localize('UTC'); b=pd.Timestamp(r.t_out).tz_localize('UTC')
        out.append(dict(uniqueid=f'{r.sleeve}|{r.sym}|{r.side}|{a.isoformat()}',sleeve=r.sleeve,symbol=r.sym,side=r.side,entry_utc=a.isoformat(),exit_utc=b.isoformat(),net_R_at_cost=r.R,initial_notional_per_risk=1,risk_fraction=fr,max_positions=cfg['max_pos']))
    return out,cfg
for book,src,bp,t1 in [('A4_4h6','15',15,None),('A2_OLD3','long',15,'2026-09-28'),('A1','long',15,'2026-09-28'),('A2_OLD3','15',15,None)]:
    rr,cfg=rows(book,src,bp)
    if t1: rr=[r for r in rr if r['entry_utc']<t1]
    for pol,lab in [('baseline','stack'),('A','refuse')]:
        s=simulate(rr,cfg['sleeves'],pol,'fixed',multiple=2.0)['summary']
        print(book,src,bp,lab,'codex-overlay-on-my-tapes ret=%.1f%% admitted=%d'%((s['terminal_equity']/4000-1)*100,s['admitted']))
