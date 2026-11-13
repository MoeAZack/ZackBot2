import hashlib,json,pathlib,collections
import numpy as np,pandas as pd
D=pathlib.Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112'); R=pathlib.Path('F:/ZackBot2_data/SVS-IMP-REVIEW-20261113'); OUT=pathlib.Path(__file__).parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
mf=D/'input_manifest.json'; assert sha(mf)=='99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95'
m=json.loads(mf.read_text()); f=json.loads((R/'research/impretest1h/freeze.json').read_text()); hashes={}; events=[]; n=collections.Counter(); t0=pd.Timestamp('2024-10-11 17:00'); t1=pd.Timestamp('2026-09-27 23:00')
for s,meta in sorted(m['symbols'].items()):
 p=D/'clean'/f'{s}_1h.csv'; a=sha(p); b=meta['csv_sha256']; z=f['files']['h5_wide_d01_20261112/clean/'+s+'_1h.csv']; hashes[s]={'actual':a,'manifest':b,'freeze':z,'match':a==b==z}; assert a==b==z
 d=pd.read_csv(p); d['t']=pd.to_datetime(d.t,unit='ms'); assert not d.t.duplicated().any(); d=d.set_index('t').loc[:t1]; d=d.reindex(pd.date_range(d.index.min(),t1,freq='h')); o,h,l,c,v,q=[pd.to_numeric(d[x],errors='coerce') for x in ['o','h','l','c','v','qv']]
 valid=np.isfinite(o)&np.isfinite(h)&np.isfinite(l)&np.isfinite(c)&np.isfinite(v)&(o>0)&(h>0)&(l>0)&(c>0)&(v>=0)&(h>=l)&(h>=o)&(h>=c)&(l<=o)&(l<=c)
 tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False).mean(); prior=v.shift(1).rolling(24,min_periods=24).median(); qok=np.isfinite(q)&(q>=0); daily=q.where(qok).resample('D').sum(min_count=24); med=daily.rolling(30,min_periods=30).median().shift(1); eligible=(med.reindex(d.index.floor('D')).to_numpy()>=1e7)&(qok.rolling(720,min_periods=720).sum().to_numpy()==720)&valid.to_numpy()
 O,C,H,L,V,A,P=[x.to_numpy() for x in [o,c,h,l,v,atr,prior]]; valid=valid.to_numpy(); ts=d.index; pending=None
 for k in range(len(d)):
  if pending:
   i,side,mid,origin=pending
   if not valid[k] or not np.isfinite(A[k]) or A[k]<=0: n['invalid_pending']+=1; pending=None; continue
   cancel=C[k]<origin if side==1 else C[k]>origin
   trigger=(L[k]<=mid and C[k]>=mid and C[k]>O[k]) if side==1 else (H[k]>=mid and C[k]<=mid and C[k]<O[k])
   if cancel: n['cancelled']+=1; pending=None; continue
   if trigger:
    n['all_triggers']+=1; pending=None
    if ts[k]<t0: n['before_window']+=1; continue
    n['window_triggers']+=1; reason='' if eligible[k] else 'ineligible'
    if not reason and (k+1>=len(d) or not valid[k+1]): reason='missing_next_hour'
    n[reason or 'decisions']+=1; events.append(dict(sym=s,impulse_open=ts[i],trigger_open=ts[k],side=side,scheduled_entry=ts[k]+pd.Timedelta(hours=1) if not reason else pd.NaT,dist=3*A[k] if not reason else np.nan,refused=reason)); continue
   if k-i==12: n['expired']+=1; pending=None
   continue
  if not valid[k] or not np.isfinite(A[k]) or A[k]<=0 or not np.isfinite(P[k]): continue
  body=abs(C[k]-O[k]); rg=H[k]-L[k]
  if A[k]<=body<=3*A[k] and V[k]>=2*P[k] and rg>0:
   side=1 if C[k]>O[k] and C[k]>=L[k]+.8*rg else (-1 if C[k]<O[k] and C[k]<=H[k]-.8*rg else 0)
   if side: pending=(k,side,(O[k]+C[k])/2,O[k]); n['setups']+=1
raw=pd.read_csv(R/'docs/research/impretest1h/results/raw_signals.csv',parse_dates=['impulse_open','trigger_open','scheduled_entry']).fillna({'refused':''}); ours=pd.DataFrame(events); key=['sym','trigger_open','side']; comp=ours.merge(raw,on=key,how='outer',indicator=True,suffixes=('_own','_raw')); both=comp[comp._merge=='both']; dist=both.dist_own.notna() & both.dist_raw.notna(); bad=dist & ~np.isclose(both.dist_own,both.dist_raw,rtol=1e-11,atol=1e-13)
res={'manifest_sha256':sha(mf),'all_input_hashes_match':all(x['match'] for x in hashes.values()),'input_hashes':hashes,'state_counts':dict(n),'raw_rows':len(raw),'own_rows':len(ours),'own_duplicate_keys':int(ours.duplicated(key).sum()),'raw_duplicate_keys':int(raw.duplicated(key).sum()),'matched':len(both),'own_only':int((comp._merge=='left_only').sum()),'raw_only':int((comp._merge=='right_only').sum()),'impulse_time_mismatches':int((both.impulse_open_own!=both.impulse_open_raw).sum()),'refusal_mismatches':int((both.refused_own!=both.refused_raw).sum()),'entry_time_mismatches':int((~((both.scheduled_entry_own==both.scheduled_entry_raw)|(both.scheduled_entry_own.isna()&both.scheduled_entry_raw.isna()))).sum()),'distance_compared':int(dist.sum()),'distance_mismatches':int(bad.sum()),'max_abs_distance_error':float((both.loc[dist,'dist_own']-both.loc[dist,'dist_raw']).abs().max()),'pre_window_impulses_triggering_in_window':int((ours.impulse_open<t0).sum()),'notes':['Warmup impulses allowed; out-of-window and ineligible triggers consume setups.','Portfolio capacity is descriptive and does not change signal state; raw CSV has no explicit capacity refusals.','Next-hour check additionally requires valid OHLCV; any discrepancy is reported.']}
(OUT/'comparison.json').write_text(json.dumps(res,indent=2,default=str)); comp[comp._merge!='both'].to_csv(OUT/'unmatched.csv',index=False); print(json.dumps({k:v for k,v in res.items() if k!='input_hashes'},indent=2))
