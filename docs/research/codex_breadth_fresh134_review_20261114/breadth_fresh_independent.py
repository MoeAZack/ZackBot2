"""Independent breadth signals/D01 and cash arithmetic; reviewed native engine for admission."""
from pathlib import Path
from types import SimpleNamespace
import sys,subprocess,json,hashlib,io
import pandas as pd
import numpy as np
R=Path(r'F:\ZackBot2_data\C1H-20261112\review-branch')
DATA=Path(r'C:\Dev\ZackBot_research_cache\longscout_fresh_20261114')
O=Path(r'F:\ZackBot2_data\BREADTH-FRESH134-REVIEW-20261114'); O.mkdir(exist_ok=True,parents=True)
HEAD='6907a9e5e8280a42b7ad377aefd3507275680235'; KEY='cp_y2_breadth_thrust_1d'
def blob(p): return subprocess.check_output(['git','show',HEAD+':'+p],cwd=R)
def artifact(p): return pd.read_csv(io.BytesIO(blob(p)))
man=(DATA/'input_manifest.json').read_bytes(); assert hashlib.sha256(man).hexdigest()=='fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e'
pins=json.loads(man)['symbols']; coins=sorted(set(pins)-{'BTCUSDT'}); assert len(coins)==134
raw={}; liq={}
for sym,v in pins.items():
    data=(DATA/'clean'/f'{sym}_1h.csv').read_bytes(); assert hashlib.sha256(data).hexdigest()==v['sha256']
    h=pd.read_csv(io.BytesIO(data)); h.t=pd.to_datetime(h.t,unit='ms'); assert h.t.is_unique and h.t.is_monotonic_increasing
    day=h.t.dt.floor('D'); dv=h.groupby(day).qv.sum().where(h.groupby(day).size()==24)
    dv=dv.reindex(pd.date_range(dv.index.min(),dv.index.max(),freq='D')); med=dv.rolling(30).median().shift()
    run=h.groupby(h.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount()+1
    liq[sym]=pd.Series((med.reindex(day).values>=1e7)&(run.values>=720),index=h.t)
    g=h.set_index('t').resample('1D'); d=g.agg(dict(o='first',h='max',l='min',c='last',v='sum')); d=d[g.size()==24].dropna().reset_index()
    d['e21']=d.c.ewm(span=21,adjust=False).mean(); d['e50']=d.c.ewm(span=50,adjust=False).mean()
    tr=pd.concat([d.h-d.l,(d.h-d.c.shift()).abs(),(d.l-d.c.shift()).abs()],axis=1).max(axis=1)
    d['atr']=tr.ewm(alpha=1/14,adjust=False).mean(); d['st']=1.; raw[sym]=d
panel=pd.DataFrame({s:d.set_index('t').c for s,d in raw.items()}).sort_index()
breadth=(panel>panel.ewm(span=20,adjust=False,min_periods=20).mean()).where(panel.notna()).sum(axis=1)/panel.notna().sum(axis=1)
u=(breadth>=.75)&(breadth.rolling(5).min().shift()<=.25); n=(breadth<=.25)&(breadth.rolling(5).max().shift()>=.75)
u=u&~u.shift(fill_value=False); n=n&~n.shift(fill_value=False)
timeline=pd.DatetimeIndex(raw['BTCUSDT'].t); arrays={}; frames={}; signals={}; rows=[]
for s,d in raw.items():
    pad=d.set_index('t').reindex(timeline); pad['t']=timeline; pad=pad.reset_index(drop=True)
    frames[s]=pad; arrays[s]={k:pad[k].values for k in ('o','h','l','c','atr','e50','st')}
    if s=='BTCUSDT': continue
    le=u.reindex(d.t).fillna(False).values & (d.c>d.e21).values; se=n.reindex(d.t).fillna(False).values & (d.c<d.e21).values
    eligible=liq[s].reindex(d.t+pd.Timedelta(hours=23)).fillna(False).values & (np.arange(len(d))>=60)&(d.t>=pd.Timestamp('2022-08-26 06:00')).values
    for side,g in [('long',le),('short',se)]:
        for j in np.flatnonzero(g): rows.append((s,d.t.iloc[j],side,bool(eligible[j])))
    signals[s]={key:pd.Series(x,index=d.t).reindex(timeline,fill_value=False).values for key,x in [('le',le&eligible),('se',se&eligible),('lx',np.zeros(len(d),bool)),('sx',np.zeros(len(d),bool))]}
ref=artifact(f'docs/research/breadth_fresh134_d01/max1/{KEY}_signals.csv'); ref.decision_bar_open_utc=pd.to_datetime(ref.decision_bar_open_utc)
expected=set(ref.itertuples(index=False,name=None)); own=set(rows); assert own==expected,(len(expected-own),len(own-expected))
# Native admission engine is byte-identical in 646ab3fa and reviewed 9cb4341f. No submitted research imports.
sys.path.insert(0,r'F:\ZackBot2_data\LONGSCOUT-REVIEW-20261114')
import backtest as BT,strategies as S
S.STRATEGIES[KEY]=dict(name='Independent breadth replay',style='breadth',tf='1d',sides='both',params={},mgmt=dict(stop_atr=2.5,tp_r=3,max_bars=20,tp1_r=1,tp1_frac=.5,be_r=1))
book=SimpleNamespace(t=pd.Series(timeline),syms=list(raw),d=frames,arr=arrays,raw_sig=lambda key,s,params:signals[s])
rules=json.loads(subprocess.check_output(['git','show','646ab3fa:data/exchange_rules_testnet.json'],cwd=R))
results=[]; cashchecks=[]; order0diff={}
for k in range(40):
    order=coins if k==0 else list(np.random.default_rng(20261112+k).permutation(coins)); fills=[]
    trades,curve=BT.run(book,[dict(key=KEY,share=1.,risk=8/1e7,max_pos=1,symbols=order)],start=1e7,max_lev=3333.33/1e7,daily_halt=.08,t0='2022-08-26 06:00',warmup=60,fund_per_bar=.0003,pessimistic='path',exchange_rules=rules,fill_log=fills)
    turnover={}
    for f in fills: turnover[(f['sym'],f['pos_i'])]=turnover.get((f['sym'],f['pos_i']),0)+abs(f['qty']*f['ref'])
    trades['turnover']=[turnover[(s,i)] for s,i in zip(trades.sym,trades.i_in)]; trades['pnl15']=trades.pnl-trades.turnover*.0008; trades['pnl23']=trades.pnl-trades.turnover*.0016
    trades['t_in']=timeline[trades.i_in]; early=trades.t_in<pd.Timestamp('2025-01-01'); best=trades.groupby('sym').pnl15.sum().idxmax()
    results.append(dict(n=len(trades),meanR15=trades.pnl15.mean()/8,meanR23=trades.pnl23.mean()/8,meanR15_early=trades.loc[early,'pnl15'].mean()/8,meanR15_late=trades.loc[~early,'pnl15'].mean()/8,meanR15_ex_best=trades.loc[trades.sym!=best,'pnl15'].mean()/8))
    if k==0:
        r=artifact(f'docs/research/breadth_fresh134_d01/max1/{KEY}_order0_path_trades.csv')
        assert len(r)==len(trades) and list(zip(r.sym,r.i_in,r.i_out,r.side,r.why))==list(zip(trades.sym,trades.i_in,trades.i_out,trades.side,trades.why))
        order0diff={c:float(np.max(abs(r[c].values-trades[c].values))) for c in ('pnl','R','turnover','pnl15','pnl23')}; assert max(order0diff.values())<1e-7
        # Independent sample cash reconstruction from fill refs and daily close inventory, including partial exits.
        for row in trades.iloc[:10].itertuples():
            fs=[f for f in fills if f['sym']==row.sym and f['pos_i']==row.i_in]; ent=next(f for f in fs if f['kind']=='entry'); exits=[f for f in fs if f['kind']=='exit']; qty=ent['qty']; ep=ent['ref']*(1+row.side*.0002)
            fee=qty*ep*.0005; gross=0.; funding=0.; active=qty
            for i in range(row.i_in,row.i_out+1):
                funding+=active*arrays[row.sym]['c'][i]*.0003
                for f in exits:
                    if f['i']==i:
                        px=f['ref']*(1-row.side*.0002); gross+=row.side*(px-ep)*f['qty']; fee+=f['qty']*px*.0005; active-=f['qty']
            ownp=gross-fee-funding; cashchecks.append(dict(sym=row.sym,i_in=int(row.i_in),difference=float(ownp-row.pnl)))
        trades.to_csv(O/'order0_own_trades.csv',index=False)
    if k%10==0: print('completed order',k,flush=True)
pub=json.loads(blob(f'docs/research/breadth_fresh134_d01/max1/{KEY}_result.json')); med=pd.DataFrame(results).median().to_dict(); differences={c:float(med[c]-pub['medians'][c]) for c in med}; assert max(abs(x) for x in differences.values())<1e-8
out=dict(head=HEAD,source='646ab3fa1be92614998905e6ab3497ab03a7d382',inputs_verified=135,signals_matched=len(own),eligible_signals=sum(x[3] for x in rows),missing=0,extra=0,orders=40,medians=med,median_differences=differences,order0_trades=int(pub['order0']['n']),order0_max_abs_differences=order0diff,cash_samples=cashchecks,verdict='FAIL; negative later and ex-best, insufficient primary sample; no rescue',scope='Independent raw-data breadth/signal/eligibility construction; native reviewed engine for all40 admissions. Cash sample arithmetic independently reconstructs fees/slippage/funding. No copied research alpha/runner imports. Secondary max3 not rerun.')
(O/'report.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8',newline='\n'); print(json.dumps(out,indent=2))
