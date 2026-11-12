import csv,json,hashlib,datetime,collections
from pathlib import Path
from decimal import Decimal as D
A=Path(r'C:\Dev\ZackBot_cowork_data\edge01_20261023\cache_15m'); B=Path(r'C:\Dev\paper-backup-20261105\candles_15m_research'); C=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112'); F=Path(r'F:\ZackBot2_data\MPE-INDEPENDENT-20261112'); O=Path(r'F:\ZackBot2_data\MPE-DATA-INDEPENDENT-20261112')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dt(t): return datetime.datetime.fromtimestamp(t/1000,datetime.timezone.utc).isoformat()
def rows(p):
 with p.open(newline='') as f:
  for r in csv.DictReader(f): yield (int(D(r['t'])),*[D(r[k]) for k in ['o','h','l','c','v','qv']])
co=json.loads((F/'contract.json').read_text()); quality=json.loads((F/'data_quality.json').read_text()); man=json.loads((C/'input_manifest.json').read_text()); out={'input_hashes':{p.name:sha(p) for p in [F/'contract.json',F/'data_quality.json',C/'input_manifest.json']},'manifest_matches_contract':sha(C/'input_manifest.json')==co['signal']['canonical_1h']['manifest_sha256'],'symbols':{}}
for s in sorted(co['execution_data']['sha256']):
 a=A/(s+'_15m.csv'); b=B/a.name; c=C/'clean'/(s+'_1h.csv'); xs=list(rows(a)); ys=list(rows(c)); keys=[x[0] for x in xs]; groups=collections.defaultdict(list)
 for x in xs: groups[x[0]//3600000*3600000].append(x)
 canon={x[0]:x for x in ys}; diffs=[]; compared=0; incomplete=0; missing=0; counts=collections.Counter()
 for t,g in sorted(groups.items()):
  if len(g)!=4 or sorted(x[0] for x in g)!=[t+i*900000 for i in range(4)]: incomplete+=1; continue
  g.sort(); agg=[g[0][1],max(x[2] for x in g),min(x[3] for x in g),g[-1][4],sum(x[5] for x in g),sum(x[6] for x in g)]
  if t not in canon: missing+=1; continue
  compared+=1; diff={}
  for name,x,y in zip(['o','h','l','c','v','qv'],agg,canon[t][1:]):
   if x!=y:
    rel=abs(x-y)/abs(y) if y else abs(x-y)
    if name in ('v','qv') and rel<=D('0.000001'): continue
    counts[name]+=1; diff[name]={'15m':str(x),'1h':str(y),'relative':str(rel)}
  if diff: diffs.append({'utc':dt(t),'fields':diff})
 result={'15m_sha256':sha(a),'backup_sha256':sha(b),'15m_hash_matches_backup':sha(a)==sha(b),'15m_hash_matches_contract':sha(a)==co['execution_data']['sha256'][s], 'canonical_sha256':sha(c),'canonical_hash_matches_manifest':sha(c)==man['symbols'][s]['csv_sha256'],'rows':len(xs),'first_utc':dt(keys[0]),'last_utc':dt(keys[-1]),'duplicates':len(keys)-len(set(keys)),'non_increasing':sum(y<=x for x,y in zip(keys,keys[1:])), 'step_not_15min':sum(y-x!=900000 for x,y in zip(keys,keys[1:])), 'off_grid':sum(t%900000!=0 for t in keys),'bad_ohlc':sum(not (min(o,h,l,cl)>0 and l<=min(o,cl)<=max(o,cl)<=h) for t,o,h,l,cl,v,q in xs),'nonpositive_volume_or_quote_volume':sum(v<=0 or q<=0 for t,o,h,l,cl,v,q in xs),'incomplete_hours':incomplete,'hours_compared':compared,'canonical_missing_for_15m_hours':missing,'canonical_window_hours_missing_15m':sum(1 for y in ys if keys[0]<=y[0]<=keys[-1] and y[0] not in groups),'mismatches':dict(counts),'difference_hours':diffs}
 result['quality_counts_match']=all(result[k]==quality[s][v] for k,v in [('rows','rows'),('duplicates','dup'),('bad_ohlc','ohlc_bad'),('incomplete_hours','incomplete_hours'),('hours_compared','hours_compared')]) and all(counts.get(k,0)==quality[s][v] for k,v in [('o','o_mismatch'),('h','h_mismatch'),('l','l_mismatch'),('c','c_mismatch'),('v','v_mismatch_gt1e-6')])
 out['symbols'][s]=result; print(s,len(xs),compared,dict(counts),'hours',len(diffs),'quality',result['quality_counts_match'],flush=True)
(O/'result.json').write_text(json.dumps(out,indent=2))
