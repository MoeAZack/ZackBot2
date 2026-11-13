import hashlib, json, pathlib, zipfile, io
import numpy as np
import pandas as pd

base=pathlib.Path('F:/ZackBot2_data/SVS-IMP-REVIEW-20261113')
spot=pathlib.Path('F:/ZackBot2_data/SPOTFLOW-D01-20261112')
futures=pathlib.Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean')
out=base/'independent_svs'; out.mkdir(exist_ok=True)
freeze=base/'research/svs1h/freeze.json'
F=json.loads(freeze.read_text()); M=json.loads((spot/'data_manifest_frozen.json').read_text())
sha=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
inputs={}; mismatches=[]
for rel, expected in F['files'].items():
    if rel.startswith('SPOTFLOW-D01-20261112/') and rel.endswith('.zip'): p=spot/rel.split('/',1)[1]
    elif rel.startswith('h5_wide_d01_20261112/clean/') and rel.endswith('_1h.csv'): p=futures/pathlib.Path(rel).name
    else: continue
    actual=sha(p); inputs[rel]=actual
    if actual != expected: mismatches.append(rel)
if mismatches: raise RuntimeError('input hash mismatch: '+str(mismatches))
start=pd.Timestamp('2024-10-11 17:00'); end=pd.Timestamp('2026-09-27 23:00')

def eligible(df):
    good=np.isfinite(df.qv)&(df.qv>=0)
    for c in ['o','h','l','c']: good &= np.isfinite(df[c]) & (df[c]>0)
    # Complete-day quote volume requires 24 valid hours, excluding current UTC day.
    daily=df.qv.where(good).resample('D').sum(min_count=24)
    med=daily.shift(1).rolling(30,min_periods=30).median()
    dailyok=med.reindex(df.index.normalize()).set_axis(df.index)>=1e7
    return good & (good.rolling(720,min_periods=720).sum()==720) & dailyok

rows=[]; endpoints={}
for symbol in sorted(M['symbols']):
    f=pd.read_csv(futures/f'{symbol}_1h.csv'); f.t=pd.to_datetime(f.t,unit='ms'); f=f.set_index('t').asfreq('h').loc[:end]
    pieces=[]
    for month in M['months']:
        with zipfile.ZipFile(spot/f'{symbol}-1h-{month}.zip') as z:
            with z.open(z.namelist()[0]) as h:
                d=pd.read_csv(h,header=None,usecols=[0,1,2,3,4,6,7],names=['t','o','h','l','c','end','qv'])
        unit='us' if d.t.iloc[0]>10**14 else 'ms'
        delta=3_599_999_999 if unit=='us' else 3_599_999
        endpoints[f'{symbol}/{month}']={'unit':unit,'bad_close_time':int((d['end']-d.t != delta).sum())}
        d.t=pd.to_datetime(d.t,unit=unit); pieces.append(d.drop(columns='end'))
    s=pd.concat(pieces).set_index('t').asfreq('h').loc[:end]
    idx=f.index.union(s.index); f=f.reindex(idx); s=s.reindex(idx)
    ef=eligible(f); es=eligible(s)
    fq=f.qv.where(np.isfinite(f.qv)&(f.qv>=0)); sq=s.qv.where(np.isfinite(s.qv)&(s.qv>=0)); Q=fq+sq
    share=(sq/Q).where(Q>0); avg=share.shift(1).rolling(168,min_periods=168).mean(); std=share.shift(1).rolling(168,min_periods=168).std(ddof=1)
    z=(share-avg)/std.where(std>0); qmed=Q.shift(1).rolling(168,min_periods=168).median()
    prev=f.c.shift(1); tr=pd.concat([f.h-f.l,(f.h-prev).abs(),(f.l-prev).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/14,adjust=False).mean()
    body=f.c-f.o; valid=ef&es&(Q>0)&(body.abs()>=atr)&(atr>0)&(body!=0)&(Q>=2*qmed)&(z.abs()>=2)
    for t in idx[valid & (idx>=start) & (idx<=end)]:
        side=int(np.sign(body[t]) * (1 if z[t]>=2 else -1))
        lag=(t+pd.Timedelta(hours=1) in f.index and t+pd.Timedelta(hours=2) in f.index and pd.notna(f.loc[t+pd.Timedelta(hours=1),'o']) and pd.notna(f.loc[t+pd.Timedelta(hours=2),'o']))
        rows.append({'sym':symbol,'signal_open':t,'side':side,'z':float(z[t]),'share':float(share[t]),'Q':float(Q[t]),'dist':float(3*atr[t]),'scheduled_entry':t+pd.Timedelta(hours=2) if lag else pd.NaT})
    print(symbol,len(rows),flush=True)
ours=pd.DataFrame(rows); ref=pd.read_csv(base/'docs/research/svs1h/results/raw_signals.csv',parse_dates=['signal_open','scheduled_entry','decision_available'])
keys=['sym','signal_open','side']; joined=ours.merge(ref,on=keys,how='outer',indicator=True,suffixes=('_independent','_reference'))
both=joined[joined._merge=='both'].copy()
numeric={c:{'max_abs':float((both[c+'_independent']-both[c+'_reference']).abs().max()),'different_gt_1e-9':int(((both[c+'_independent']-both[c+'_reference']).abs()>1e-9).sum())} for c in ['z','share','Q','dist']}
result={'frozen_source_tree':'c5b92a74b46537029751d171d1214436f84680f8','freeze_sha256':sha(freeze),'freeze_expected':(freeze.parent/'freeze.sha256').read_text().strip(),'ruling_sha256':sha(pathlib.Path('F:/ZackBot2_data/C1H-20261112/review-branch/docs/research/next_trials_20261113/FROZEN_RULING.md')),'reference_csv_sha256':sha(base/'docs/research/svs1h/results/raw_signals.csv'),'input_hashes':inputs,'input_hash_mismatches':mismatches,'endpoint_summary':{'files':len(endpoints),'bad_close_time':sum(x['bad_close_time'] for x in endpoints.values()),'units':pd.Series([x['unit'] for x in endpoints.values()]).value_counts().to_dict()},'independent_count':len(ours),'reference_count':len(ref),'matched':len(both),'missing_from_independent':int((joined._merge=='right_only').sum()),'extra_independent':int((joined._merge=='left_only').sum()),'numeric':numeric,'schedule_mismatches':int((both.scheduled_entry_independent!=both.scheduled_entry_reference).sum()),'decision_timestamp_mismatches':int((both.decision_available!=both.signal_open+pd.Timedelta(hours=1)).sum()),'nonmatches':joined[joined._merge!='both'][keys+['_merge']].astype(str).to_dict('records'),'boundary':{'first_independent':str(ours.signal_open.min()),'last_independent':str(ours.signal_open.max()),'first_reference':str(ref.signal_open.min()),'last_reference':str(ref.signal_open.max()),'reference_refused':int(ref.scheduled_entry.isna().sum())},'manifest_order':M['symbols'],'freeze_order':F['coins'],'a1_assertion':'The crash asserts unsorted manifest symbols equal sorted freeze coins; current runner sorts manifest. Crash itself proves assertion provenance only, not proof of the separately claimed source tree.'}
(out/'comparison.json').write_text(json.dumps(result,indent=2,default=str)); ours.to_csv(out/'independent_signals.csv',index=False)
print(json.dumps({k:v for k,v in result.items() if k not in ('input_hashes','nonmatches','manifest_order','freeze_order')},indent=2))
