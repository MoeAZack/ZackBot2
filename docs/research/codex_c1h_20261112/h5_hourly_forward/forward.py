"""FWD-01 research-only prospective replay; never connects to trading accounts."""
import json,sys,time,hashlib,os,subprocess,traceback
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import requests,pandas as pd
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'source'))
import strategies as S,backtest as BT,feasibility as F
import h5_hourly_long as H
S.STRATEGIES[H.KEY] = dict(H.RESEARCH)
API='https://fapi.binance.com'
STEPS={'15m':900000,'1h':3600000,'4h':14400000,'1d':86400000,'1m':60000}
def get(path,params=None,base=API):
 r=requests.get(base+path,params=params,timeout=30);r.raise_for_status();return r.json()
def write(p,obj):
 p=Path(p);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(obj,indent=2,default=str));os.replace(tmp,p)
def fetch(pair,boundary):
 sym,tf=pair;step=STEPS[tf];end=boundary//step*step; mark=sym.startswith('MARK_'); api_sym=sym.removeprefix('MARK_'); count=18000 if tf=='1m' else 300
 p=ROOT/'data'/f'{sym}-{tf}.json';old=json.loads(p.read_text()) if p.exists() else []
 start=int(old[-1][0])+step if old else end-count*step
 incoming=[]
 while start<end:
  chunk=get('/fapi/v1/markPriceKlines' if mark else '/fapi/v1/klines',{'symbol':api_sym,'interval':tf,'startTime':start,'endTime':end-1,'limit':1000})
  if not chunk:raise ValueError('missing_market_candles')
  incoming.extend(x for x in chunk if int(x[6])<end);nxt=int(chunk[-1][0])+step
  if nxt<=start:raise ValueError('no_market_progress')
  start=nxt
 raw=old+incoming;times=[int(x[0]) for x in raw]
 if len(raw)<count or times[-1]!=end-step or any(b-a!=step for a,b in zip(times,times[1:])):raise ValueError('nonconsecutive_market_history')
 if any(int(x[6])>=end for x in raw):raise ValueError('open_candle')
 write(p,raw)
 frame=pd.DataFrame([{'t':pd.to_datetime(int(x[0]),unit='ms'),'o':float(x[1]),'h':float(x[2]),'l':float(x[3]),'c':float(x[4]),'v':float(x[5]),'qv':float(x[7]),'ntrades':float(x[8]),'taker_buy_base':float(x[9]),'taker_buy_quote':float(x[10])} for x in raw])
 return pair,frame,hashlib.sha256(p.read_bytes()).hexdigest()
def evaluate(boundary,probe=False):
 m=json.loads((ROOT/'freeze.json').read_text())
 verify_freeze(m)
 for name,h in m['source_sha256'].items():
  if hashlib.sha256((ROOT/'source'/name).read_bytes()).hexdigest()!=h:raise RuntimeError('frozen_source_changed')
 pairs=[(s,tf) for tf in sorted({a['tf'] for a in m['arms']}) for s in m['symbols']]
 books={};hashes={};fail={};extras={}
 with ThreadPoolExecutor(max_workers=3) as pool:
  futures={pair:pool.submit(fetch,pair,boundary) for pair in pairs}
  for pair,f in futures.items():
   try:
    _,frame,h=f.result();books.setdefault(pair[1],{})[pair[0]]=frame;hashes['|'.join(pair)]=h
   except Exception as ex:fail['|'.join(pair)]=type(ex).__name__+': '+str(ex)[:100]
 rulefile=ROOT/'testnet-rules.json' 
 if not rulefile.exists():
  raw=get('/fapi/v1/exchangeInfo',base='https://testnet.binancefuture.com');write(rulefile,raw)
 rules=F.rules_from_exchange_info(json.loads(rulefile.read_text()))
 if any(s not in rules for s in m['symbols']):raise RuntimeError('testnet_rule_missing')
 rows=[]
 for arm in m['arms']:
  row={'arm':arm['id'],'tf':arm['tf'],'status':'INCOMPLETE','results':[]}
  try:
   raw=books[arm['tf']]
   if set(raw)!=set(m['symbols']):raise ValueError('missing_symbol_data')
   book=BT.Book(raw)
   # Do not invent inputs for microstructure/mark/minute strategies. KeyErrors stay INCOMPLETE.
   for sl in arm['sleeves']:
    for sym in m['symbols']:book.raw_sig(sl['key'],sym,sl.get('params'))
   for bps in m['cost_bps_per_executed_side']:
    BT.FEE=(bps-2)/10000;BT.SLIP=.0002
    tr,cv=BT.run(book,arm['sleeves'],start=4000,max_lev=10,daily_halt=.03,t0=pd.to_datetime(m['t0_ms'],unit='ms'),warmup=220,fund_per_bar=.0001/8,exchange_rules=rules,risk_rules={"open_risk_cap":{"mode":"enforce","pct":2.5}})
    outdir=ROOT/'rows'/str(boundary);outdir.mkdir(exist_ok=True)
    stem=arm['id'].replace(':','_').replace('+','_')+f'-{bps}'
    tr.to_csv(outdir/(stem+'-trades.csv'),index=False);cv.to_csv(outdir/(stem+'-curve.csv'))
    row['results'].append({'cost_bps_per_side':bps,'closed_trades':len(tr),'net_closed_usd':float(tr.pnl.sum()) if len(tr) else 0,'mean_r':float(tr.R.mean()) if len(tr) else None,'max_drawdown_usd':float((cv.cummax()-cv).max()) if len(cv) else 0,'coin_net_usd':tr.groupby('sym').pnl.sum().to_dict() if len(tr) else {},'mtm':float(cv.iloc[-1]) if len(cv) else 4000,'evaluated_bars':len(cv),'feasibility':cv.attrs.get('feasibility')})
   if boundary<=m['t0_ms'] and any(x['closed_trades'] != 0 or x['evaluated_bars'] != 0 for x in row['results']): raise RuntimeError('baseline_not_empty')
   row['status']='BASELINE_VERIFIED' if boundary<=m['t0_ms'] else 'RESEARCH_REPLAY_COMPLETE'
   row['qualification']='NONE: research path model and funding stress proxy; independent parity and native funding reconciliation outstanding'
  except Exception as ex:row['error']=type(ex).__name__+': '+str(ex)[:140]
  rows.append(row)
 result={'id':'H5-HOURLY-FWD-20261112','head':m['head'],'boundary_ms':boundary,'boundary_cairo':datetime.fromtimestamp(boundary/1000,ZoneInfo('Africa/Cairo')).isoformat(),'observed_utc':datetime.now(timezone.utc).isoformat(),'probe_only':probe,'market_data_sha256':hashes,'data_failures':fail,'arms':rows,'complete_arms':sum(r['status']!='INCOMPLETE' for r in rows),'incomplete_arms':sum(r['status']=='INCOMPLETE' for r in rows)}
 p=ROOT/('probe.json' if probe else f'rows/{boundary}/summary.json');p.parent.mkdir(exist_ok=True,parents=True);write(p,result)
 return result


