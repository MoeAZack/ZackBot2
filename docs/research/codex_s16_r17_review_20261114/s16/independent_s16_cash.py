"""Own raw signals, admission, sizing, exits and cash. No research/engine imports."""
from pathlib import Path
import json,hashlib,subprocess,io,math,argparse
import pandas as pd
import numpy as np
REPO=Path(r'F:\ZackBot2_data\S16-REVIEW-20261114')
FRESH=Path(r'C:\Dev\ZackBot_research_cache\longscout_fresh_20261114')
OLD=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112')
OUT=Path(r'F:\ZackBot2_data\S16-REVIEW-20261114\own_cash'); OUT.mkdir(parents=True,exist_ok=True)
parser=argparse.ArgumentParser(); parser.add_argument('--fit',type=Path); args=parser.parse_args()
if args.fit:
    OUT=Path(r'F:\ZackBot2_data\REGIME-FLIP-REVIEW-20261114\fit\own_cash'); OUT.mkdir(exist_ok=True,parents=True)
HEAD='d9bf7bf75bd274e47e72dc4fc33bf10d32802895'; KEY='s16_inside_day_breakdown_4h'; PREFIX='docs/research/longscout_v16/'+KEY+'/'
def blob(p): return subprocess.check_output(['git','show',HEAD+':'+p],cwd=REPO)
published=json.loads(blob(PREFIX+KEY+'_result.json')); coins=published['coins']; assert len(coins)==165 and coins==sorted(coins)
if args.fit:
    fit=json.loads((args.fit/'freeze.json').read_text()); assert fit['excluded_unavailable']==['ICPUSDT','TLMUSDT']
    assert hashlib.sha256((args.fit/'rules.json').read_bytes()).hexdigest()==fit['rules_sha256']
    coins=fit['symbols']; assert len(coins)==163 and coins==sorted(set(published['coins'])-set(fit['excluded_unavailable']))
