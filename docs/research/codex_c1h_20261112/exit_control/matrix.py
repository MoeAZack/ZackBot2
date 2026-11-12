"""Bounded two-worker hourly research matrix; no runtime/account access. Matched exit diagnosis, not new independent alpha."""
import sys,json,subprocess,time,hashlib,concurrent.futures
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
R=Path(__file__).resolve().parent
KEYS=['c1h_h1_ema_pullback','c1h_h2_compression_breakout','c1h_h3_keltner_reclaim','c1h_h4_rsi2_reset','c1h_h5_volume_impulse','c1h_h6_btc_relative']
def stamp():return datetime.now(ZoneInfo('Africa/Cairo')).isoformat()
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def atomic(p,d):t=p.with_suffix('.tmp');t.write_text(json.dumps(d,indent=2));t.replace(p)
contract=json.loads((R/'execution_freeze.json').read_text())
for name,sha in contract['code_sha256'].items():assert h(R/name)==sha,'Frozen execution code changed'
first=[('cohort9_1h',k,15,'worst') for k in KEYS]
allcases=[(ds,k,bp,path) for ds in ['cohort9_1h','long8_1h'] for k in KEYS for bp,path in [(15,'worst'),(7,'path'),(15,'path'),(23,'path'),(7,'worst'),(23,'worst'),(0,'path')]]
jobs=first+[x for x in allcases if x not in first];done=[];failed=[];started=time.monotonic()
def run(c):
 ds,k,bp,path=c;tag=f'{ds}.{k}.{bp}.{path}';t=time.monotonic()
 with (R/'logs'/(tag+'.log')).open('w') as out:
  p=subprocess.run([sys.executable,str(R/'run_case.py'),'--dataset',ds,'--key',k,'--bps',str(bp),'--path',path],stdout=out,stderr=subprocess.STDOUT)
 return dict(case=c,exit_code=p.returncode,elapsed_s=round(time.monotonic()-t,2),log=tag+'.log')
def first_table():
 rows=[]
 for ds,k,bp,path in first:
  p=R/'results'/ds/k/f'{bp}bps_{path}'/'result.json'
  if not p.is_file():return
  d=json.loads(p.read_text());m=d['metrics'];rows.append((k,m))
 lines=['<!-- agent: codex -->',f'**C1H matched FIXED-STOP exit-control first six results — {stamp()}**','Same original entry signals, PROT OFF; matched management diagnostic, not holdout. Nine cohort coins, 1h, consumedDEV Oct2024–Sep2026, adverse intrabar fills,15bp PER SIDE plus stated funding stress proxy. Not independent profitability qualification.','|Candidate|Closed|Mean net R|Closes/week|Marked DD|','|---|---:|---:|---:|---:|']
 for k,m in rows:lines.append(f"|{k}|{m['n']}|{m['avg_R']:+.3f}|{m['closes_per_calendar_week']:.2f}|{100*m['mark_to_market_max_drawdown']:.1f}%|")
 lines+=['Remaining direct 7/15/23bp path/worst and gross diagnostics + longer8coin history still running. No account or sleeve activated. Historical split is consumedDEV, not sealed holdout.','ROADMAP FIT: frequent after-cost replacement search. DEFERRED: activation until economic and independent execution gates.','LANES: Build Claude separate hourly candidates; Evidence C1H two-worker cost matrix running; Integration no runtime changes, forward studies continue.']
 post=R/'first-six-post.md';post.write_text('\n'.join(lines));p=subprocess.run(['gh','issue','comment','219','--repo','MoeAZack/ZackBot2','--body-file',str(post)],capture_output=True,text=True)
 atomic(R/'first-six-post-result.json',{'exit_code':p.returncode,'url':p.stdout.strip(),'error':p.stderr[-500:] if p.returncode else None,'posted_cairo':stamp()})
posted=False
atomic(R/'progress.json',{'status':'RUNNING','at_cairo':stamp(),'total':len(jobs),'done':0})
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
 futures={ex.submit(run,c):c for c in jobs}
 for f in concurrent.futures.as_completed(futures):
  row=f.result();done.append(row)
  if row['exit_code']:failed.append(row)
  atomic(R/'progress.json',{'status':'RUNNING','at_cairo':stamp(),'total':len(jobs),'done':len(done),'failed':failed,'elapsed_s':round(time.monotonic()-started,1),'last':row})
  print(json.dumps(row),flush=True)
  if not posted and all(any(tuple(x['case'])==c and not x['exit_code'] for x in done) for c in first):first_table();posted=True
p=subprocess.run([sys.executable,str(R/'summarize.py')],capture_output=True,text=True)
atomic(R/'progress.json',{'status':'COMPLETE' if not failed and not p.returncode else 'COMPLETED_WITH_ERRORS','at_cairo':stamp(),'total':len(jobs),'done':len(done),'failed':failed,'elapsed_s':round(time.monotonic()-started,1),'summary_exit':p.returncode,'summary_output':p.stdout.strip(),'summary_error':p.stderr[-2000:]})
print('MATRIX_FINISHED',p.returncode,'failed',len(failed),flush=True)