def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def verify_freeze(m):
 expected=(ROOT/'freeze.sha256').read_text().strip()
 if digest(ROOT/'freeze.json') != expected: raise RuntimeError('frozen_manifest_changed')
 for path,h in m['immutable_sha256'].items():
  if digest(ROOT/path)!=h: raise RuntimeError('frozen_input_changed: '+path)

def post(result):
 row=result['arms'][0]; results=row.get('results',[])
 body=('<!-- agent: codex -->\nH5 hourly prospective research '+result['boundary_cairo']+'; '+row['status']+'. '+str([(x['cost_bps_per_side'],x['closed_trades'],round(x['net_closed_usd'],4)) for x in results])+'. Columns: cost bp/side, closed trades, hypothetical closed USD. Baseline is not observed trading and zero closed trades are explicit only for complete results. Hypothetical solo book, not live parity or independent P&L; 1bp/8h funding stress proxy, not native funding. Evidence: `F:\\ZackBot2_data\\H5-HOURLY-FWD-20261112\\rows\\'+str(result['boundary_ms'])+'\\summary.json`.\nROADMAP FIT: prospective H5 strategy economics. DEFERRED: parity, native funding, mainnet.\nLANES: Build H5 PAPER; Evidence independent validation; Integration runtime and CI.')
 p=ROOT/'post.md';p.write_text(body,encoding='utf-8')
 # Reconcile signed marker before retries so a lost response cannot duplicate a row.
 marker='H5 hourly prospective research '+result['boundary_cairo']+';'
 q=subprocess.run(['gh','api','--paginate','repos/MoeAZack/ZackBot2/issues/219/comments','--jq','.[].body'],capture_output=True,text=True,encoding='utf-8')
 if q.returncode: raise RuntimeError('github_reconciliation_failed')
 if marker not in q.stdout:
  r=subprocess.run(['gh','api','repos/MoeAZack/ZackBot2/issues/219/comments','-F','body=@'+str(p),'--jq','.html_url'],capture_output=True,text=True,encoding='utf-8')
  if r.returncode: raise RuntimeError('github_post_failed')
  with (ROOT/'posts.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps({'boundary':result['boundary_ms'],'url':r.stdout.strip()})+'\n')

def validate_cursor(c,m):
 nxt=c['next_ms']
 if not isinstance(nxt,int) or nxt<m['t0_ms'] or (nxt-m['t0_ms'])%3600000: raise RuntimeError('invalid_cursor')
 if nxt>m['t0_ms']:
  prev=nxt-3600000; p=ROOT/'rows'/str(prev)/'summary.json'
  if c.get('previous_boundary')!=prev or not p.exists() or digest(p)!=c.get('summary_sha256'): raise RuntimeError('cursor_row_mismatch')
  for name,h in c.get('data_sha256',{}).items():
   if digest(ROOT/'data'/name)!=h: raise RuntimeError('cursor_data_changed')
  if not c.get('data_sha256'): raise RuntimeError('cursor_missing_data_hashes')
 return nxt

def main():
 import msvcrt
 lock=(ROOT/'runner.lock').open('a+b'); lock.seek(0);lock.write(b'1');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
 while True:
  try:
   m=json.loads((ROOT/'freeze.json').read_text());verify_freeze(m)
   c=json.loads((ROOT/'cursor.json').read_text()); nxt=validate_cursor(c,m)
   now=int(get('/fapi/v1/time')['serverTime'])
   if now>=nxt+15000:
    r=evaluate(nxt);post(r);write(ROOT/'cursor.json',{'next_ms':nxt+3600000,'previous_boundary':nxt,'summary_sha256':digest(ROOT/'rows'/str(nxt)/'summary.json'),'data_sha256':{p.name:digest(p) for p in (ROOT/'data').glob('*.json')}})
   write(ROOT/'heartbeat.json',{'utc':datetime.now(timezone.utc).isoformat(),'next_ms':nxt,'status':'RUNNING'})
  except Exception as ex: write(ROOT/'heartbeat.json',{'utc':datetime.now(timezone.utc).isoformat(),'status':'ERROR','error':type(ex).__name__+': '+str(ex)[:180]})
  time.sleep(60)
if __name__=='__main__': main()
