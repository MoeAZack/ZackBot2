import json,hashlib,math
from pathlib import Path
import numpy as np,pandas as pd
P=Path('F:/ZackBot2_data/IVS-REVIEW-20261113');ip=P/'docs/research/ivs1h/inputs';rp=P/'docs/research/ivs1h/results';hour=pd.read_csv(ip/'btcbvol_hourly.csv',dtype={'value':str});t=pd.DatetimeIndex(pd.to_datetime(hour.hour_open_utc));T0=pd.Timestamp('2024-10-11 17:00');T1=pd.Timestamp('2026-09-27 23:00');expected=pd.date_range('2024-10-04','2026-09-27 23:00',freq='h');assert t.equals(expected)
assert np.array_equal(hour.hour_open_us.to_numpy(),t.as_unit('ns').astype('int64')//1000)
manifest=json.loads((ip/'expected_manifest.json').read_text());audit=json.loads((ip/'audit.json').read_text());pub=json.loads((rp/'results.json').read_text())
val=pd.to_numeric(hour.value,errors='coerce').to_numpy();counts=hour.unique_valid_count.to_numpy();last=pd.to_numeric(hour.last_calc_time_us,errors='coerce').to_numpy();start=hour.hour_open_us.to_numpy();end=start+3600*1000000
computed=(np.isfinite(val)&(val>0)&(counts>=3000)&np.isfinite(last)&(last>=end-60*1000000)&(last<end));assert np.array_equal(computed,hour.valid.astype(bool).to_numpy())
assert (hour.unique_valid_count<=hour.rows).all();assert int(hour.rows.sum())==60600092
V=pd.Series(np.where(computed,val,np.nan),index=t);x=np.log(V/V.shift(1));prior=x.shift(1).rolling(168,min_periods=168);sd=prior.std(ddof=1);z=(x-prior.mean())/sd.where(sd>0);event=(z>=3)&(t>=T0)&(t<=T1)
cal_weeks=(T1-T0)/pd.Timedelta(days=7);tuples=[];distance={};percoin={};root=Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean')
syms=['ADAUSDT','AVAXUSDT','BNBUSDT','BTCUSDT','DOGEUSDT','ETHUSDT','LINKUSDT','SOLUSDT','XRPUSDT'];refused=[]
for s in syms:
 d=pd.read_csv(root/f'{s}_1h.csv');d.t=pd.to_datetime(d.t,unit='ms');assert d.t.is_unique and d.t.is_monotonic_increasing;d=d.set_index('t').asfreq('h');c=d.c;tr=pd.concat([d.h-d.l,(d.h-c.shift()).abs(),(d.l-c.shift()).abs()],axis=1).max(axis=1);atr=tr.ewm(alpha=1/14,adjust=False).mean()
 valid=(np.isfinite(d[['o','h','l','c','qv']]).all(axis=1)&(d[['o','h','l','c']]>0).all(axis=1)&(d.qv>=0)&(d.h>=d[['o','c']].max(axis=1))&(d.l<=d[['o','c']].min(axis=1)))
 q=d.qv.where(valid);daily=q.groupby(q.index.floor('D')).sum(min_count=24).asfreq('D');med=daily.rolling(30,min_periods=30).median().shift(1);eligible=(med.reindex(q.index.floor('D')).to_numpy()>=1e7)&(q.notna().rolling(720,min_periods=720).sum().to_numpy()==720)
 ev=event.reindex(d.index,fill_value=False);lo=ev&(c-d.o<=-atr)&(atr>0)&valid;sh=ev&(c-d.o>=atr)&(atr>0)&valid
 longs=shorts=0
 for pos in np.flatnonzero((lo|sh).to_numpy()):
  sig=d.index[pos];side=1 if lo.iloc[pos] else -1;fill=sig+pd.Timedelta(hours=1)
  if not eligible[pos]:refused.append((s,str(sig),'ineligible'));continue
  if fill not in d.index or fill>T1:refused.append((s,str(sig),'missing_next_hour'));continue
  key=(s,str(sig),side);tuples.append((*key,str(fill)));distance[key]=3*float(atr.iloc[pos]);longs+=side==1;shorts+=side==-1
 percoin[s]={'long':longs,'short':shorts}
raw=pd.read_csv(rp/'raw_signals.csv');a=set(tuples);b={(r.sym,str(pd.Timestamp(r.signal_open)),int(r.side),str(pd.Timestamp(r.scheduled_fill))) for r in raw.itertuples()};assert not (a-b) and not(b-a)
filltimes=sorted({q[3] for q in tuples});m=(t>=T0)&(t<=T1);maxdist=max(abs(distance[(r.sym,str(pd.Timestamp(r.signal_open)),int(r.side))]-r.dist) for r in raw.itertuples())
result={'head':'544a33d29d83ea988631e0d918e1697d4d63e932','hourly_sha256':hashlib.sha256((ip/'btcbvol_hourly.csv').read_bytes()).hexdigest(),'hour_table_rows':len(hour),'hour_table_criteria_match':True,'valid_hours':int(computed.sum()),'low_count_hours_in_available_files':int(((hour.unique_valid_count<3000)&hour.source_file.notna()).sum()),'stale_or_absent_last_in_available_files':int(((~((last>=end-60*1000000)&(last<end)))&hour.source_file.notna()).sum()),'window_valid_V':int(V[m].notna().sum()),'window_valid_z':int(z[m].notna().sum()),'shock_hours':int(event.sum()),'raw_tuples':len(tuples),'matched':len(a&b),'missing':len(b-a),'extra':len(a-b),'refused':refused,'distinct_fill_times':len(filltimes),'calendar_weeks':cal_weeks,'upper_bound_per_week':len(filltimes)/cal_weeks,'FREQUENCY_FAIL':len(filltimes)/cal_weeks<3,'per_coin':percoin,'max_stop_distance_error':maxdist,'scope':'all provided hourly rows validated structurally and quality recomputed; raw ZIP reaggregation separate independent lane pending; independent full StageB signals/no economics'}
(P/'independent_stage_b.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='per_coin'},indent=2))
