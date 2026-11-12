import hashlib, json, math, sys
from collections import defaultdict
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112\clean')
PR=Path(r'F:\ZackBot2_data\H5-WIDE-PREREG-20261112\preregistration.json')
RULES=Path(r'F:\ZackBot2_data\CP4-independent-review-20261112\data\exchange_rules_testnet.json')
START=pd.Timestamp('2022-08-26T06:00Z'); END=pd.Timestamp('2026-10-04T04:00Z')
SPLIT=pd.Timestamp('2025-01-01T00:00Z'); SLIP=.0002; FEE=.0013
HOUR=pd.Timedelta(hours=1)

def floor_step(x, step):
    return float((Decimal(str(x))/Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)*Decimal(str(step)))
def size(open_, atr, rule):
    entry=open_*(1+SLIP); stop=entry-3*atr
    if stop<=0: return None
    qty=floor_step(min(8/(entry-stop), 333.3333*10/entry),rule['step'])
    if qty<rule['min_qty'] or qty*entry<rule['min_notional']: return None
    return entry,stop,qty
def cairo(t): return t.tz_convert('Africa/Cairo').date().isoformat()
def exit_bar(p,row,t):
    # Stop precedes target; a gap through a stop fills from the adverse open.
    if row.l <= p['stop']:
        return min(row.o,p['stop'])*(1-SLIP),'stop'
    if row.h >= p['target']:
        return max(row.o,p['target'])*(1-SLIP),'target'
    if t+HOUR-p['time']>=pd.Timedelta(hours=48):
        return row.c*(1-SLIP),'time'
    return None
def features(sym):
    path=ROOT/(sym+'_1h.csv')
    if not path.exists(): return None, 'missing CSV'
    d=pd.read_csv(path); d['time']=pd.to_datetime(d.t,unit='ms',utc=True)
    if d.time.duplicated().any() or not d.time.is_monotonic_increasing: return None,'duplicate or unordered timestamps'
    c,h,l=d.c,d.h,d.l
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    e50=c.ewm(span=50,adjust=False).mean(); e200=c.ewm(span=200,adjust=False).mean()
    b=(c-d.o).abs(); span=h-l
    d['signal']=((b>=d.atr)&(b<=3*d.atr)&(d.v>=2*d.v.rolling(24,min_periods=24).median().shift())&(span>0)&(c>d.o)&(c>=l+.8*span)&(e50>e200))
    # A daily observation counts only with all 24 distinct full hourly candles.
    d['day']=d.time.dt.floor('D'); daily=d.groupby('day').agg(qv=('qv','sum'),n=('t','size'))
    daily.loc[daily.n!=24,'qv']=np.nan
    dayidx=pd.date_range(d.day.min(),d.day.max(),freq='D',tz='UTC'); daily=daily.reindex(dayidx)
    liq=daily.qv.rolling(30,min_periods=30).median().shift(1)
    d['liq']=d.day.map(liq)>=10_000_000
    runs=d.time.diff().ne(HOUR).cumsum(); d['consecutive']=d.groupby(runs).cumcount()+1
    d['eligible']=d.liq & (d.consecutive>=720)
    return d.set_index('time',drop=False),None

