"""Own raw signals, admission, sizing, exits and cash. No research/engine imports."""
from pathlib import Path
import json,hashlib,subprocess,io,math,argparse
import pandas as pd
import numpy as np
REPO=Path(r'F:\ZackBot2_data\C1H-20261112\review-branch')
FRESH=Path(r'C:\Dev\ZackBot_research_cache\longscout_fresh_20261114')
OLD=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112')
OUT=Path(r'F:\ZackBot2_data\REGIME-FLIP-REVIEW-20261114\own_cash'); OUT.mkdir(parents=True,exist_ok=True)
parser=argparse.ArgumentParser(); parser.add_argument('--fit',type=Path); args=parser.parse_args()
if args.fit:
    OUT=Path(r'F:\ZackBot2_data\REGIME-FLIP-REVIEW-20261114\fit\own_cash'); OUT.mkdir(exist_ok=True,parents=True)
HEAD='10486f35dd513c51b375b3b72cfe5864763a7b39'; KEY='r6_regime_flip_4h'; PREFIX='docs/research/longscout_v6/'+KEY+'/'
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
    dayg=d.set_index('t').c.resample('1D'); dc=dayg.last()[dayg.count()==6]; sma=dc.rolling(100).mean(); slope=sma-sma.shift(20)
    reg=np.where((dc>sma)&(slope>0),1,np.where((dc<sma)&(slope<0),-1,0)).astype(float); reg[slope.isna()]=np.nan
    available=pd.Series(reg,index=dc.index+pd.Timedelta(days=1)); cr=available.reindex(pd.DatetimeIndex(d.t)+pd.Timedelta(hours=4),method='ffill').values
    up=pd.Series(cr==1); dn=pd.Series(cr==-1); le=(up&~up.shift(fill_value=False)).values; se=(dn&~dn.shift(fill_value=False)).values
    elig=liq.reindex(d.t+pd.Timedelta(hours=3)).fillna(False).values & (np.arange(len(d))>=220)&(d.t>=pd.Timestamp('2022-08-26 06:00')).values
    if sym!='BTCUSDT':
        for side,g in [('long',le),('short',se)]:
            for j in np.flatnonzero(g): sigrows.append((sym,d.t.iloc[j],side,bool(elig[j])))
        sigs[sym]={'le':pd.Series(le&elig,index=d.t),'se':pd.Series(se&elig,index=d.t),'lx':pd.Series(~up.values,index=d.t),'sx':pd.Series(~dn.values,index=d.t)}
    frames[sym]=d
refsig=pd.read_csv(io.BytesIO(blob(PREFIX+KEY+'_signals.csv')),parse_dates=['decision_bar_open_utc']); refsig=refsig[refsig.sym.isin(coins)]; expected=set(refsig.itertuples(index=False,name=None)); actual=set(sigrows); assert expected==actual,(len(expected-actual),len(actual-expected))
tl=pd.DatetimeIndex(frames['BTCUSDT'].t); arrays={}; signals={}; candidates={}
for sym in coins:
    a=frames[sym].set_index('t').reindex(tl); arrays[sym]={k:a[k].values for k in ['o','h','l','c','atr']}; signals[sym]={k:x.reindex(tl,fill_value=False).values for k,x in sigs[sym].items()}
    for key,sd in [('le',1),('se',-1)]:
        for i in np.flatnonzero(signals[sym][key]): candidates.setdefault(i,[]).append((sym,sd))
rules=json.loads(subprocess.check_output(['git','show','acc8d0ee:data/exchange_rules_testnet.json'],cwd=REPO))['symbols']
if args.fit: rules=json.loads((args.fit/'rules.json').read_text())['symbols']
def replay(order):
    rank={s:j for j,s in enumerate(order)}; cash=1e7; pos=None; pending=[]; rows=[]
    for i in range(220,len(tl)):
        if tl[i]<pd.Timestamp('2022-08-26 06:00'): continue
        for sym,sd in pending:
            if pos is not None: break
            a=arrays[sym]; entry=a['o'][i]*(1+sd*.0002); dist=3*a['atr'][i-1]; risk=cash*8e-7; qty=min(risk/dist,(3333.33/1e7)*cash/entry)
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
            elif signals[p['sym']]['lx' if sd==1 else 'sx'][i]: ref=c; why='signal'
            elif i-p['i']>=120: ref=c; why='time'
            if ref is not None:
                exitpx=ref*(1-sd*.0002); fee=p['qty']*exitpx*.0005; inc=sd*(exitpx-p['entry'])*p['qty']-fee; cash+=inc; p['net']+=inc; p['fees']+=fee; turn=p['turn']+ref*p['qty']
                rows.append(dict(sym=p['sym'],side=sd,i_in=p['i'],i_out=i,t_in=tl[p['i']],t_out=tl[i],why=why,pnl=p['net'],R=p['net']/p['risk'],turnover=turn,pnl7=p['net'],pnl15=p['net']-turn*.0008,pnl23=p['net']-turn*.0016,qty=p['qty'],entry=p['entry'],exit=exitpx,fees=p['fees'],funding=p['funding']))
                pos=None
        if i+1<len(tl): pending=sorted([x for x in candidates.get(i,[]) if pos is None or x[0]!=pos['sym']],key=lambda x:rank[x[0]])
    return pd.DataFrame(rows),pos
