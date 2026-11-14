"""Independent LC5 signal, eligibility, admission and fixed-management replay. No Claude runner imports."""
from pathlib import Path
import json, hashlib, math
import pandas as pd
import numpy as np

ROOT=Path(r'F:\ZackBot2_data\LONGSCOUT-REVIEW-20261114')
DATA=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112\clean')
OUT=Path(r'F:\ZackBot2_data\LONGSCOUT-REVIEW-20261114-evidence')
REF=ROOT/'docs/research/longscout_v1'
case='lc_volume_accum_1d'
expected=json.loads((REF/f'{case}_inputs.json').read_text())
coins=json.loads((REF/f'{case}_result.json').read_text())['coins']
frames={}; eligibility={}; hashes={}
for sym,pin in expected['csv_sha256'].items():
    p=DATA/f'{sym}_1h.csv'; hashes[sym]=hashlib.sha256(p.read_bytes()).hexdigest()
    assert hashes[sym]==pin, sym
    h=pd.read_csv(p); h.t=pd.to_datetime(h.t,unit='ms'); assert h.t.is_unique and h.t.is_monotonic_increasing
    day=h.t.dt.floor('D')
    dv=h.groupby(day).qv.sum().where(h.groupby(day).size()==24)
    dv=dv.reindex(pd.date_range(dv.index.min(),dv.index.max(),freq='D'))
    median=dv.rolling(30,min_periods=30).median().shift()
    runs=h.groupby(h.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount()+1
    eligibility[sym]=pd.Series((median.reindex(day).values>=1e7)&(runs.values>=720),index=h.t)
    g=h.set_index('t').resample('1D')
    d=g.agg(dict(o='first',h='max',l='min',c='last',v='sum'))
    d=d[g.size()==24].dropna().reset_index()
    d['e50']=d.c.ewm(span=50,adjust=False).mean()
    tr=pd.concat([d.h-d.l,(d.h-d.c.shift()).abs(),(d.l-d.c.shift()).abs()],axis=1).max(axis=1)
    d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    frames[sym]=d
btc=frames['BTCUSDT']; mean=btc.c.rolling(100).mean(); slope=mean-mean.shift(20)
reg=np.where((btc.c>mean)&(slope>0),1,np.where((btc.c<mean)&(slope<0),-1,0)).astype(float)
reg[(np.arange(len(btc))<119)|mean.isna()|slope.isna()]=np.nan
btc_reg=pd.Series(reg,index=btc.t)
timeline=pd.DatetimeIndex(btc.t); sigs={}; aligned={}; sigrows=[]
for sym in coins:
    d=frames[sym]
    br=btc_reg.reindex(d.t).values
    raw=(d.v>=2.5*d.v.rolling(50).median().shift())&((d.c-d.l)/(d.h-d.l).replace(0,np.nan)>=.75)&(d.c>d.o)&(d.c>d.e50)&(br>=0)
    elig=eligibility[sym].reindex(d.t+pd.Timedelta(hours=23)).fillna(False).values & (np.arange(len(d))>=60)&(d.t>=pd.Timestamp('2022-08-26 06:00')).values
    for j in np.flatnonzero(raw): sigrows.append((sym,d.t.iloc[j],'long',bool(elig[j])))
    sigs[sym]=pd.Series(raw.values&elig,index=d.t).reindex(timeline,fill_value=False).values
    aligned[sym]=d.set_index('t').reindex(timeline)
mine=pd.DataFrame(sigrows,columns=['sym','decision_bar_open_utc','side','eligible'])
refsig=pd.read_csv(REF/f'{case}_signals.csv',parse_dates=['decision_bar_open_utc'])
def tuples(x): return set(x.itertuples(index=False,name=None))
signal_missing=tuples(refsig)-tuples(mine); signal_extra=tuples(mine)-tuples(refsig)
assert not signal_missing and not signal_extra,(len(signal_missing),len(signal_extra))
rules=json.loads((ROOT/'data/exchange_rules_testnet.json').read_text())['symbols']
cash=1e7; position=None; pending=[]; trades=[]; attempts=0; skipped=0
for i in range(60,len(timeline)):
    if timeline[i]<pd.Timestamp('2022-08-26 06:00'): continue
    for sym in pending:
        if position is not None: break
        d=aligned[sym]; px=float(d.o.iloc[i])*1.0002; dist=2*float(d.atr.iloc[i-1]); risk=cash*8e-7
        q=min(risk/dist,3333.33/1e7*cash/px); r=rules.get(sym,dict(min_notional=5,min_qty=0,step=None))
        if r.get('step'):
            step=r['step']; q=round(math.floor(q/step+1e-9)*step,max(0,-int(math.floor(math.log10(step)))) if step<1 else 0)
        attempts+=1
        if q<r.get('min_qty',0) or q*px<r.get('min_notional',0) or (r.get('max_qty') and q>r['max_qty']*(1+1e-12)):
            skipped+=1; continue
        fee=q*px*.0005; cash-=fee
        position=dict(sym=sym,i=i,qty=q,px=px,stop=px-dist,target=px+3*dist,risk=risk,fees=fee,fund=0.,net=-fee,turn=float(d.o.iloc[i])*q)
    pending=[]
    if position:
        p=position; d=aligned[p['sym']]; o,h,l,c=[float(d[k].iloc[i]) for k in ['o','h','l','c']]
        fund=p['qty']*c*.0003; cash-=fund; p['net']-=fund; p['fund']+=fund
        ref=None; why=None
        if o<=p['stop']: ref=o; why='stop'
        elif l<=p['stop']: ref=p['stop']; why='stop'
        elif h>=p['target']: ref=p['target']; why='tp'
        elif i-p['i']>=15: ref=c; why='time'
        if ref is not None:
            exitpx=ref*.9998; fee=p['qty']*exitpx*.0005
            inc=(exitpx-p['px'])*p['qty']-fee; cash+=inc; p['net']+=inc; p['fees']+=fee
            turnover=p['turn']+ref*p['qty']
            trades.append(dict(sym=p['sym'],side=1,i_in=p['i'],i_out=i,t_in=timeline[p['i']],t_out=timeline[i],why=why,pnl=p['net'],pnl7=p['net'],pnl15=p['net']-turnover*.0008,pnl23=p['net']-turnover*.0016,R=p['net']/p['risk'],turnover=turnover,quantity=p['qty'],entry=p['px'],exit=exitpx,fees=p['fees'],funding=p['fund']))
            position=None
    if i+1<len(timeline): pending=[s for s in coins if sigs[s][i] and (position is None or position['sym']!=s)]
actual=pd.DataFrame(trades); reference=pd.read_csv(REF/f'{case}_order0_path_trades.csv',parse_dates=['t_in','t_out'])
keys=['sym','side','i_in','i_out','t_in','t_out','why']
matched=actual.merge(reference,on=keys,suffixes=('_own','_ref'),how='outer',indicator=True)
num=['pnl','pnl7','pnl15','pnl23','R','turnover']
diff={k:float((matched[k+'_own']-matched[k+'_ref']).abs().max()) for k in num}
report=dict(head='0a54ad4c92f699dcf15635859dfd4766d4c25276',method='Own raw CSV daily aggregation, EMA/ATR, BTC R1, D01 eligibility, chronological max-one admission, sizing, filters, gap/stop/target/time execution and cash/fees/funding. No imports from repository code.',inputs_verified=len(hashes),raw_signals=len(mine),eligible_signals=int(mine.eligible.sum()),signal_matched=len(tuples(mine)&tuples(refsig)),signal_missing=len(signal_missing),signal_extra=len(signal_extra),trades_own=len(actual),trades_ref=len(reference),matched=int((matched._merge=='both').sum()),missing=int((matched._merge=='right_only').sum()),extra=int((matched._merge=='left_only').sum()),max_abs_differences=diff,net15=float(actual.pnl15.sum()),meanR15=float(actual.pnl15.mean()/8),early=int((actual.t_in<pd.Timestamp('2025-01-01')).sum()),late=int((actual.t_in>=pd.Timestamp('2025-01-01')).sum()),attempts=attempts,skipped=skipped,unclosed_position=position,known_limits='Uses frozen historical testnet filter snapshot; no terminal mark in compared closed-trade result. 1e7 sizing device replicated, not a real 4000 shared-account test. Research only.')
OUT.mkdir(parents=True,exist_ok=True); actual.to_csv(OUT/'volume_own_trades.csv',index=False)
(OUT/'volume_result.json').write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
print(json.dumps(report,indent=2,default=str))
assert report['missing']==report['extra']==0
assert all(v<1e-7 for v in diff.values())
