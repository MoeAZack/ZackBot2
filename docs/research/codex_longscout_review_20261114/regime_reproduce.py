import pandas as pd, numpy as np, json, hashlib
from pathlib import Path
root=Path(r'F:\ZackBot2_data\LONGSCOUT-REVIEW-20261114\docs\research\longscout_v1'); data=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112\clean'); out=Path(__file__).parent
inputs=json.loads((root/'exp_trend_regime_x31_inputs.json').read_text()); tr=pd.read_csv(root/'exp_trend_regime_x31_regime_trades.csv.gz',parse_dates=['t_in']); source=pd.read_csv(root/'exp_trend_regime_x31_all_orders_trades.csv.gz')
hashes={s:hashlib.sha256((data/f'{s}_1h.csv').read_bytes()).hexdigest()==h for s,h in inputs['csv_sha256'].items()}; regimes={}
for sym in sorted(set(tr.sym)|{'BTCUSDT'}):
 d=pd.read_csv(data/f'{sym}_1h.csv',usecols=['t','c']); d['time']=pd.to_datetime(d.t,unit='ms'); d=d.set_index('time'); assert d.index.is_unique and d.index.is_monotonic_increasing
 four=d.c.resample('4h').agg(['last','count']); four=four[four['count']==4]; day=four['last'].resample('1D').agg(['last','count']); day=day[day['count']==6]
 sma=day['last'].rolling(100,min_periods=100).mean(); slope=sma-sma.shift(20); reg=np.where((day['last']>sma)&(slope>0),'rising',np.where((day['last']<sma)&(slope<0),'falling','sideways')); reg=np.where(slope.isna(),'unknown',reg); regimes[sym]=pd.Series(reg,index=day.index+pd.Timedelta(days=1))
def lookup(sym,t):
 s=regimes[sym]; i=s.index.searchsorted(t,side='right')-1; return s.iloc[i] if i>=0 else 'unknown'
tr['ind_coin']=[lookup(s,t) for s,t in zip(tr.sym,tr.t_in)]; tr['ind_btc']=[lookup('BTCUSDT',t) for t in tr.t_in]
mis={k:tr.loc[tr['ind_'+k]!=tr[k+'_regime'],['sym','t_in','order',k+'_regime','ind_'+k]].astype(str).to_dict('records') for k in ['coin','btc']}
short=tr[tr.side==-1].groupby(['order','ind_coin']).agg(n=('R15','size'),sumR=('R15','sum')).reset_index(); agg={k:{'median_n':float(g.n.median()),'median_sumR15':float(g.sumR.median()),'orders':int(g.order.nunique())} for k,g in short.groupby('ind_coin')}; order0=tr[tr.order==0]
report={'sha256_files_checked':len(hashes),'sha256_file_mismatches':[s for s,v in hashes.items() if not v], 'path_trade_rows':len(tr),'orders':int(tr.order.nunique()),'source_rows':len(source),'label_disagreement_counts':{k:len(v) for k,v in mis.items()},'disagreement_examples':{k:v[:30] for k,v in mis.items()}, 'order0_closes':len(order0),'order0_net_pnl15':float(order0.pnl15.sum()),'short_coin_regime_medians':agg,'method':'4 complete unique 1h bars per 4h bin; 6 complete bins per UTC day; daily close available at next UTC midnight; SMA100 minus its value 20 completed days earlier; classify at t_in.'}
(out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))
pub=json.loads((root/'exp_trend_regime_x31_regime.json').read_text()); checks=[]
for kind in ['coin','btc']:
 for regime in ['falling','rising','sideways','unknown']:
  for side,name in [(-1,'short'),(1,'long')]:
   sub=tr[(tr['ind_'+kind]==regime)&(tr.side==side)]; by=sub.groupby('order').R15.agg(['count','mean','sum']); z=sub[sub.order==0]; ref=pub['by_'+kind+'_regime'][regime+'|'+name]
   got={'order0_n':len(z),'order0_meanR15':z.R15.mean(),'order0_sumR15':z.R15.sum(),'median_n':by['count'].median(),'median_meanR15':by['mean'].median(),'median_sumR15':by['sum'].median(),'orders_with_trades':len(by)}
   for field,v in got.items():
    if not np.isclose(v,ref[field],atol=1e-10,rtol=1e-10): checks.append([kind,regime,name,field,float(v),ref[field]])
