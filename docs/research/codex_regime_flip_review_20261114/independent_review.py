"""Independent signal, clock and concentration review; no submitted strategy imports."""
import hashlib,io,json,subprocess
from pathlib import Path
import numpy as np
import pandas as pd
R=Path('F:/ZackBot2_data/REGIME-FLIP-REVIEW-20261114'); G=Path('F:/ZackBot2_data/LONGSCOUT-V5-REVIEW-20261114/objects.git');F=Path('C:/Dev/ZackBot_research_cache/longscout_fresh_20261114'); E=Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112');H='10486f35dd513c51b375b3b72cfe5864763a7b39'
R.mkdir(parents=True,exist_ok=True)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def blob(p):return subprocess.check_output(['git','--git-dir='+str(G),'show',H+':'+p])
assert digest(F/'input_manifest.json')=='fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e'
assert digest(E/'input_manifest.json')=='99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95'
fm=json.loads((F/'input_manifest.json').read_bytes())['symbols'];em=json.loads((E/'input_manifest.json').read_bytes())['symbols'];fresh=json.loads((F/'selected.json').read_text())['selected'];orig={'ADAUSDT','AVAXUSDT','BNBUSDT','BTCUSDT','DOGEUSDT','ETHUSDT','LINKUSDT','SOLUSDT','XRPUSDT'};extra=sorted(set(em)-orig);coins=sorted(fresh+extra)
assert len(fresh)==134 and len(extra)==31 and len(set(coins))==165
def regime(d):
    v=d.set_index('t').c.resample('1D').agg(['last','count']);dc=v['last'][v['count']==6].dropna()
    mean=dc.rolling(100,min_periods=100).mean();slope=mean-mean.shift(20)
    state=pd.Series(0.,index=dc.index);state[(dc>mean)&(slope>0)]=1;state[(dc<mean)&(slope<0)]=-1;state[mean.isna()|slope.isna()]=np.nan
    result=pd.merge_asof(pd.DataFrame({'closed':d.t+pd.Timedelta(hours=4)}),pd.DataFrame({'closed':state.index+pd.Timedelta(days=1),'state':state.to_numpy()}),on='closed',direction='backward')
    return result.state
def flips(d):
    st=regime(d);up=st.eq(1);down=st.eq(-1)
    return st,up&~up.shift(fill_value=False),down&~down.shift(fill_value=False)
