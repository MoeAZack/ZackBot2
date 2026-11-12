"""Independent candidate tapes using accepted, UNCHANGED backtest.run; no Claude gen/overlay imports."""
import sys,json,csv,hashlib,time,math,os
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parent;DATASET=sys.argv[1];sys.path.insert(0,str(ROOT/'source'));import backtest as B
try:
 import ctypes;ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
except Exception:pass
specs=[('exp_vwaprev','exp_vwaprev','both'),('exp_range','exp_range','both'),('exp_mom_short','exp_mom_short','short'),('ema_mom','ema_mom','long'),('donchian_ens','donchian_ens','long'),('squeeze_tp','squeeze_tp','long'),('exp_breakout','exp_breakout','both'),('exp_trend','exp_trend','both'),('ema_st_S','ema_st','short'),('bear_breakdown','bear_breakdown','short'),('ema_st_L','ema_st','long'),('hot_coin','hot_coin','long')]
if DATASET=='short15':specs=[(k,k,'both') for k in ['exp_scalp','exp_structure','range_scalp','failed_breakout_scalp','momentum_burst_scalp','quick_bank_runner']]
if DATASET=='long4':specs=[x for x in specs if x[0]!='exp_vwaprev']
meta=json.loads((ROOT/'preregistration.json').read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for n,h in meta['source_sha256'].items():assert sha(ROOT/'source'/n)==h
folder=ROOT/'inputs'/DATASET;raw={p.stem:pd.read_csv(p,parse_dates=['t']) for p in folder.glob('*.csv')};book=B.Book(raw);assert not book.gaps
out=ROOT/'tapes-path'/DATASET;out.mkdir(parents=True,exist_ok=True);step=pd.Timedelta(minutes=15) if DATASET=='short15' else pd.Timedelta(hours=4)
manifest={'script_sha256':sha(Path(__file__)),'preregistration_sha256':sha(ROOT/'preregistration.json'),'source':meta['head'],'dataset':DATASET,'cases':specs,'bars':len(book.t),'status':'RUNNING'}
(out/'generation_manifest.json').write_text(json.dumps(manifest,indent=2));completed=[]
for ident,key,side in specs:
 for bps in [0,7,15,23]:
  stem=f'{ident}.{bps}';path=out/(stem+'.csv');receipt=out/(stem+'.json')
  if path.exists() and receipt.exists():completed.append(json.loads(receipt.read_text()));continue
  started=time.monotonic();fills=[];B.FEE=bps/10000;B.SLIP=0
  tr,cv=B.run(book,[dict(key=key,share=1.,risk=1e-6,max_pos=len(book.syms),sides=side)],start=1e9,max_lev=10,daily_halt=1.,warmup=220,fund_per_bar=0.,pessimistic='path',fill_log=fills)
  assert len(cv)==len(book.t)-220,'Candidate generator ended early; do not call partial tape full-window'
  assert not cv.attrs.get('liquidations'), 'Candidate generator liquidation invalidates tape'
  entries={(f['sym'],f['pos_i'],f['side']):f for f in fills if f['kind']=='entry'};rows=[]
  for t in tr.itertuples(index=False):
   entry=entries[(t.sym,int(t.i_in),int(t.side))];ri=float(entry['risk']);assert ri>0 and math.isfinite(t.R)
   tin=pd.Timestamp(book.t.iloc[int(t.i_in)]).tz_localize('UTC');tout=pd.Timestamp(book.t.iloc[int(t.i_out)]).tz_localize('UTC');rows.append(dict(uniqueid=f'{ident}|{t.sym}|{int(t.side)}|{tin.isoformat()}',sleeve=ident,symbol=t.sym,side=int(t.side),entry_utc=tin.isoformat(),exit_utc=(tout+step).isoformat(),exit_open_utc=tout.isoformat(),net_R_at_cost=float(t.R),initial_notional_per_risk=float(entry['ref'])*float(entry['qty'])/ri,source_risk_usd=ri,why=t.why,cost_bps=bps))
  fields=['uniqueid','sleeve','symbol','side','entry_utc','exit_utc','exit_open_utc','net_R_at_cost','initial_notional_per_risk','source_risk_usd','why','cost_bps']
  with path.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
  record={'strategy':ident,'bps':bps,'closed_candidates':len(rows),'mean_R':sum(r['net_R_at_cost'] for r in rows)/len(rows) if rows else None,'full_bar_coverage':True,'generation_equity_fraction_remaining':float(cv.iloc[-1])/1e9,'open_at_end_candidates':len(entries)-len(rows),'csv_sha256':sha(path),'elapsed':time.monotonic()-started,'not_qualified':True};receipt.write_text(json.dumps(record,indent=2));completed.append(record);(out/'generation_results.json').write_text(json.dumps(completed,indent=2));print(json.dumps(record),flush=True)
  del fills,entries,tr,cv,rows
manifest['status']='COMPLETE';manifest['completed_cases']=len(completed);(out/'generation_manifest.json').write_text(json.dumps(manifest,indent=2));print('COMPLETE',DATASET,len(completed),flush=True)
