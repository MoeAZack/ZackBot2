import csv,json,sys,gc,collections,hashlib,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'overlay'));from overlay_sim import simulate
MODE=sys.argv[1];OUT=ROOT/'runs-validated';OUT.mkdir(exist_ok=True)
A4=['exp_vwaprev','exp_range','exp_mom_short','ema_mom','donchian_ens','squeeze_tp'];SC=['exp_scalp','exp_structure','range_scalp','failed_breakout_scalp','momentum_burst_scalp','quick_bank_runner'];ALL=['exp_vwaprev','exp_range','exp_mom_short']+SC+['ema_mom','donchian_ens','squeeze_tp'];NEW=['exp_breakout','exp_trend','ema_st_S'];OLD=['bear_breakdown','ema_st_L','hot_coin']
BOOKS=[('A4_4H_2Y','short4',A4,.002,1),('A4_4H_LONG','long4',[x for x in A4 if x!='exp_vwaprev'],.002,1),('NEW3_2Y','short4',NEW,.004,2),('NEW3_LONG','long4',NEW,.004,2),('OLD3_2Y','short4',OLD,.004,2),('OLD3_LONG','long4',OLD,.004,2)]
if MODE=='all12':BOOKS=[('A4_ALL12_2Y','short4',ALL,.002,1)]
POLICIES=[('baseline','baseline',1.5),('refuse','A',1.5),('cap1p5','B',1.5),('cap2','B',2.)]

def load(book,dataset,keys,bps,fraction,maxp,timing='close',exclude=None):
 rows=[]
 for key in keys:
  src='short15' if key in SC else dataset
  with (ROOT/'tapes'/src/f'{key}.{bps}.csv').open(newline='') as f:
   for raw in csv.DictReader(f):
    if exclude=='outlier' and key=='ema_st_L' and raw['symbol']=='XRPUSDT' and raw['entry_utc'].startswith('2024-11-06T12:00:00'):continue
    if exclude=='year2024' and raw['entry_utc'].startswith('2024-'):continue
    r={k:raw[k] for k in ['uniqueid','sleeve','symbol','side','entry_utc','exit_utc','net_R_at_cost','initial_notional_per_risk']};r['risk_fraction']=fraction;r['max_positions']=maxp
    if timing=='open':
     ex=datetime.fromisoformat(raw['exit_open_utc']);en=datetime.fromisoformat(raw['entry_utc']);r['exit_utc']=max(ex,en+timedelta(microseconds=1)).isoformat()
    rows.append(r)
 return rows

matrix=ROOT/('matrix-'+MODE+'.json')
if not matrix.exists():matrix.write_text(json.dumps({'mode':MODE,'books':BOOKS,'primary_costs':[7,15,23],'policies':POLICIES,'sensitivity15':['exit-open (same-bar+1us)','equity-risk','funding3bp/8h at close'],'outlier_controls':'OLD3_LONG15bp remove specificXRP andremove2024 entries, all policies','source_model':'7b5beecb unchanged','purpose':'consumedDEV,defaultOFF,no runtimechanges'},indent=2))
SHA=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
CODE={'driver':SHA(Path(__file__)),'overlay':SHA(ROOT/'overlay/overlay_sim.py'),'preregistration':SHA(ROOT/'preregistration.json')}
summary_rows=[];yearrows=[]
for book,dataset,keys,frac,maxp in BOOKS:
 for bps in [7,15,23]:
  scenarios=[('primary','close','fixed',0,None)]
  if bps==15:scenarios += [('exit_open','open','fixed',0,None),('equity_risk','close','equity',0,None),('funding_stress','close','fixed',3,None)]
  if book=='OLD3_LONG' and bps==15:scenarios += [('drop_outlier','close','fixed',0,'outlier'),('drop_2024','close','fixed',0,'year2024')]
  for name,timing,riskmode,funding,exclude in scenarios:
   rows=load(book,dataset,keys,bps,frac,maxp,timing,exclude)
   for label,pol,mult in POLICIES:
    case=f'{book}.{bps}.{name}.{label}';folder=OUT/case;folder.mkdir(exist_ok=True)
    tape_hashes={key:SHA(ROOT/'tapes'/('short15' if key in SC else dataset)/f'{key}.{bps}.csv') for key in keys}
    fingerprint=hashlib.sha256(json.dumps({'code':CODE,'tapes':tape_hashes,'case':case,'keys':keys,'fraction':frac,'maxpos':maxp,'timing':timing,'riskmode':riskmode,'funding':funding,'exclude':exclude},sort_keys=True).encode()).hexdigest()
    if (folder/'summary.json').exists():
     s=json.loads((folder/'summary.json').read_text());assert s.get('run_fingerprint')==fingerprint,'Refusing stale cached result: '+case
    else:
     started=time.monotonic();result=simulate(rows,keys,pol,riskmode,initial=4000,tie='exit-first',multiple=mult,funding_bps=funding);s=result['summary'];s.update(case=case,book=book,bps=bps,scenario=name,policy_label=label,candidate_count=len(rows),realized_only=True,not_qualified=True,elapsed=time.monotonic()-started)
     s['run_fingerprint']=fingerprint;s['input_tape_sha256']=tape_hashes;s['code_sha256']=CODE
     s['refusal_counts']=dict(collections.Counter(r['reason'] for r in result['records'] if r['status']=='refused'));admit=[r for r in result['records'] if r['status']=='admitted']
     for filename,records in [('admitted.csv',admit),('curve.csv',result['curve'])]:
      if records:
       with (folder/filename).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
     (folder/'summary.json').write_text(json.dumps(s,indent=2,allow_nan=False));del result,admit;gc.collect()
    z={k:s.get(k) for k in ['case','book','bps','scenario','policy_label','terminal_equity','max_closed_equity_drawdown','first_10pct_closed_drawdown_utc','admitted','refused','joined_other_count','joined_other_average_R','nonjoined_other_count','nonjoined_other_average_R']};z['return_pct']=(s['terminal_equity']/4000-1)*100;z['dd_pct']=100*s['max_closed_equity_drawdown'];summary_rows.append(z)
    yearrows.extend(dict(case=case,book=book,bps=bps,scenario=name,policy=label,year=y,**v) for y,v in s['yearly'].items())
    with (ROOT/('results-validated-'+MODE+'.csv')).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(summary_rows[0]));w.writeheader();w.writerows(summary_rows)
    (ROOT/('yearly-validated-'+MODE+'.json')).write_text(json.dumps(yearrows,indent=2));print(json.dumps(z),flush=True)
   del rows;gc.collect()
print('OVERLAY COMPLETE',MODE,len(summary_rows),flush=True)
