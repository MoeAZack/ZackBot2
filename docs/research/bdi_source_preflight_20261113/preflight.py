import urllib.request,hashlib,json,zipfile,csv,io,collections,fractions,decimal
from pathlib import Path
root=Path(r'F:\ZackBot2_data\BDI-PREFLIGHT-20261113'); base='https://data.binance.vision/data/futures/um/daily/bookDepth/'
targets=[(s,d) for d in ['2026-09-02','2026-09-06'] for s in ['BTCUSDT','ETHUSDT','SOLUSDT']]+[('BTCUSDT','2025-01-15')]; results=[]; total=0; cap=16*1024*1024
try:
 for sym,day in targets:
  name=f'{sym}-bookDepth-{day}.zip'; url=base+sym+'/'+name; dest=root/name
  def fetch(u,limit):
   with urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'BDI-source-preflight/1.0'}),timeout=45) as resp:
    data=resp.read(limit+1)
    if len(data)>limit: raise RuntimeError('byte cap exceeded')
    return data,dict(resp.headers)
  checksum,ch=fetch(url+'.CHECKSUM',4096); (root/(name+'.CHECKSUM')).write_bytes(checksum); expected=checksum.decode().split()[0].lower()
  if len(expected)!=64: raise RuntimeError('invalid checksum')
  if dest.exists(): data=dest.read_bytes(); headers={'cache':'existing'}
  else: data,headers=fetch(url,cap-total); dest.write_bytes(data)
  total+=len(data)
  if total>cap: raise RuntimeError('cumulative ZIP cap exceeded')
  actual=hashlib.sha256(data).hexdigest()
  if actual!=expected: raise RuntimeError('checksum mismatch '+name)
  with zipfile.ZipFile(io.BytesIO(data)) as z:
   names=z.namelist()
   if len(names)!=1: raise RuntimeError('unexpected ZIP entries')
   with z.open(names[0]) as f:
    reader=csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',newline='')); schema=reader.fieldnames
    rows=0; times=collections.Counter(); pairs=collections.Counter(); bands={}; ordered=True; prev=None
    for row in reader:
     rows+=1; t=row['timestamp']; p=decimal.Decimal(row['percentage']); times[t]+=1; pairs[(t,p)]+=1
     if prev is not None and t<prev: ordered=False
     prev=t
     if p in (-1,1):
      b=bands.setdefault(str(int(p)),{'n':0,'depth':set(),'notional':set(),'ratio':set(),'zero':0,'longest':0,'run':0,'last':None,'times':[]})
      dep=decimal.Decimal(row['depth']); notional=decimal.Decimal(row['notional']); pair=(dep,notional)
      b['n']+=1; b['depth'].add(dep); b['notional'].add(notional)
      if dep: b['ratio'].add(fractions.Fraction(notional)/fractions.Fraction(dep))
      else: b['zero']+=1
      b['run']=b['run']+1 if pair==b['last'] else 1; b['longest']=max(b['longest'],b['run']); b['last']=pair; b['times'].append(t)
  bout={}
  for k,b in bands.items():
   ts=b['times']; bout[k]={'rows':b['n'],'unique_timestamps':len(set(ts)),'ordered':all(a<=c for a,c in zip(ts,ts[1:])),'first':ts[0],'last':ts[-1],'distinct_depth':len(b['depth']),'distinct_notional':len(b['notional']),'distinct_exact_notional_over_depth':len(b['ratio']),'zero_depth':b['zero'],'longest_identical_depth_notional_run':b['longest']}
  one=collections.Counter({t:n for (t,p),n in pairs.items() if p==-1}); plus=collections.Counter({t:n for (t,p),n in pairs.items() if p==1})
  result={'symbol':sym,'date':day,'url':url,'zip_bytes':len(data),'sha256':actual,'published_sha256':expected,'checksum_match':True,'zip_entry':names[0],'schema':schema,'rows':rows,'unique_timestamps':len(times),'rowgroups_per_timestamp':dict(sorted(collections.Counter(times.values()).items())),'duplicate_timestamp_percentage_pairs':sum(n-1 for n in pairs.values() if n>1),'timestamp_ordered':ordered,'first_timestamp':min(times),'last_timestamp':max(times),'minus_plus_same_timestamp_multiset':one==plus,'minus_plus_one_each_per_timestamp':all(one[t]==plus[t]==1 for t in times),'bands':bout,'last_modified':headers.get('Last-Modified')}
  results.append(result); (root/'evidence.json').write_text(json.dumps({'total_zip_bytes':total,'results':results},indent=2),encoding='utf-8'); print(json.dumps(result),flush=True)
except Exception as e:
 (root/'ERROR.txt').write_text(repr(e),encoding='utf-8'); print('STOP: '+repr(e),flush=True); raise
