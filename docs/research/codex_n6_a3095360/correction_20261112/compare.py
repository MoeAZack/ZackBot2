import importlib.util, pathlib, json, csv, hashlib
from datetime import timedelta
old=pathlib.Path('F:/ZackBot2_data/N6-independent-a3095360')
spec=importlib.util.spec_from_file_location('ind',old/'independent_n6.py'); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
paths={s:m.REPO/'data_long/4h'/f'{s}_4h.csv' for s in m.SYMS}
freeze=json.loads((old/'freeze.json').read_text());assert all(m.digest(paths[s])==freeze['inputs_sha256'][s] for s in m.SYMS)
frames={s:m.load(paths[s]) for s in m.SYMS}; times=sorted(set.intersection(*(set(frames[s]) for s in m.SYMS)))
b=[frames['BTCUSDT'][t] for t in times]; bu=[];bd=[]
for i in range(len(times)):
 bu.append(i>=20 and b[i][3]>max(x[1] for x in b[i-20:i]));bd.append(i>=20 and b[i][3]<min(x[2] for x in b[i-20:i]))
sets={k:set() for k in ['ours','frozen','frozen_no_close','ours_with_close']}
for s in m.SYMS[1:]:
 d=[frames[s][t] for t in times];fast=m.ema([x[3] for x in d],50);slow=m.ema([x[3] for x in d],200)
 for i in range(20,len(times)):
  hi=max(x[1] for x in d[i-20:i]);lo=min(x[2] for x in d[i-20:i]); c=d[i][3]
  # Cross at i=20 has no previous rolling extreme; frozen xup returns false.
  for side,br,prior in [(1,bu,bu[i-1]),(-1,bd,bd[i-1])]:
   event=br[i] and not prior
   fcross=event and i>=21
   trend=(fast[i]>slow[i]) if side==1 else (fast[i]<slow[i])
   directional=(c<hi) if side==1 else (c>lo)
   inside=lo<c<hi
   close_ema=(c>fast[i]) if side==1 else (c<fast[i])
   key=(s,times[i].isoformat(),side,i)
   if i>=199 and i<len(times)-1 and event and trend and inside:sets['ours'].add(key)
   if fcross and trend and directional and close_ema:sets['frozen'].add(key)
   if fcross and trend and directional:sets['frozen_no_close'].add(key)
   if i>=199 and i<len(times)-1 and event and trend and inside and close_ema:sets['ours_with_close'].add(key)
def compare(label,subset):
 a={x for x in sets['ours'] if subset(x)};z={x for x in sets['frozen'] if subset(x)}
 return {'label':label,'ours':len(a),'frozen':len(z),'intersection':len(a&z),'ours_only':len(a-z),'frozen_only':len(z-a),'ours_only_removed_by_close_ema':len((a-z)-sets['ours_with_close']),'ours_with_close':len(a&sets['ours_with_close'])}
# Original implementation begins at index 219, and excludes last bar; full comparable requires account for independent warmup assumption.
allrows=compare('full_frozen_vs_independent_min199',lambda x:True)
warm=compare('common_after_warmup220_exclude_final',lambda x:219<=x[3]<len(times)-1)
fullraw={'frozen':len(sets['frozen']),'frozen_no_close':len(sets['frozen_no_close']),'frozen_first_219':sum(x[3]<219 for x in sets['frozen'])}
ours,dec,_=m.signal_sets(frames); assert dec=={(s,t,k) for s,t,k,i in sets['ours'] if i>=219}
extra=sorted((x for x in sets['ours']-sets['frozen'] if x[3]>=219),key=lambda x:(x[1],x[0],x[2]))
out=pathlib.Path('F:/ZackBot2_data/N6-discrepancy-20261112');out.mkdir(exist_ok=False)
with (out/'ours_only_after_warmup_decision.csv').open('x',newline='') as f:
 w=csv.writer(f);w.writerow(['symbol','decision_candle_open_utc','side']);w.writerows((s,t,k) for s,t,k,i in extra)
res={'source':'frozen research/newstrat_cp2.py and strategies.py at b75fa28b; pure stdlib reproduction with existing independent_n6.py and SHA-verified frozen candle files','time_alignment':'decision candle open UTC; original candidate CSV is next-candle entry timestamp','n_candles':len(times),'counts_full':allrows,'counts_after_warmup220':warm,'frozen_full_raw':fullraw,'examples_ours_not_frozen':[(s,t,k) for s,t,k,i in extra[:10]],'status':'FORWARD_ONLY'}
(out/'comparison.json').write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