rows=[]; hashes={};prefix=[];clock=[]
for s in ['BTCUSDT']+coins:
    root=F if s in fm else E;rec=(fm if root==F else em)[s];p=root/'clean'/f'{s}_1h.csv';h=digest(p);assert h==rec.get('sha256',rec.get('csv_sha256')),s;hashes[s]=h
    h1=pd.read_csv(p);h1.t=pd.to_datetime(h1.t,unit='ms') if np.issubdtype(h1.t.dtype,np.number) else pd.to_datetime(h1.t)
    assert h1.t.is_monotonic_increasing and not h1.t.duplicated().any()
    day=h1.t.dt.floor('D');q=h1.groupby(day).qv.agg(['sum','count']);q=q['sum'].where(q['count']==24).reindex(pd.date_range(day.min(),day.max(),freq='D'));prior=q.rolling(30,min_periods=30).median().shift(1);streak=h1.groupby(h1.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount().to_numpy()+1
    liquid=(prior.reindex(day).to_numpy()>=1e7)&(streak>=720)
    g=h1.set_index('t').resample('4h');d=g.agg({'o':'first','h':'max','l':'min','c':'last'});d=d[g.size()==4].dropna().reset_index()
    assert d.t.diff().iloc[1:].eq(pd.Timedelta(hours=4)).all(),s
    if s=='BTCUSDT':continue
    eligible=pd.Series(liquid,index=pd.DatetimeIndex(h1.t)).reindex(pd.DatetimeIndex(d.t)+pd.Timedelta(hours=3)).fillna(False).to_numpy(dtype=bool)&(np.arange(len(d))>=220)&(d.t.to_numpy()>=np.datetime64('2022-08-26T06:00'))
    state,le,se=flips(d)
    for side,signal in [('long',le),('short',se)]:
        for i in np.flatnonzero(signal.to_numpy()):rows.append((s,str(d.t.iloc[i]),side,bool(eligible[i])))
    if s in set(fresh[:6]+extra[:6]):
        for frac in (0.45,0.65,0.85):
            end=int(len(d)*frac);truncated=d.iloc[:end].copy().reset_index(drop=True);_,a,b=flips(truncated)
            prefix.append({'symbol':s,'bars':end,'cutoff_bar_open':str(truncated.t.iloc[-1]),'long_diffs':int((a.to_numpy()!=le.to_numpy()[:end]).sum()),'short_diffs':int((b.to_numpy()!=se.to_numpy()[:end]).sum())})
    changes=state.notna() & state.ne(state.shift())
    clock.extend([int(x) for x in d.loc[changes,'t'].dt.hour])
print('files',len(hashes),'prefix checks',len(prefix),flush=True)
own=pd.DataFrame(rows,columns=['sym','decision_bar_open_utc','side','eligible']);own.to_csv(R/'independent_signals.csv',index=False)
base='docs/research/longscout_v6/r6_regime_flip_4h/r6_regime_flip_4h';raw=blob(base+'_signals.csv');ref=pd.read_csv(io.BytesIO(raw))
def key(d):return set((r.sym,str(pd.Timestamp(r.decision_bar_open_utc)),r.side,bool(r.eligible)) for r in d.itertuples())
a,b=key(own),key(ref); comparison={'raw_own':len(own),'raw_published':len(ref),'unique_own':len(a),'unique_published':len(b),'matched':len(a&b),'missing':len(b-a),'extra':len(a-b),'eligible_own':int(own.eligible.sum()),'eligible_published':int(ref.eligible.sum()),'missing_examples':sorted(b-a)[:10],'extra_examples':sorted(a-b)[:10],'published_sha256':hashlib.sha256(raw).hexdigest()}
tr=pd.read_csv(io.BytesIO(blob(base+'_order0_path_trades.csv'))); sigkeys={(r.sym,pd.Timestamp(r.decision_bar_open_utc),r.side) for r in own[own.eligible].itertuples()};bad=[]
for t in tr.itertuples():
    k=(t.sym,pd.Timestamp(t.t_in)-pd.Timedelta(hours=4),'long' if t.side==1 else 'short')
    if k not in sigkeys:bad.append(str(k))
alltr=pd.read_csv(io.BytesIO(blob(base+'_all_orders_trades.csv.gz')),compression='gzip');alltr=alltr[alltr.scen=='path'];conc=[]
for i in range(40):
    t=alltr[alltr.order==i];ranking=t.groupby('sym').pnl15.sum().sort_values(ascending=False);d={'order':i,'n':len(t),'meanR15':float(t.pnl15.mean()/8),'best':ranking.head(10).index.tolist()}
    for n in (1,3,5,10):d[f'ex_top{n}_meanR15']=float(t[~t.sym.isin(ranking.head(n).index)].pnl15.mean()/8)
    conc.append(d)
c=pd.DataFrame(conc);published=json.loads(blob(base+'_result.json'))
out={'head':H,'coins':len(coins),'input_hashes':hashes,'comparison':comparison,'prefix_checks':prefix,'prefix_total':len(prefix),'prefix_long_diffs':sum(x['long_diffs'] for x in prefix),'prefix_short_diffs':sum(x['short_diffs'] for x in prefix),'change_hour_counts':{str(x):clock.count(x) for x in sorted(set(clock))},'order0_entries':len(tr),'order0_missing_preceding_eligible_signal':len(bad),'entry_mismatches':bad[:10],'concentration_medians':{k:float(c[k].median()) for k in ['meanR15','ex_top1_meanR15','ex_top3_meanR15','ex_top5_meanR15','ex_top10_meanR15']},'concentration_min':float(c.meanR15.min()),'order0_best':conc[0]['best'],'order0_median_trade_R15':float(tr.pnl15.median()/8),'order0_max_trade_R15':float(tr.pnl15.max()/8),'published_status':published['wide165']['status'],'published_gates':published['gates']}
(R/'independent_review.json').write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({k:v for k,v in out.items() if k not in ['input_hashes','prefix_checks']},indent=2))
