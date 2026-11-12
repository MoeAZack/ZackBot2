import csv,json,hashlib,random,sys
from pathlib import Path
src=Path('F:/ZackBot2_data/DUP-COIN-SIDE-INDEPENDENT-20261112');out=Path('F:/ZackBot2_data/DUP-TIE-REPRO-20261112')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
orig=src/'overlay/overlay_sim.py'; code=orig.read_text();(out/'overlay_sim_original.py').write_bytes(orig.read_bytes())
code=code.replace("def simulate(rows,order,policy,risk_mode,initial=4000,tie='exit-first',multiple=1.5,funding_bps=0):", "def simulate(rows,order,policy,risk_mode,initial=4000,tie='exit-first',multiple=1.5,funding_bps=0,symbol_order=None):")
assert 'events.sort(key=lambda e:e[:5])' in code
code=code.replace('events.sort(key=lambda e:e[:5]);', "symbol_rank={s:i for i,s in enumerate(symbol_order)} if symbol_order is not None else None\n events.sort(key=lambda e:(e[0],e[1],e[2],symbol_rank[e[3]] if symbol_rank is not None else e[3],e[4]));")
(out/'overlay_sim.py').write_text(code);sys.path.insert(0,str(out));from overlay_sim import simulate
books=[('A4_4H_2Y','short4',['exp_vwaprev','exp_range','exp_mom_short','ema_mom','donchian_ens','squeeze_tp'],.002,1),('A4_4H_LONG','long4',['exp_range','exp_mom_short','ema_mom','donchian_ens','squeeze_tp'],.002,1),('OLD3_2Y','short4',['bear_breakdown','ema_st_L','hot_coin'],.004,2),('OLD3_LONG','long4',['bear_breakdown','ema_st_L','hot_coin'],.004,2)]
uid='ema_st_L|XRPUSDT|1|2024-11-06T12:00:00+00:00';manifest={'original_overlay_sha256':sha(orig),'copy_sha256':sha(out/'overlay_sim_original.py'),'modified_sha256':sha(out/'overlay_sim.py'),'tapes':{},'semantics':'timestamp, exit-before-entry, declared sleeve, permuted symbol, uniqueid; same permutation all policies; fixed risk, no funding, close-of-exit-bar','source_head':'7b5beecb3a964d4ce36aa7187a734093ffe6818a','review_head':'09eb1e9ebd5e1ff0a9747a386faf24612c78a6b0','exclusion_uniqueid':uid,'orders':'seed 0 sorted symbols; seed 1..39 independent random.Random(seed).shuffle sorted symbols','scope':'fixed consumed DEV tapes, not point-in-time selection or qualification'}
results=[]
for tape in ['tapes','tapes-path']:
 for book,dataset,keys,frac,maxp in books:
  for cost in [15,7,23]:
   rows=[]
   for key in keys:
    p=src/tape/dataset/(key+'.'+str(cost)+'.csv');manifest['tapes'][str(p)]=sha(p)
    with p.open(newline='') as f:
     for raw in csv.DictReader(f):
      r={k:raw[k] for k in ['uniqueid','sleeve','symbol','side','entry_utc','exit_utc','net_R_at_cost','initial_notional_per_risk']};r['risk_fraction']=frac;r['max_positions']=maxp;rows.append(r)
   symbols=sorted({r['symbol'] for r in rows});variants=[('all',rows)]
   if book=='OLD3_LONG':
    filtered=[r for r in rows if r['uniqueid']!=uid];assert len(rows)-len(filtered)==1,(tape,cost,len(rows)-len(filtered));variants.append(('drop_XRP',filtered))
   for variant,rr in variants:
    for seed in range(40):
     order=symbols.copy()
     if seed:random.Random(seed).shuffle(order)
     labels=[('baseline','baseline',1.5),('cap2','B',2)] if book.startswith('A4') else [('baseline','baseline',1.5),('refuse','A',1.5)];vals={}
     for label,policy,mult in labels:
      z=simulate(rr,keys,policy,'fixed',initial=4000,tie='exit-first',multiple=mult,symbol_order=order)['summary'];vals[label]=(z['terminal_equity']/4000-1)*100
     results.append({'tape':tape,'book':book,'cost':cost,'variant':variant,'seed':seed,'symbol_order':'|'.join(order),'baseline_pct':vals['baseline'],'policy_pct':vals[labels[1][0]],'delta_pp':vals[labels[1][0]]-vals['baseline'],'candidates':len(rr)})
   with (out/'results.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(results[0]));w.writeheader();w.writerows(results)
   (out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(tape,book,cost,'complete',flush=True)
print('COMPLETE',len(results),flush=True)
