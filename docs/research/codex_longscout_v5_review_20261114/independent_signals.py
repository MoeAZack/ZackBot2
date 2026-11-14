"""Independent V5 entry and D01 check: pandas only, no submitted runner/strategy imports."""
import hashlib, io, json, subprocess
from pathlib import Path
import numpy as np
import pandas as pd
R=Path('F:/ZackBot2_data/LONGSCOUT-V5-REVIEW-20261114'); F=Path('C:/Dev/ZackBot_research_cache/longscout_fresh_20261114'); E=Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112')
HEAD='f9a914babc53dd2c2ad1eac438a33c26fcdc0b88'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def blob(path): return subprocess.check_output(['git','--git-dir='+str(R/'objects.git'),'show',HEAD+':'+path])
assert sha(F/'input_manifest.json')=='fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e'
assert sha(E/'input_manifest.json')=='99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95'
fm=json.loads((F/'input_manifest.json').read_bytes())['symbols']; em=json.loads((E/'input_manifest.json').read_bytes())['symbols']
fresh=json.loads((F/'selected.json').read_text())['selected']; orig={'ADAUSDT','AVAXUSDT','BNBUSDT','BTCUSDT','DOGEUSDT','ETHUSDT','LINKUSDT','SOLUSDT','XRPUSDT'}
extra=sorted(set(em)-orig); coins=sorted(fresh+extra)
assert len(fresh)==134 and len(extra)==31 and len(set(coins))==165
frames={}; eligibility={}; filehash={}
for s in ['BTCUSDT']+coins:
    root=F if s in fm else E; rec=(fm if root==F else em)[s]; path=root/'clean'/f'{s}_1h.csv'
    h=sha(path); assert h==rec.get('sha256',rec.get('csv_sha256')),s
    filehash[s]=h
    h1=pd.read_csv(path); h1.t=pd.to_datetime(h1.t,unit='ms') if np.issubdtype(h1.t.dtype,np.number) else pd.to_datetime(h1.t)
    assert h1.t.is_monotonic_increasing and not h1.t.duplicated().any()
    day=h1.t.dt.floor('D'); q=h1.groupby(day).qv.agg(['sum','count']); q=q['sum'].where(q['count']==24).reindex(pd.date_range(day.min(),day.max(),freq='D'))
    med=q.rolling(30,min_periods=30).median().shift(1)
    streak=h1.groupby(h1.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount().to_numpy()+1
    liquid=(med.reindex(day).to_numpy()>=1e7)&(streak>=720)
    groups=h1.set_index('t').resample('4h'); f=groups.agg({'o':'first','h':'max','l':'min','c':'last','v':'sum'})
    f=f[groups.size()==4].dropna(subset=['o','h','l','c']).reset_index()
    eligibility[s]=(pd.Series(liquid,index=pd.DatetimeIndex(h1.t)).reindex(pd.DatetimeIndex(f.t)+pd.Timedelta(hours=3)).fillna(False).to_numpy(dtype=bool)&(np.arange(len(f))>=220)&(f.t.to_numpy()>=np.datetime64('2022-08-26T06:00')))
    frames[s]=f
print('validated files:',len(filehash),flush=True)
btc=frames['BTCUSDT']; dt=btc.set_index('t').c.resample('1D').agg(['last','count']); dc=dt['last'][dt['count']==6].dropna()
sma=dc.rolling(100,min_periods=100).mean(); slope=sma-sma.shift(20); regime=pd.Series(0.0,index=dc.index); regime[(dc>sma)&(slope>0)]=1; regime[(dc<sma)&(slope<0)]=-1; regime[sma.isna()|slope.isna()]=np.nan
bc=btc.set_index('t').c
results={}
for case in ['s5_high_beta_btc_down_4h','r5_ichimoku_cloud_4h']:
    rows=[]
    for s in coins:
        d=frames[s]; c=d.c
        if case.startswith('s5_'):
            b=bc.reindex(pd.DatetimeIndex(d.t)); br=pd.Series(b.to_numpy()).pct_change(); cr=c.pct_change()
            beta=cr.rolling(180,min_periods=180).cov(br)/br.rolling(180,min_periods=180).var()
            e20=c.ewm(span=20,adjust=False).mean(); e50=c.ewm(span=50,adjust=False).mean()
            mapped=pd.merge_asof(pd.DataFrame({'at':d.t+pd.Timedelta(hours=4)}),pd.DataFrame({'at':regime.index+pd.Timedelta(days=1),'r':regime.to_numpy()}),on='at',direction='backward').r
            ss=(e50>e20)&(e50.shift()<=e20.shift())&(beta>=1.2)&(mapped==-1)
            signals=[('short',ss)]
        else:
            mid=lambda n:(d.h.rolling(n).max()+d.l.rolling(n).min())/2
            ten=mid(9); kij=mid(26); a=((ten+kij)/2).shift(26); b=mid(52).shift(26); top=np.maximum(a,b); bot=np.minimum(a,b)
            long=(c>top)&(c.shift()<=top.shift())&(ten>kij)
            short=(c<bot)&(c.shift()>=bot.shift())&(ten<kij)
            signals=[('long',long&~short),('short',short&~long)]
        for side,arr in signals:
            for i in np.flatnonzero(arr.fillna(False).to_numpy()): rows.append((s,str(d.t.iloc[i]),side,bool(eligibility[s][i])))
    own=pd.DataFrame(rows,columns=['sym','decision_bar_open_utc','side','eligible']); own.to_csv(R/(case+'_independent_signals.csv'),index=False)
    rel='docs/research/longscout_v5/'+case+'/'+case+'_signals.csv'; data=blob(rel); ref=pd.read_csv(io.BytesIO(data))
    def keyed(d): return set((r.sym,str(pd.Timestamp(r.decision_bar_open_utc)),r.side,bool(r.eligible)) for r in d.itertuples())
    a,b=keyed(own),keyed(ref); results[case]={'own_count':len(own),'published_count':len(ref),'own_unique':len(a),'published_unique':len(b),'matched':len(a&b),'missing':len(b-a),'extra':len(a-b),'own_eligible':int(own.eligible.sum()),'published_eligible':int(ref.eligible.sum()),'missing_examples':sorted(b-a)[:10],'extra_examples':sorted(a-b)[:10],'published_signal_sha256':hashlib.sha256(data).hexdigest()}
    print(case,results[case],flush=True)
(R/'independent_signal_results.json').write_text(json.dumps({'head':HEAD,'current_pr_head':'eb5f615c78660490e76cad97b8f09c2fffbe1e89','input_hashes':filehash,'results':results},indent=2)+'\n')