stats=[]; terminal_summaries=[]; matched=0; diffs={}; subsets={}; nativecases=published['per_order']['path']
for k in range(40):
    order=coins if k==0 else list(np.random.default_rng(20261112+k).permutation(coins)); t,terminal=replay(order); early=t.t_in<pd.Timestamp('2025-01-01'); best=t.groupby('sym').pnl15.sum().idxmax()
    row=dict(n=len(t),meanR15=t.pnl15.mean()/8,meanR23=t.pnl23.mean()/8,meanR15_early=t.loc[early,'pnl15'].mean()/8,meanR15_late=t.loc[~early,'pnl15'].mean()/8,meanR15_ex_best=t.loc[t.sym!=best,'pnl15'].mean()/8)
    if not args.fit: assert all(np.isclose(v,nativecases[k][key],rtol=1e-9,atol=1e-9) for key,v in row.items()),(k,row,nativecases[k])
    row['fresh_R15']=t.loc[t.sym.isin(fresh),'pnl15'].mean()/8; row['extra_R15']=t.loc[~t.sym.isin(fresh),'pnl15'].mean()/8; stats.append(row)
    mark15=0.
    if terminal:
        p=terminal; close=arrays[p['sym']]['c'][-1]; xp=close*(1-p['sd']*.0002)
        mark7=p['net']+p['sd']*(xp-p['entry'])*p['qty']-p['qty']*xp*.0005
        mark15=mark7-(p['turn']+close*p['qty'])*.0008
    terminal_summaries.append(dict(order=k,symbol=terminal['sym'] if terminal else None,terminal_liquidation_mark15=float(mark15),closed_pnl15=float(t.pnl15.sum()),reported_4000_equity15=float(4000+t.pnl15.sum()+mark15)))
    if k==0 and not args.fit:
        ref=pd.read_csv(io.BytesIO(blob(PREFIX+KEY+'_order0_path_trades.csv'))); assert len(ref)==len(t)
        assert list(zip(ref.sym,ref.side,ref.i_in,ref.i_out,ref.why))==list(zip(t.sym,t.side,t.i_in,t.i_out,t.why))
        matched=len(t); diffs={x:float(np.max(abs(ref[x].values-t[x].values))) for x in ['pnl','R','pnl7','pnl15','pnl23','turnover']}; assert max(diffs.values())<1e-7
        t.to_csv(OUT/'order0_own_trades.csv',index=False)
    if k%10==0: print('own replay complete order',k,flush=True)
med=pd.DataFrame(stats).median().to_dict(); report=dict(head=HEAD,source='acc8d0eea5fbc964d45d582923732e2bbb5ba322',inputs_verified=len(hashpins),signals_matched=len(actual),eligible=sum(x[3] for x in sigrows),missing=0,extra=0,order0_trades_matched=matched,order0_max_abs_differences=diffs,orders_independently_replayed=40,medians=med,verdict='Economic screen reproduced; consumed-data lead only, not mainnet or account activation.',method='Own 1h aggregation, R1 daily availability, ATR, signals, liquidity, native-capacity convention, floor sizing, stop/gap/signal/time fills, fees/slippage/funding and cash. Imports no submitted strategy, runner or native execution engine. Historical filter snapshot/proxy and 10m sizing device preserved for parity.')
report['terminal_open_orders_count']=sum(x['symbol'] is not None for x in terminal_summaries)
report['terminal_mark15_min']=min(x['terminal_liquidation_mark15'] for x in terminal_summaries)
report['terminal_mark15_max']=max(x['terminal_liquidation_mark15'] for x in terminal_summaries)
if args.fit:
    report['trial']=fit['id']; report['symbols']=len(coins); report['excluded_unavailable']=fit['excluded_unavailable']
    report['scope']='Deployment-fit variant, not direct165 trade-file comparison; same independently verified alpha/execution code, current testnet filters and predeclared venue163 only.'
    o0,unused=replay(coins); early=o0.t_in<pd.Timestamp('2025-01-01')
    report['order0_half_counts']=[int(early.sum()),int((~early).sum())]
    report['gates']=dict(trades=med['n']>=100,R15=med['meanR15']>0,R23=med['meanR23']>=0,early=med['meanR15_early']>0,late=med['meanR15_late']>0,ex_best=med['meanR15_ex_best']>0,half_samples=min(report['order0_half_counts'])>=30,fresh=med['fresh_R15']>0,extra=med['extra_R15']>0)
    report['verdict']='FIT_PASS' if all(report['gates'].values()) else 'FIT_FAIL'
    o0.to_csv(OUT/'order0_own_trades.csv',index=False)
(OUT/'terminal_marks.json').write_text(json.dumps(terminal_summaries,indent=2)+'\n',encoding='utf-8',newline='\n')
(OUT/'per_order_stats.json').write_text(json.dumps(stats,indent=2)+'\n',encoding='utf-8',newline='\n')
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n'); print(json.dumps(report,indent=2))
