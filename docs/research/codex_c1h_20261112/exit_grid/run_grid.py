"""Bounded two-worker discovery scan, all162dependent configurations retained."""
import json,subprocess,sys,time,hashlib,itertools,concurrent.futures
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
R=Path(__file__).resolve().parent
BASE=['c1h_h1_ema_pullback','c1h_h2_compression_breakout','c1h_h3_keltner_reclaim','c1h_h4_rsi2_reset','c1h_h5_volume_impulse','c1h_h6_btc_relative']
KEYS=[f'{k}__s{s}_t{t}_b{b}' for s,t,b in itertools.product([2,3,4],[2,3,4],[12,24,48]) for k in BASE]
def stamp():return datetime.now(ZoneInfo('Africa/Cairo')).isoformat()
def atomic(p,d):tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(d,indent=2));tmp.replace(p)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for n,h in json.loads((R/'execution_freeze.json').read_text())['code_sha256'].items():assert sha(R/n)==h,'Execution freeze changed'
jobs=[(ds,k,bp,path) for k in KEYS for ds,bp,path in [('cohort9_1h',15,'worst'),('cohort9_1h',7,'worst'),('cohort9_1h',23,'worst'),('cohort9_1h',0,'path'),('long8_1h',15,'worst'),('long8_1h',7,'worst'),('long8_1h',23,'worst')]]
def run(c):
 ds,k,bp,path=c;tag=f'{ds}.{k}.{bp}.{path}';t=time.monotonic()
 with (R/'logs'/(tag+'.log')).open('w') as out:p=subprocess.run([sys.executable,str(R/'run_case.py'),'--dataset',ds,'--key',k,'--bps',str(bp),'--path',path],stdout=out,stderr=subprocess.STDOUT)
 return {'case':c,'exit_code':p.returncode,'elapsed_s':round(time.monotonic()-t,2),'log':tag+'.log'}
def metrics(k,ds,bp):
 path='path' if bp==0 else 'worst';p=R/'results'/ds/k/f'{bp}bps_{path}'/'result.json'
 return json.loads(p.read_text()) if p.is_file() else None
def screen():
 out=[]
 for k in KEYS:
  required=[metrics(k,ds,bp) for ds in ['cohort9_1h','long8_1h'] for bp in [7,15,23]]
  if not all(required):continue
  failures=[];row={'key':k,'classification':'HISTORICAL_SCREEN_ONLY','independent_validation':False}
  for ds in ['cohort9_1h','long8_1h']:
   d15=metrics(k,ds,15);m=d15['metrics'];d23=metrics(k,ds,23)['metrics'];prefix='nine' if ds=='cohort9_1h' else 'long'
   row[prefix+'_meanR15']=m['avg_R'];row[prefix+'_meanR23']=d23['avg_R'];row[prefix+'_freq']=m['closes_per_calendar_week'];row[prefix+'_dd_pct']=m['mark_to_market_max_drawdown']*100
   if not m['economic_window_complete'] or m['liquidations'] or m['feasibility'].get('unknown_symbols'):failures.append(prefix+':execution')
   if m['n']<100:failures.append(prefix+':sample')
   if m['avg_R'] is None or m['avg_R']<=0:failures.append(prefix+':15bp')
   if d23['avg_R'] is None or d23['avg_R']<=0:failures.append(prefix+':23bp')
   if not m['ex_best_coin_positive']:failures.append(prefix+':bestcoin')
   if m['closes_per_calendar_week']<2:failures.append(prefix+':frequency')
   for period,st in m['split_stats'].items():
    if st['n']<30 or st['mean_R']<=0:failures.append(prefix+':split:'+period)
   start=datetime.fromisoformat(d15['coverage']['first_utc']);end=datetime.fromisoformat(d15['coverage']['last_utc'])
   eligible=[]
   for half,st in m['halfyear_stats'].items():
    yr=int(half[:4]);hn=int(half[-1]);lo=datetime(yr,1 if hn==1 else 7,1);hi=datetime(yr,7,1) if hn==1 else datetime(yr+1,1,1)
    if lo>=start and hi<=end:eligible.append(st['sum_R']>0)
   row[prefix+'_positive_complete_halfyear_fraction']=sum(eligible)/len(eligible) if eligible else None
   if not eligible or sum(eligible)/len(eligible)<.5:failures.append(prefix+':halfyears')
  row['screen_pass']=not failures;row['failures']=failures;out.append(row)
 atomic(R/'screen.json',{'at_cairo':stamp(),'complete_configurations':len(out),'total_configurations':162,'screen_pass_count':sum(x['screen_pass'] for x in out),'warning':'ConsumedDEV dependent-grid discovery, NOT qualification; no automatic account activation','rows':out})
 return out