fm=(FRESH/'input_manifest.json').read_bytes(); om=(OLD/'input_manifest.json').read_bytes()
assert hashlib.sha256(fm).hexdigest()=='fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e'
assert hashlib.sha256(om).hexdigest()=='99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95'
fresh=json.loads(fm)['symbols']; old=json.loads(om)['symbols']; frames={}; sigs={}; sigrows=[]; hashpins={}
for sym in ['BTCUSDT']+coins:
    isfresh=sym in fresh; root=FRESH if isfresh else OLD; data=(root/'clean'/f'{sym}_1h.csv').read_bytes()
    pin=fresh[sym]['sha256'] if isfresh else old[sym]['csv_sha256']; assert hashlib.sha256(data).hexdigest()==pin,sym
    hashpins[sym]=pin; h=pd.read_csv(io.BytesIO(data)); h.t=pd.to_datetime(h.t,unit='ms'); assert h.t.is_unique and h.t.is_monotonic_increasing
    day=h.t.dt.floor('D'); q=h.groupby(day).qv.sum().where(h.groupby(day).size()==24); q=q.reindex(pd.date_range(q.index.min(),q.index.max(),freq='D')); med=q.rolling(30).median().shift()
    runs=h.groupby(h.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount()+1
    liq=pd.Series((med.reindex(day).values>=1e7)&(runs.values>=720),index=h.t)
    g=h.set_index('t').resample('4h'); d=g.agg(dict(o='first',h='max',l='min',c='last',v='sum')); d=d[g.size()==4].dropna().reset_index()
    tr=pd.concat([d.h-d.l,(d.h-d.c.shift()).abs(),(d.l-d.c.shift()).abs()],axis=1).max(axis=1); d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    day=d.t.dt.floor('D'); groups=d.groupby(day); dc=groups.c.agg(['last','count']); dc=dc['last'][dc['count']==6]; mean=dc.rolling(100).mean(); slope=mean-mean.shift(20); reg=np.where((dc<mean)&(slope<0),-1,0); reg=pd.Series(reg,index=dc.index+pd.Timedelta(days=1)); cr=reg.reindex(pd.DatetimeIndex(d.t)+pd.Timedelta(hours=4),method='ffill').to_numpy()
    tab=groups.agg(high=('h','max'),low=('l','min'),count=('c','size')); tab=tab[tab['count']==6]; valid=tab.index.to_series().diff().eq(pd.Timedelta(days=1)).to_numpy(); inside=(tab.high.to_numpy()<tab.high.shift().to_numpy())&(tab.low.to_numpy()>tab.low.shift().to_numpy())&valid
    src=pd.DataFrame({'inside':inside,'lo':tab.low.to_numpy()},index=tab.index+pd.Timedelta(days=1)); mapped=src.reindex(pd.DatetimeIndex(d.t)+pd.Timedelta(hours=4),method='ffill'); low=pd.Series(mapped.lo.to_numpy()); ins=mapped.inside.fillna(False).to_numpy(); close=d.c.reset_index(drop=True); se=((close<low)&(close.shift()>=low.shift())).to_numpy()&ins&(cr==-1); le=np.zeros(len(d),dtype=bool)
    elig=liq.reindex(d.t+pd.Timedelta(hours=3)).fillna(False).values & (np.arange(len(d))>=220)&(d.t>=pd.Timestamp('2022-08-26 06:00')).values
    if sym!='BTCUSDT':
        for side,g in [('long',le),('short',se)]:
            for j in np.flatnonzero(g): sigrows.append((sym,d.t.iloc[j],side,bool(elig[j])))
        sigs[sym]={'le':pd.Series(le&elig,index=d.t),'se':pd.Series(se&elig,index=d.t),'lx':pd.Series(False,index=d.t),'sx':pd.Series(False,index=d.t)}
    frames[sym]=d
refsig=pd.read_csv(io.BytesIO(blob(PREFIX+KEY+'_signals.csv')),parse_dates=['decision_bar_open_utc']); refsig=refsig[refsig.sym.isin(coins)]; expected=set(refsig.itertuples(index=False,name=None)); actual=set(sigrows); assert expected==actual,(len(expected-actual),len(actual-expected))
tl=pd.DatetimeIndex(frames['BTCUSDT'].t); arrays={}; signals={}; candidates={}
for sym in coins:
    a=frames[sym].set_index('t').reindex(tl); arrays[sym]={k:a[k].values for k in ['o','h','l','c','atr']}; signals[sym]={k:x.reindex(tl,fill_value=False).values for k,x in sigs[sym].items()}
    for key,sd in [('le',1),('se',-1)]:
        for i in np.flatnonzero(signals[sym][key]): candidates.setdefault(i,[]).append((sym,sd))
rules=json.loads(subprocess.check_output(['git','show','b9ac5a4064b0c6cff2738503aadf5fec069973c8:data/exchange_rules_testnet.json'],cwd=REPO))['symbols']
if args.fit: rules=json.loads((args.fit/'rules.json').read_text())['symbols']
def replay(order):
    rank={s:j for j,s in enumerate(order)}; cash=1e7; pos=None; pending=[]; rows=[]
    for i in range(220,len(tl)):
        if tl[i]<pd.Timestamp('2022-08-26 06:00'): continue
        for sym,sd in pending:
            if pos is not None: break
            a=arrays[sym]; entry=a['o'][i]*(1+sd*.0002); dist=2.5*a['atr'][i-1]; risk=cash*8e-7; qty=min(risk/dist,(3333.33/1e7)*cash/entry)
            rule=rules.get(sym,dict(step=None,min_qty=0,min_notional=5))
            if rule.get('step'):
                step=rule['step']; qty=round(math.floor(qty/step+1e-9)*step,max(0,-int(math.floor(math.log10(step)))) if step<1 else 0)
            if qty<rule.get('min_qty',0) or qty*entry<rule.get('min_notional',0) or (rule.get('max_qty') and qty>rule['max_qty']*(1+1e-12)): continue
            fee=qty*entry*.0005; cash-=fee; pos=dict(sym=sym,sd=sd,entry=entry,stop=entry-sd*dist,qty=qty,i=i,risk=risk,net=-fee,turn=qty*a['o'][i],fees=fee,funding=0.)
        pending=[]
        if pos:
            p=pos; a=arrays[p['sym']]; sd=p['sd']; o,hi,lo,c=(a[k][i] for k in ['o','h','l','c']); fund=p['qty']*c*.00005; p['net']-=fund; p['funding']+=fund; cash-=fund
            ref=None; why=None
            if sd*(o-p['stop'])<=0: ref=o; why='stop'
            elif (lo<=p['stop'] if sd==1 else hi>=p['stop']): ref=p['stop']; why='stop'
            elif (hi>=p['entry']+2.5*(p['entry']-p['stop']) if sd==1 else lo<=p['entry']-2.5*(p['stop']-p['entry'])): ref=p['entry']+sd*2.5*abs(p['entry']-p['stop']); why='tp'
            elif i-p['i']>=30: ref=c; why='time'
            if ref is not None:
                exitpx=ref*(1-sd*.0002); fee=p['qty']*exitpx*.0005; inc=sd*(exitpx-p['entry'])*p['qty']-fee; cash+=inc; p['net']+=inc; p['fees']+=fee; turn=p['turn']+ref*p['qty']
                rows.append(dict(sym=p['sym'],side=sd,i_in=p['i'],i_out=i,t_in=tl[p['i']],t_out=tl[i],why=why,pnl=p['net'],R=p['net']/p['risk'],turnover=turn,pnl7=p['net'],pnl15=p['net']-turn*.0008,pnl23=p['net']-turn*.0016,qty=p['qty'],entry=p['entry'],exit=exitpx,fees=p['fees'],funding=p['funding']))
                pos=None
        if i+1<len(tl): pending=sorted([x for x in candidates.get(i,[]) if pos is None or x[0]!=pos['sym']],key=lambda x:rank[x[0]])
    return pd.DataFrame(rows),pos

t,terminal=replay(coins)
ref=pd.read_csv(io.BytesIO(blob(PREFIX+KEY+'_order0_path_trades.csv')))
print('reference',len(ref),'own',len(t),flush=True)
keys=['sym','side','i_in','i_out','why']; matches=list(zip(*(ref[k] for k in keys)))==list(zip(*(t[k] for k in keys))) if len(ref)==len(t) else False
print('sequence match',matches,flush=True)
if len(ref)==len(t):
 print('max differences',{k:float(np.max(abs(ref[k].values-t[k].values))) for k in ['pnl','R','pnl7','pnl15','pnl23','turnover']},flush=True)
else:
 for i,(a,b) in enumerate(zip(ref.itertuples(),t.itertuples())):
  if (a.sym,a.i_in,a.i_out,a.why)!=(b.sym,b.i_in,b.i_out,b.why): print('first mismatch',i,a.sym,a.i_in,a.i_out,a.why,b.sym,b.i_in,b.i_out,b.why);break
print('sum R15',float(t.pnl15.sum()/8),'mean R15',float(t.pnl15.mean()/8),'n early',int((t.t_in<pd.Timestamp('2025-01-01')).sum()),'late',int((t.t_in>=pd.Timestamp('2025-01-01')).sum()),flush=True)
t.to_csv(OUT/'order0_own_trades.csv',index=False)