def execute(symbols, all_data, rules):
    daily=defaultdict(float); trades=[]; skips=defaultdict(int); closed=4000.; peak_closed=4000.; peak_mark=4000.; dd_c=dd_m=0.; maxrisk=0.; pos=None; pending=None; eligible_hours=0; mark=4000.; errors=[]
    times=pd.date_range(START,END,freq='h'); indexes={s:set(d.index) for s,d in all_data.items() if s in symbols}
    for t in times:
        if pending:
            sym,atr,decision=pending; pending=None
            if t not in indexes.get(sym,set()): skips['stale_next_bar']+=1
            elif pos is None and daily[cairo(t)]>-120:
                row=all_data[sym].loc[t]; sz=size(row.o,atr,rules[sym])
                if sz is None: skips['filter_or_notional']+=1
                else:
                    entry,stop,qty=sz; risk=qty*(entry-stop); maxrisk=max(maxrisk,risk)
                    fee=entry*qty*FEE; closed-=fee; daily[cairo(t)]-=fee
                    pos=dict(symbol=sym,time=t,entry=entry,stop=stop,target=entry+4*(entry-stop),qty=qty,risk=risk,entryfee=fee,funding=0.,decision=decision)
            else: skips['daily_halt_or_busy']+=1
        exited=False
        if pos:
            sym=pos['symbol']
            if t not in indexes.get(sym,set()):
                errors.append(f'missing held bar {sym} {t.isoformat()}'); break
            row=all_data[sym].loc[t]; result=exit_bar(pos,row,t)
            # Conservative full-hour proxy, including every exit bar.
            f=row.c*pos['qty']*.0001/8; pos['funding']+=f; closed-=f; daily[cairo(t+HOUR)]-=f
            if result:
                price,reason=result; gross=(price-pos['entry'])*pos['qty']; fee=price*pos['qty']*FEE
                exit_time=t if reason=='stop' and row.o<=pos['stop'] else t+HOUR
                closed+=gross-fee; daily[cairo(exit_time)]+=gross-fee
                net=gross-fee-pos['entryfee']-pos['funding']
                trades.append(dict(**pos,exit_time=exit_time,exit_price=price,reason=reason,gross=gross,exitfee=fee,net=net,R=net/pos['risk'],budget_R=net/8))
                pos=None; exited=True
        active=[s for s in symbols if s in indexes and t in indexes[s] and bool(all_data[s].at[t,'eligible'])]
        if active: eligible_hours+=1
        if pos is None and daily[cairo(t+HOUR)]>-120 and t<END:
            for s in active:
                row=all_data[s].loc[t]
                if bool(row.signal): pending=(s,float(row.atr),t); break
        if pos:
            row=all_data[pos['symbol']].loc[t]; reserve=row.c*(1-SLIP)*pos['qty']*FEE
            mark=closed+(row.c*(1-SLIP)-pos['entry'])*pos['qty']-reserve
        else: mark=closed
        peak_closed=max(peak_closed,closed); peak_mark=max(peak_mark,mark)
        dd_c=max(dd_c,(peak_closed-closed)/peak_closed); dd_m=max(dd_m,(peak_mark-mark)/peak_mark)
    return trades,dict(closed=closed,marked=mark,terminal=pos,dd_closed=dd_c,dd_marked=dd_m,max_initial_risk=maxrisk,eligible_hours=eligible_hours,skips=dict(skips),errors=errors)

def hostile():
    rule=dict(step=.001,min_qty=.001,min_notional=5)
    entry,stop,qty=size(100,1,rule); assert qty*(entry-stop)<=8 and qty*(entry-stop)>7.99
    assert entry>100 and qty<=8/3
    p=dict(stop=97.,target=112.,time=START)
    row=type('Row',(),dict(o=95.,h=115.,l=94.,c=100.))()
    price,reason=exit_bar(p,row,START); assert reason=='stop' and abs(price-95*(1-SLIP))<1e-10
    row.o=100.; price,reason=exit_bar(p,row,START); assert reason=='stop'
    assert cairo(pd.Timestamp('2025-07-01T21:00Z'))=='2025-07-02'
    assert cairo(pd.Timestamp('2025-01-01T21:00Z'))=='2025-01-01'
    assert cairo(pd.Timestamp('2025-01-01T22:00Z'))=='2025-01-02'
    assert exit_bar(dict(stop=1,target=1000,time=START),type('Row',(),dict(o=100,h=101,l=99,c=100))(),START+pd.Timedelta(hours=47))[1]=='time'
    print('hostile core assertions passed')

def main():
    hostile()
    reg=json.loads(PR.read_text()); rules=json.loads(RULES.read_text())['symbols']; symbols=sorted(reg['original_symbols']+reg['extra_symbols'])
    data={}; excluded={}
    for s in symbols:
        if s not in rules: excluded[s]='missing testnet filter'; continue
        d,e=features(s)
        if e: excluded[s]=e
        else: data[s]=d
    print('loaded',len(data),'excluded',excluded,flush=True)
    output={'provisional':True,'excluded':excluded,'arms':{}}
    for name,arm in [('original9',reg['original_symbols']),('extra31',reg['extra_symbols']),('union40',symbols)]:
        trades,stats=execute(sorted(arm),data,rules)
        summary={}
        for label,arr in [('all',trades),('early',[x for x in trades if x['time']<SPLIT]),('later',[x for x in trades if x['time']>=SPLIT])]:
            summary[label]=dict(count=len(arr),meanR_actual=float(np.mean([x['R'] for x in arr])) if arr else None,meanR_budget=float(np.mean([x['budget_R'] for x in arr])) if arr else None,net=sum(x['net'] for x in arr))
        stats['summary']=summary; stats['closes_week_calendar']=len(trades)/((END-START).total_seconds()/604800); stats['closes_week_eligible']=len(trades)/(stats['eligible_hours']/168) if stats['eligible_hours'] else None
        output['arms'][name]=stats
        pd.DataFrame(trades).to_csv(Path(__file__).with_name(name+'_trades.csv'),index=False)
        print(name,stats,flush=True)
    Path(__file__).with_name('results.json').write_text(json.dumps(output,indent=2,default=str))
if __name__=='__main__': main()