path=source[source.scen=='path'].reset_index(drop=True); cols=list(path.columns)
report['published_aggregate_values_checked']=2*4*2*7; report['aggregate_disagreements']=checks; report['source_path_rows_match_tolerance'] = bool(all(np.allclose(path[c],tr[c],rtol=1e-12,atol=1e-12) if c in ['pnl7','pnl15','pnl23','R'] else (path[c].astype(str).values==tr[c].astype(str).values).all() for c in cols)); report['R15_max_abs_error_vs_pnl15_over_8']=float((tr.R15-tr.pnl15/8).abs().max())
(out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps({k:report[k] for k in ['published_aggregate_values_checked','aggregate_disagreements','source_path_rows_match_tolerance','R15_max_abs_error_vs_pnl15_over_8']},indent=2))
# Independent reconstruction of EMA cross, momentum and point-in-time liquidity eligibility.
published=pd.read_csv(root/'exp_trend_regime_x31_signals.csv',parse_dates=['decision_bar_open_utc']); rows=[]
for sym in sorted(set(inputs['csv_sha256'])-{'BTCUSDT'}):
 d=pd.read_csv(data/f'{sym}_1h.csv'); d['time']=pd.to_datetime(d.t,unit='ms'); d=d.set_index('time'); assert d.index.is_unique and d.index.is_monotonic_increasing
 g=d.resample('4h'); bars=g.agg({'c':'last'}); bars=bars[g.size()==4]; c=bars.c
 e20=c.ewm(span=20,adjust=False).mean(); e50=c.ewm(span=50,adjust=False).mean(); e200=c.ewm(span=200,adjust=False).mean()
 long=(e20>e50)&(e20.shift()<=e50.shift())&(c>e200)&(c>c.shift(42))&(c>c.shift(180))
 short=(e50>e20)&(e50.shift()<=e20.shift())&(c<e200)&(c<c.shift(42))&(c<c.shift(180))
 day=d.index.floor('D'); daily=d.groupby(day).qv.agg(['sum','size']); daily=daily.reindex(pd.date_range(daily.index.min(),daily.index.max(),freq='D')); median=daily['sum'].where(daily['size']==24).rolling(30,min_periods=30).median().shift(1)
 consecutive=pd.Series(d.index,index=d.index).diff().ne(pd.Timedelta(hours=1)).cumsum(); run=consecutive.groupby(consecutive).cumcount()+1
 for side,mask in [('long',long),('short',short)]:
  for i in np.flatnonzero(mask):
   t=bars.index[i]; h=t+pd.Timedelta(hours=3); elig=bool(i>=220 and t>=pd.Timestamp('2022-08-26 06:00') and h in d.index and run.loc[h]>=720 and median.get(h.floor('D'),float('nan'))>=1e7)
   rows.append((sym,t,side,elig))
expected=set(map(tuple,published[['sym','decision_bar_open_utc','side','eligible']].itertuples(index=False,name=None))); actual=set(rows)
report['signals']={'published_rows':len(published),'independent_rows':len(rows),'published_unique':len(expected),'independent_unique':len(actual),'missing':len(expected-actual),'extra':len(actual-expected),'missing_examples':[str(x) for x in list(expected-actual)[:10]],'extra_examples':[str(x) for x in list(actual-expected)[:10]],'independent_eligible':sum(r[3] for r in rows)}
(out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report['signals'],indent=2))



assert not report['sha256_file_mismatches'] and not report['aggregate_disagreements']
assert all(v==0 for v in report['label_disagreement_counts'].values())
assert report['signals']['missing']==report['signals']['extra']==0
