"""Independent N6 signal check from the owner's written rule. Standard library only.
No Claude research, strategies, indicator, backtest or execution code is imported.
Signals are not fills; open-position/cap/exit simulation is deliberately not inferred.
"""
import csv,json,hashlib
from pathlib import Path
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parent
REPO=Path('C:/Dev/ZackBot2_newstrat')
HEAD='a3095360e38e71035b82339a0284852a154831b3'
SYMS=['BTCUSDT','ETHUSDT','XRPUSDT','SOLUSDT','DOGEUSDT','BNBUSDT','LINKUSDT','AVAXUSDT']
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p):
 out={}
 with Path(p).open(newline='') as f:
  for r in csv.DictReader(f):
   t=datetime.fromisoformat(r['t']).replace(tzinfo=timezone.utc)
   v=tuple(float(r[k]) for k in ['o','h','l','c'])
   if t in out and out[t]!=v:raise ValueError('conflicting duplicate candle')
   out[t]=v
 return out
def ema(values,span):
 a=2/(span+1);z=None;out=[]
 for x in values:
  z=x if z is None else a*x+(1-a)*z;out.append(z)
 return out
def signal_sets(frames,warmup=220,edge=True,inside_both=True):
 times=sorted(set.intersection(*(set(frames[s]) for s in SYMS)))
 if not times or any(b-a!=timedelta(hours=4) for a,b in zip(times,times[1:])):raise ValueError('incomplete common 4h grid')
 btc=[frames['BTCUSDT'][t] for t in times];raw_up=[];raw_down=[]
 for i,b in enumerate(btc):
  raw_up.append(i>=20 and b[3]>max(x[1] for x in btc[i-20:i]))
  raw_down.append(i>=20 and b[3]<min(x[2] for x in btc[i-20:i]))
 entries=set();decisions=set()
 for sym in SYMS:
  if sym=='BTCUSDT':continue
  data=[frames[sym][t] for t in times];fast=ema([r[3] for r in data],50);slow=ema([r[3] for r in data],200)
  for i in range(max(warmup-1,199,20),len(times)-1):
   hi=max(r[1] for r in data[i-20:i]);lo=min(r[2] for r in data[i-20:i]);close=data[i][3]
   up=raw_up[i] and (not edge or not raw_up[i-1]);dn=raw_down[i] and (not edge or not raw_down[i-1])
   inside=lo<close<hi
   direction=1 if up and fast[i]>slow[i] and (inside if inside_both else close<hi) else (-1 if dn and fast[i]<slow[i] and (inside if inside_both else close>lo) else 0)
   if direction:
    decisions.add((sym,times[i].isoformat(),direction));entries.add((sym,times[i+1].isoformat(),direction))
 return entries,decisions,times

def main():
 paths={s:REPO/'data_long/4h'/f'{s}_4h.csv' for s in SYMS};ledger=REPO/'docs/research/newstrat_cp/cp_n6_btc_lead_catchup_4h.path.trades.csv'
 # Freeze rule interpretations BEFORE any signal-versus-ledger comparison. Never pick a variant by its match count.
 frozen={'head':HEAD,'signal_source':'owner written rule + docs/research/newstrat_cp_result.md; no implementation import/read','script_sha256':digest(__file__),'inputs_sha256':{s:digest(p) for s,p in paths.items()},'reference_sha256':digest(ledger),'spec_sha256':digest(REPO/'docs/research/newstrat_cp_result.md'),'primary':'strict FIRST breakout event: close through prior20 high/low; previous breakout-condition false; alt strictly inside BOTH previous20 extremes; EMA50/200 aligned, seeded firstclose; BTCexcluded','diagnostics':['directional inside only, still first event','continuous breakout condition, directional inside only (no first-event requirement)'],'alignment':'signal uses closed candle i; candidate next-open entry at candle i+1. Ledger t_in is entry-open, not decision-open. Common8-symbol contiguous grid; warmup220 matches documented setup. No execution/cap/exit code replay.','qualification':'FORWARD_ONLY regardless; no P&L rerun and no holdout tuning'}
 with (ROOT/'freeze.json').open('x') as f:json.dump(frozen,f,indent=2)
 frames={s:load(p) for s,p in paths.items()};reference=[]
 with ledger.open(newline='') as f:
  for r in csv.DictReader(f):reference.append((r['sym'],datetime.fromisoformat(r['t_in']).replace(tzinfo=timezone.utc).isoformat(),int(r['side'])))
 ref=set(reference);results=[]
 for label,edge,inside in [('PRIMARY_FIRST_INSIDE_RANGE',True,True),('DIAGNOSTIC_FIRST_DIRECTIONAL',True,False),('DIAGNOSTIC_CONTINUOUS_DIRECTIONAL',False,False)]:
  got,decisions,times=signal_sets(frames,edge=edge,inside_both=inside)
  matched=ref&got;missing=ref-got;extra=got-ref
  result={'interpretation':label,'reference_unique':len(ref),'reference_rows':len(reference),'candidate_entries':len(got),'matched':len(matched),'missing':len(missing),'extra':len(extra),'BTC_candidates':sum(s=='BTCUSDT' for s,_,_ in got),'first':times[0].isoformat(),'last':times[-1].isoformat(),'bars':len(times)};results.append(result)
  for kind,rows in [('candidate',got),('decision',decisions),('matched',matched),('missing',missing),('extra',extra)]:
   with (ROOT/(label+'.'+kind+'.csv')).open('w',newline='') as f:
    w=csv.writer(f);w.writerow(['symbol','candle_open_utc','side']);w.writerows(sorted(rows))
 output={'head':HEAD,'freeze_sha256':digest(ROOT/'freeze.json'),'results':results,'reference_duplicate_rows':len(reference)-len(ref),'conclusion':'Report primary literally; diagnostics expose FIRST/inside semantics. Raw signal extras are not execution parity failures by themselves; filled ledger excludes signals while held or capacity-constrained. Missing reference entries are a rule discrepancy. No independent execution or profitability parity claimed. N6 remains FORWARD_ONLY, ex-SOL evidence negative.'}
 (ROOT/'result.json').write_text(json.dumps(output,indent=2));print(json.dumps(output,indent=2))
if __name__=='__main__':main()
