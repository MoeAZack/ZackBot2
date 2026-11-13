"""Independent multi-position admission/management review; imports no submitted strategy or runner."""
from pathlib import Path
import collections
import hashlib
import io
import json
import math
import subprocess
import numpy as np
import pandas as pd

WS=Path(__file__).resolve().parent
# Reuse our independently written input/indicator/signal construction, not Claude's implementation.
scope={}
src=(WS/'independent_sieve_review.py').read_text(encoding='utf-8')
exec(compile(src.split('cash={}; cash_rows=[]')[0],str(WS/'independent_sieve_review.py'),'exec'),scope)
REPO=scope['REPO']; hourly=scope['hourly']; coins=scope['coins']; original=scope['original']; rules=scope['rules']
four=scope['four']; four_sig=scope['signals']['exp_trend']; four_elig=scope['eligibility']; floor=scope['qty_floor']
OUT=Path(r'F:\ZackBot2_data\SIEVE-MP-REVIEW-20261113');OUT.mkdir(exist_ok=True)
HEAD='45fe30bcc8ea7b4ae1e5d3d66f7f3d1d73523b3f'
CP='646ab3fa1be92614998905e6ab3497ab03a7d382'
def blob(p,h=HEAD): return subprocess.check_output(['git','show',h+':'+p],cwd=REPO)
def reference(case,mp,mode='path',kind='trades'):
    if mp==1: folder='sieve_v1_panel'; tag=case
    else: folder='sieve_v1_mp';tag=case+'_mp'+str(mp)
    leaf=tag+('_signals.csv' if kind=='signals' else '_order0_'+mode+'_trades.csv')
    return pd.read_csv(io.BytesIO(blob('docs/research/extra31_screen/'+folder+'/'+leaf)))

