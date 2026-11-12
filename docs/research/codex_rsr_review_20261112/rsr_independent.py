import json, hashlib, math
from pathlib import Path
import numpy as np
import pandas as pd
BASE=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112')
MANIFEST='99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95'
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def roll(a): return a.rolling(168,min_periods=1).sum().shift(1)
def benchmark(r,ok):
 v=r.notna() & np.isfinite(r) & ok; x=r.where(v,0); n=v.sum(axis=1); den=v.rsub(n,axis=0); return x.rsub(x.sum(axis=1),axis=0).div(den.where(den>=8))
def features(r,ok):
 m=benchmark(r,ok); y=r.where(np.isfinite(r)); x=m.where(y.notna()); y=y.where(x.notna()); n=roll(x.notna().astype(float)); sx=roll(x.fillna(0)); sy=roll(y.fillna(0)); xx=roll(x.fillna(0)**2); xy=roll(x.fillna(0)*y.fillna(0)); var=xx-sx*sx/n
 beta=((xy-sx*sy/n)/var).where((n>=120)&(var>0)&np.isfinite(var)); alpha=(sy-beta*sx)/n; e=(r-alpha-beta*m).where(np.isfinite(r)&np.isfinite(m)&np.isfinite(alpha)&np.isfinite(beta)); sigma=e.rolling(168,min_periods=120).std(ddof=0).shift(1); shock=e.rolling(3,min_periods=3).sum(); market=m.rolling(3,min_periods=3).sum(); z=shock/(sigma*math.sqrt(3)); z=z.where((sigma>0)&np.isfinite(sigma)&np.isfinite(z)&(market.abs()<.015)&ok)
 return m,alpha,beta,e,sigma,z
def eligibility(c,q):
 p=c.notna()&np.isfinite(c)&(c>0)&q.notna()&np.isfinite(q)&(q>=0); count=p.resample('1D').sum(); daily=q.where(p).resample('1D').sum(min_count=24).where(count==24); median=daily.rolling(30,min_periods=30).median().shift(1); return (median.reindex(c.index.normalize()).set_axis(c.index)>=1e7)&p.rolling(720,min_periods=720).sum().eq(720)
def tests():
 idx=pd.date_range('2024-01-01',periods=600,freq='h',tz='UTC'); rng=np.random.default_rng(314159); common=rng.normal(0,.002,len(idx)); r=pd.DataFrame({f'c{i}':common+rng.normal(0,.001,len(idx)) for i in range(10)},index=idx); ok=pd.DataFrame(True,index=idx,columns=r.columns); a=features(r,ok); r2=r.copy(); r2.loc[idx[400]:]*=11; b=features(r2,ok)
 for i in range(6): np.testing.assert_allclose(a[i].iloc[:400],b[i].iloc[:400],equal_nan=True,rtol=1e-9,atol=1e-12)
 own=r.copy(); own['c0']*=25; np.testing.assert_allclose(benchmark(r,ok)['c0'],benchmark(own,ok)['c0'],atol=1e-16)
 affine=r.copy(); affine['c0']=.001+2*benchmark(r,ok)['c0']; f=features(affine,ok); assert np.nanmax(np.abs(f[3]['c0'].iloc[168:]))<1e-12; assert abs(f[1]['c0'].iloc[300]-.001)<1e-12 and abs(f[2]['c0'].iloc[300]-2)<1e-10
 zero=pd.DataFrame(0.,index=idx,columns=r.columns); assert features(zero,ok)[2].isna().all().all(); assert a[2].iloc[:120].isna().all().all(); small=ok.copy(); small.iloc[:,8:]=False; assert benchmark(r,small)['c0'].isna().all()
 missing=r.copy(); missing.loc[idx[300],'c0']=np.nan; f=features(missing,ok); assert np.isnan(f[3].loc[idx[300],'c0']) and np.isnan(f[5].loc[idx[302],'c0']); later=r.copy(); later.loc[idx[400],'c0']+=.02; assert abs(a[4].loc[idx[400],'c0']-features(later,ok)[4].loc[idx[400],'c0'])<1e-15
 idx2=pd.date_range('2024-01-01',periods=800,freq='h',tz='UTC'); c=pd.DataFrame(1.,index=idx2,columns=['a']); q=c*1e6; el=eligibility(c,q); assert not el.iloc[719,0] and el.iloc[720,0]; q.iloc[100,0]=np.nan; assert not eligibility(c,q).iloc[720,0]
 return dict.fromkeys(['future_prefix','own_exclusion','affine_intercept','zero_variance','insufficient_history','missing_hour','prior_sigma','eight_others','D01_complete_days'],'pass')
def main(out):
 out=Path(out); assert sha(BASE/'input_manifest.json')==MANIFEST; mf=json.loads((BASE/'input_manifest.json').read_text()); symbols=sorted(mf['symbols']); assert len(symbols)==40; frames=[]
 for s in symbols:
  p=BASE/'clean'/f'{s}_1h.csv'; assert sha(p)==mf['symbols'][s]['csv_sha256'],s; v=pd.read_csv(p,usecols=['t','c','qv']); assert v.t.is_unique and (v.t%3600000==0).all(); v.index=pd.to_datetime(v.t,unit='ms',utc=True); frames.append(v[['c','qv']].rename(columns={'c':s,'qv':s}))
 idx=pd.date_range(min(f.index.min() for f in frames),max(f.index.max() for f in frames),freq='h',tz='UTC'); c=pd.concat([f.iloc[:,0].reindex(idx) for f in frames],axis=1); q=pd.concat([f.iloc[:,1].reindex(idx) for f in frames],axis=1); ok=eligibility(c,q); r=np.log(c.where(c>0)).diff(); z=features(r,ok)[-1]; hits=z.where((z<=-3)|(z>=3)).loc['2022-08-26':'2026-10-04 04:00'].stack().dropna().rename('z').reset_index(); hits.columns=['signal_open_utc','symbol','z']; hits['side']=np.where(hits.z<=-3,'LONG','SHORT'); hits['entry_open_utc']=hits.signal_open_utc+pd.Timedelta(hours=1); hits.to_csv(out/'entries.csv',index=False,float_format='%.17g'); print(json.dumps({'entries':len(hits),'long':int((hits.side=='LONG').sum()),'short':int((hits.side=='SHORT').sum()),'entries_sha256':sha(out/'entries.csv')}))
if __name__=='__main__':
 import sys
 if sys.argv[1]=='test': print(json.dumps(tests(),sort_keys=True))
 elif sys.argv[1]=='run': main(sys.argv[2])