def post_result(rows,final):
 passed=[x for x in rows if x['screen_pass']];top=sorted(rows,key=lambda x:min(x['nine_meanR15'],x['long_meanR15']),reverse=True)[:5]
 lines=['<!-- agent: codex -->',f'**C1H exit-grid {"FINAL" if final else "first checkpoint"} — {stamp()}**',f'{len(rows)}/162 dependent configurations complete; {len(passed)} pass the preregistered HISTORICAL screen. This is consumedDEV parameter discovery, NOT independently verified alpha, fresh holdout or activation approval. All configurations retained.','|Configuration|9coin R15|Long R15|9coin R23|Long R23|Screen|','|---|---:|---:|---:|---:|---|']
 for x in top:lines.append(f"|{x['key']}|{x['nine_meanR15']:+.3f}|{x['long_meanR15']:+.3f}|{x['nine_meanR23']:+.3f}|{x['long_meanR23']:+.3f}|{'PASS research only' if x['screen_pass'] else 'FAIL'}|")
 lines+=['Every funded case uses direct7/15/23bp PER EXECUTED SIDE + .01%/8h both-side funding stress proxy, max1lot/sleeve, initial$8risk with unchanged BT compounding. No native-funding/PIT qualification. No account/settings/positions changed. Candidate activation requires independent signal/execution/account-fit/protection review and a frozen prospective arm; historical grid winners are not evidence of durable profitability.','ROADMAP FIT: frequent after-cost strategy economics. DEFERRED: any activation/mainnet from a grid ranking.','LANES: Build Claude separate1h research; Evidence bounded2worker grid; Integration historical screen and forward-row checks.']
 p=R/('final-post.md' if final else 'checkpoint-post.md');p.write_text('\n'.join(lines),encoding='utf-8');res=subprocess.run(['gh','issue','comment','219','--repo','MoeAZack/ZackBot2','--body-file',str(p)],capture_output=True,text=True)
 atomic(R/('final-post-result.json' if final else 'checkpoint-post-result.json'),{'exit_code':res.returncode,'url':res.stdout.strip(),'error':res.stderr[-500:] if res.returncode else None})
started=time.monotonic();done=[];failed=[];checkpoint=False
atomic(R/'progress.json',{'status':'RUNNING','at_cairo':stamp(),'done':0,'total':len(jobs),'workers':2})
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
 fs={ex.submit(run,c):c for c in jobs}
 for f in concurrent.futures.as_completed(fs):
  row=f.result();done.append(row)
  if row['exit_code']:failed.append(row)
  atomic(R/'progress.json',{'status':'RUNNING','at_cairo':stamp(),'done':len(done),'total':len(jobs),'failed':failed,'elapsed_s':round(time.monotonic()-started,1),'last':row})
  print(json.dumps(row),flush=True)
  if len(done)%42==0:
   rows=screen()
   if len(rows)>=6 and not checkpoint:post_result(rows,False);checkpoint=True
rows=screen();s=subprocess.run([sys.executable,str(R/'summarize.py')],capture_output=True,text=True);post_result(rows,True)
atomic(R/'progress.json',{'status':'COMPLETE' if not failed and not s.returncode else 'COMPLETED_WITH_ERRORS','at_cairo':stamp(),'done':len(done),'total':len(jobs),'failed':failed,'elapsed_s':round(time.monotonic()-started,1),'summary_exit':s.returncode,'complete_configurations':len(rows),'screen_pass_count':sum(x['screen_pass'] for x in rows)})