from pathlib import Path
import pandas as pd, numpy as np, hashlib, json, subprocess, io
R=Path('F:/ZackBot2_data/S16-REVIEW-20261114'); H='d9bf7bf75bd274e47e72dc4fc33bf10d32802895'
F=Path('C:/Dev/ZackBot_research_cache/longscout_fresh_20261114'); E=Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112')
fm=json.loads((F/'input_manifest.json').read_bytes())['symbols']; em=json.loads((E/'input_manifest.json').read_bytes())['symbols']; fresh=json.loads((F/'selected.json').read_text())['selected']; orig={'ADAUSDT','AVAXUSDT','BNBUSDT','BTCUSDT','DOGEUSDT','ETHUSDT','LINKUSDT','SOLUSDT','XRPUSDT'}; coins=sorted(fresh+sorted(set(em)-orig)); assert len(coins)==165
base='docs/research/longscout_v16/s16_inside_day_breakdown_4h/s16_inside_day_breakdown_4h'
def blob(path): return subprocess.check_output(['git','-C',str(R),'show',H+':'+path])
def build(d):
    day=d.t.dt.floor('D'); groups=d.groupby(day); dc=groups.c.agg(['last','count']); dc=dc['last'][dc['count']==6]; mean=dc.rolling(100).mean(); slope=mean-mean.shift(20); reg=np.where((dc<mean)&(slope<0),-1,0); reg=pd.Series(reg,index=dc.index+pd.Timedelta(days=1)); cr=reg.reindex(pd.DatetimeIndex(d.t)+pd.Timedelta(hours=4),method='ffill').to_numpy()
    tab=groups.agg(high=('h','max'),low=('l','min'),count=('c','size')); tab=tab[tab['count']==6]; valid=tab.index.to_series().diff().eq(pd.Timedelta(days=1)).to_numpy(); inside=(tab.high.to_numpy()<tab.high.shift().to_numpy())&(tab.low.to_numpy()>tab.low.shift().to_numpy())&valid
    src=pd.DataFrame({'inside':inside,'lo':tab.low.to_numpy()},index=tab.index+pd.Timedelta(days=1)); mapped=src.reindex(pd.DatetimeIndex(d.t)+pd.Timedelta(hours=4),method='ffill'); low=pd.Series(mapped.lo.to_numpy()); ins=mapped.inside.fillna(False).to_numpy(); c=d.c.reset_index(drop=True); cross=(c<low)&(c.shift()>=low.shift()); return (cross.to_numpy()&ins&(cr==-1))
rows=[]; gaps=[]; prefix=[]; files=0
for sym in coins:
    root=F if sym in fm else E; rec=(fm if root==F else em)[sym]; raw=(root/'clean'/f'{sym}_1h.csv').read_bytes(); assert hashlib.sha256(raw).hexdigest()==rec.get('sha256',rec.get('csv_sha256')); files+=1
    h=pd.read_csv(io.BytesIO(raw)); h.t=pd.to_datetime(h.t,unit='ms'); assert h.t.is_unique and h.t.is_monotonic_increasing
    day=h.t.dt.floor('D'); q=h.groupby(day).qv.agg(['sum','count']); q=q['sum'].where(q['count']==24).reindex(pd.date_range(day.min(),day.max(),freq='D')); med=q.rolling(30,min_periods=30).median().shift(); run=h.groupby(h.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount().to_numpy()+1; liquid=(med.reindex(day).to_numpy()>=1e7)&(run>=720)
    g=h.set_index('t').resample('4h'); d=g.agg({'o':'first','h':'max','l':'min','c':'last'}); d=d[g.size()==4].dropna().reset_index(); ng=int((d.t.diff().iloc[1:]!=pd.Timedelta(hours=4)).sum());
    if ng:gaps.append((sym,ng))
    elig=pd.Series(liquid,index=pd.DatetimeIndex(h.t)).reindex(pd.DatetimeIndex(d.t)+pd.Timedelta(hours=3)).fillna(False).to_numpy(dtype=bool)&(np.arange(len(d))>=220)&(d.t.to_numpy()>=np.datetime64('2022-08-26T06:00'))
    sig=build(d)
    for i in np.flatnonzero(sig):rows.append((sym,str(d.t.iloc[i]),'short',bool(elig[i])))
    if sym in set(fresh[:6]+coins[-6:]):
        for frac in (.45,.65,.85):
            n=int(len(d)*frac); sub=d.iloc[:n].copy().reset_index(drop=True); prefix.append(int(np.sum(build(sub)!=sig[:n])))
ref=pd.read_csv(io.BytesIO(blob(base+'_signals.csv'))); expected={(x.sym,str(pd.Timestamp(x.decision_bar_open_utc)),x.side,bool(x.eligible)) for x in ref.itertuples()}; actual=set(rows)
out={'head':H,'files_hash_verified':files,'gapped_4h_symbols':gaps,'raw_own':len(rows),'raw_published':len(ref),'matched':len(expected&actual),'missing':len(expected-actual),'extra':len(actual-expected),'eligible':sum(x[3] for x in rows),'missing_examples':sorted(expected-actual)[:5],'extra_examples':sorted(actual-expected)[:5],'prefix_tests':len(prefix),'prefix_differences':sum(prefix)}
(R/'independent_s16_signals.json').write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