daily={}; day_elig={}
for sym,d in hourly.items():
    g=d.set_index('t').resample('1D',label='left',closed='left')
    f=g.agg(dict(o='first',h='max',l='min',c='last',v='sum',qv='sum'))
    f=f[g.size()==24].dropna(subset=['o','h','l','c']).reset_index()
    f['e21']=f.c.ewm(span=21,adjust=False).mean()
    tr=np.maximum(f.h-f.l,np.maximum((f.h-f.c.shift()).abs().fillna(0),(f.l-f.c.shift()).abs().fillna(0)))
    f['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    daily[sym]=f
    q=d.set_index('t').qv.resample('1D').agg(['sum','count'])
    prior=q['sum'].where(q['count']==24).rolling(30,min_periods=30).median().shift(1)
    liq=(prior.reindex(pd.DatetimeIndex(d.t.dt.floor('D'))).to_numpy()>=1e7)&(np.arange(len(d))+1>=720)
    day_elig[sym]=(pd.Series(liq,index=pd.DatetimeIndex(d.t)).reindex(pd.DatetimeIndex(f.t)+pd.Timedelta(hours=23)).fillna(False).to_numpy(dtype=bool)
                   & (np.arange(len(f))>=60)&(f.t>=pd.Timestamp('2022-08-26 06:00')).to_numpy())

panel=pd.DataFrame({s:pd.Series(f.c.to_numpy(),index=pd.DatetimeIndex(f.t)) for s,f in daily.items()}).sort_index()
mean20=panel.ewm(span=20,adjust=False,min_periods=20).mean()
above=(panel>mean20).where(panel.notna())
breadth=above.sum(axis=1)/panel.notna().sum(axis=1).replace(0,np.nan)
up=(breadth>=.75)&(breadth.rolling(5).min().shift(1)<=.25)
down=(breadth<=.25)&(breadth.rolling(5).max().shift(1)>=.75)
up=up&~up.shift(1,fill_value=False);down=down&~down.shift(1,fill_value=False)
day_sig={}; raw=[]
for sym in coins:
    f=daily[sym]; idx=pd.DatetimeIndex(f.t)
    le=up.reindex(idx).fillna(False).to_numpy()&(f.c>f.e21).to_numpy()
    se=down.reindex(idx).fillna(False).to_numpy()&(f.c<f.e21).to_numpy()
    day_sig[sym]=dict(le=le,se=se,lx=np.zeros(len(f),bool),sx=np.zeros(len(f),bool))
    for side,a in [('long',le),('short',se)]:
        for i in np.flatnonzero(a):raw.append((sym,f.t.iloc[i].isoformat(),side,bool(day_elig[sym][i])))

signal_checks={}
def tuples(df):return set((r.sym,pd.Timestamp(r.decision_bar_open_utc).isoformat(),r.side,bool(r.eligible)) for r in df.itertuples())
own_day=pd.DataFrame(raw,columns=['sym','decision_bar_open_utc','side','eligible'])
own_day.to_csv(OUT/'cp_y2_independent_signals.csv',index=False)
for case,mps,own in [('exp_trend',[1,3,5],pd.read_csv(scope['OUT']/'exp_trend_independent_signals.csv')),
                     ('cp_y2_breadth_thrust_1d',[3,5],own_day)]:
    for mp in mps:
        ref=reference(case,mp,kind='signals');a,b=tuples(own),tuples(ref)
        signal_checks[case+'_mp'+str(mp)]={'matched':len(a&b),'missing':len(b-a),'extra':len(a-b),'eligible':int(own.eligible.sum()),'raw':len(own)}
        assert a==b and len(own)==len(ref),(case,mp,sorted(a-b)[:5],sorted(b-a)[:5])
        print('signals',case,mp,signal_checks[case+'_mp'+str(mp)],flush=True)

def simulate(case,mp,worst=False):
    is_daily=case.startswith('cp_y2');frames=daily if is_daily else four;sig=day_sig if is_daily else four_sig;elig=day_elig if is_daily else four_elig
    tl=pd.DatetimeIndex(frames['BTCUSDT'].t);n=len(tl); warm=60 if is_daily else 220;fpb=.0003 if is_daily else .00005
    arrays={};gs={}
    for s in coins:
        f=frames[s];off=int(tl.get_indexer([f.t.iloc[0]])[0])
        arrays[s]={}
        for k in ['o','h','l','c','atr']:
            x=np.full(n,np.nan);x[off:off+len(f)]=f[k].to_numpy();arrays[s][k]=x
        gs[s]={}
        for k,v in sig[s].items():
            x=np.zeros(n,bool);x[off:off+len(f)]=v&(elig[s] if k in ['le','se'] else True);gs[s][k]=x
    eq=1e7;positions={};pending={};closed=[];fills=[];peak=0;qtydiff=0
    def close(s,p,i,ref,frac,why):
        nonlocal eq
        step=rules[s].get('step');q=p['qty']*frac
        if frac<1 and step:q=floor(q,step)
        if q<=0:return False
        exit_px=ref*(1-p['side']*.0002);fee=q*exit_px*.0005
        pnl=p['side']*(exit_px-p['entry'])*q-fee
        eq+=pnl;p['pnl']+=pnl;p['fees']+=fee;p['gross']+=p['side']*(exit_px-p['entry'])*q
        p['turnover']+=ref*q;p['qty']-=q
        if step:p['qty']=max(0,floor(p['qty'],step))
        fills.append(dict(sym=s,i=i,pos_i=p['i'],kind='exit',qty=q,ref=ref,fill=exit_px,fee=fee,why=why))
        if p['qty']<=1e-12:
            closed.append(dict(sym=s,side=p['side'],i_in=p['i'],i_out=i,t_in=str(tl[p['i']]),t_out=str(tl[i]),why=why,
                               qty_entry=p['q0'],entry_fill=p['entry'],gross=p['gross'],fees7=p['fees'],funding=p['fund'],
                               turnover=p['turnover'],pnl7=p['pnl'],pnl15=p['pnl']-p['turnover']*.0008,pnl23=p['pnl']-p['turnover']*.0016))
            return True
        return False

    def manage(s,p,i):
        nonlocal eq
        sd=p['side'];a=arrays[s];o,h,l,c,atr=[a[k][i] for k in ['o','h','l','c','atr']];atr=a['atr'][i-1]
        funding=p['qty']*c*fpb;eq-=funding;p['pnl']-=funding;p['fund']+=funding
        if sd*(o-p['stop'])<=0:return close(s,p,i,o,1,'stop')
        stop_inside=sd*((l if sd==1 else h)-p['stop'])<=0
        points=([o,h,l,c] if sd==1 else [o,l,h,c]) if worst or c==o else ([o,l,h,c] if c>o else [o,h,l,c])
        blocked_partial=False
        for k,b in enumerate(points):
            a0=points[max(0,k-1)];move=sd*(b-a0)
            if k and move<0 and sd*(b-p['stop'])<=0:
                ref=p['stop'] if sd*(a0-p['stop'])>=0 else a0
                return close(s,p,i,ref,1,'stop')
            if (k==0 or move>0) and not stop_inside:
                events=[]
                if is_daily and not p['partial'] and not blocked_partial:
                    level=p['entry']+sd*p['dist']
                    if sd*(b-level)>=0:events.append((sd*level,0,level,'tp1'))
                target=p['entry']+sd*3*p['dist']
                if sd*(b-target)>=0:events.append((sd*target,1,target,'tp'))
                for _,_,level,why in sorted(events):
                    if why=='tp1':
                        q=floor(p['qty']*.5,rules[s].get('step'))
                        if q<=0:blocked_partial=True;continue
                        p['partial']=True
                        if close(s,p,i,level,.5,'tp1'):return True
                    elif close(s,p,i,level,1,'tp'):return True
            p['best']=max(p['best'],b) if sd==1 else min(p['best'],b)
            if k and is_daily and sd*(p['best']-(p['entry']+sd*p['dist']))>=0:
                p['stop']=max(p['stop'],p['entry']) if sd==1 else min(p['stop'],p['entry'])
        ex=gs[s]['lx' if sd==1 else 'sx'][i]
        if ex or i-p['i']>=(20 if is_daily else 180):return close(s,p,i,c,1,'signal' if ex else 'time')
        return False

    for i in range(warm,n):
        if tl[i]<pd.Timestamp('2022-08-26 06:00'):continue
        # Native engine captures sleeve equity once for this batch of pending entries.
        sleeve_eq=eq
        for s,sd in pending.items():
            if len(positions)>=mp:break
            a=arrays[s];ref=a['o'][i];entry=ref*(1+sd*.0002);dist=2.5*a['atr'][i-1]
            used=sum(p['qty']*arrays[z]['c'][i-1] for z,p in positions.items())
            raw=sleeve_eq*8/1e7/dist;cap=max(0,3333.33*sleeve_eq/1e7-used)/entry
            q=floor(min(raw,cap),rules[s].get('step'))
            if q<rules[s].get('min_qty',0) or q*entry<rules[s].get('min_notional',0) or q>rules[s].get('max_qty',float('inf')):continue
            fee=q*entry*.0005;eq-=fee
            positions[s]=dict(side=sd,qty=q,q0=q,entry=entry,dist=dist,stop=entry-sd*dist,best=entry,partial=False,
                              i=i,pnl=-fee,fees=fee,fund=0.,gross=0.,turnover=ref*q)
            fills.append(dict(sym=s,i=i,pos_i=i,kind='entry',qty=q,ref=ref,fill=entry,fee=fee,why='entry'))
        pending={};peak=max(peak,len(positions))
        for s,p in list(positions.items()):
            if manage(s,p,i):del positions[s]
        if i+1<n:
            for s in coins:
                if s in positions:continue
                side=1 if gs[s]['le'][i] else (-1 if gs[s]['se'][i] else 0)
                if side:pending[s]=side
    terminal=[]
    for s,p in positions.items():
        mark=arrays[s]['c'][-1];xp=mark*(1-p['side']*.0002)
        marked=p['pnl']+p['side']*(mark-p['entry'])*p['qty']
        liquidation=p['pnl']+p['side']*(xp-p['entry'])*p['qty']-xp*p['qty']*.0005
        terminal.append(dict(sym=s,qty=p['qty'],t_in=str(tl[p['i']]),side=p['side'],mark=mark,
                             marked_pnl7=marked,marked_pnl15=marked-p['turnover']*.0008,marked_pnl23=marked-p['turnover']*.0016,
                             liquidation_pnl15=liquidation-(p['turnover']+mark*p['qty'])*.0008,
                             liquidation_pnl23=liquidation-(p['turnover']+mark*p['qty'])*.0016,
                             note='Cumulative lot PnL includes realized partials and remaining open mark; not just unrealized PnL.'))
    return closed,fills,terminal,peak

results={}
for case,mps in [('exp_trend',[1,3,5]),('cp_y2_breadth_thrust_1d',[3,5])]:
    for mp in mps:
        for mode in ['path','worst']:
            own,fills,terminal,peak=simulate(case,mp,mode=='worst'); ref=reference(case,mp,mode)
            def identity(r):return(r['sym'],int(r['side']),pd.Timestamp(r['t_in']).isoformat(),pd.Timestamp(r['t_out']).isoformat(),r['why'])
            actual=[identity(r) for r in own]; expected=[identity(r) for r in ref.to_dict('records')]
            a,b=collections.Counter(actual),collections.Counter(expected); missing=list((b-a).elements());extra=list((a-b).elements())
            same=actual==expected
            if same:errors={k:max([abs(r[k]-v) for r,v in zip(own,ref[k])],default=0) for k in ['pnl7','pnl15','pnl23','turnover']}
            else:errors={}
            tag=case+'_mp'+str(mp)+'_'+mode
            results[tag]=dict(closed_own=len(own),closed_reference=len(ref),matched=sum((a&b).values()),missing=len(missing),extra=len(extra),
                              ordered_set_equal=same,errors=errors,missing_examples=missing[:5],extra_examples=extra[:5],
                              terminal_open_lots=terminal,max_concurrent=peak,closed_net15_usd=sum(r['pnl15'] for r in own),
                              partial_fill_count=sum(f['why']=='tp1' for f in fills),distinct_entry_timestamps=len(set(r['t_in'] for r in own)),
                              early_entry_timestamps=len(set(r['t_in'] for r in own if pd.Timestamp(r['t_in'])<pd.Timestamp('2025-01-01'))),
                              later_entry_timestamps=len(set(r['t_in'] for r in own if pd.Timestamp(r['t_in'])>=pd.Timestamp('2025-01-01'))))
            pd.DataFrame(own).to_csv(OUT/(tag+'_own_trades.csv'),index=False)
            pd.DataFrame(fills).to_csv(OUT/(tag+'_own_fills.csv'),index=False)
            print('RESULT',tag,results[tag],flush=True)
            # Keep mismatch evidence instead of hiding a defect behind a failed run with no receipt.
            (OUT/'independent_mp_review.json').write_text(json.dumps(dict(head=HEAD,cp_source=CP,
                independent_base_source_sha256=hashlib.sha256(src.replace('\r\n','\n').encode()).hexdigest(),
                signals=signal_checks,cases=results),indent=2,default=str))
assert all(v['ordered_set_equal'] and all(e<1e-8 for e in v['errors'].values()) for v in results.values()), 'Independent trade mismatch; see receipt'
print('COMPLETE',str(OUT/'independent_mp_review.json'),flush=True)
