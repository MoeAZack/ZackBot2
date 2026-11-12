"""Validate frozen SPOTFLOW archive data; no signals or trade calculations."""
import csv, datetime as dt, hashlib, io, json, pathlib, zipfile
from decimal import Decimal
P=pathlib.Path('F:/ZackBot2_data/SPOTFLOW-D01-20261112')
H=lambda b:hashlib.sha256(b).hexdigest()
manifest=json.loads((P/'data_manifest_frozen.json').read_text()); downloads=json.loads((P/'download_progress.json').read_text())
if downloads['finished']!=225 or downloads['stop'] or any(x['status']!='OK' for x in downloads['results']): raise SystemExit('BLOCKED: incomplete or failed download')
res={'manifest_sha256':H((P/'data_manifest_frozen.json').read_bytes()),'download_sha256':H((P/'download_progress.json').read_bytes()),'archives':[],'joins':{}}
col=['t','o','h','l','c','v','qv','trades','tbqv','tbv']
start=dt.datetime(2024,10,11,17); end=dt.datetime(2026,9,27,23)
for symbol in manifest['symbols']:
 times=set(); data={}
 for item in [x for x in manifest['entries'] if x['symbol']==symbol]:
  month=item['month']; name=item['url'].rsplit('/',1)[-1]; b=(P/name).read_bytes(); checksum=(P/(name+'.CHECKSUM')).read_text().split()[0]
  r={'symbol':symbol,'month':month,'sha256':H(b),'checksum_ok':H(b)==checksum,'rows':0,'errors':0,'missing_hours':0}; res['archives'].append(r)
  if not r['checksum_ok']: raise ValueError('checksum '+name)
  y,m=map(int,month.split('-')); begin=dt.datetime(y,m,1); finish=dt.datetime(y+int(m==12),1 if m==12 else m+1,1); factor=1 if y<2025 else 1000
  expected={int((begin+dt.timedelta(hours=i)).replace(tzinfo=dt.timezone.utc).timestamp()*1000) for i in range(int((finish-begin).total_seconds()/3600))}; observed=set()
  z=zipfile.ZipFile(io.BytesIO(b)); members=z.namelist()
  if len(members)!=1: raise ValueError('archive members')
  for row in csv.reader(io.TextIOWrapper(z.open(members[0]),encoding='utf-8')):
   r['rows']+=1
   try:
    if len(row)!=12: raise ValueError('columns')
    raw,close=int(row[0]),int(row[6]); t=raw//factor
    if raw%factor or close%factor!=factor-1 or close//factor!=t+3599999 or t not in expected or t in observed or t in times: raise ValueError('time unit, bounds or duplicate')
    o,h,l,c,v,q,tb,tq=map(Decimal,(row[1],row[2],row[3],row[4],row[5],row[7],row[9],row[10])); trades=int(row[8])
    if not(0<l<=min(o,c)<=max(o,c)<=h and 0<=tb<=v and 0<=tq<=q and trades>=0): raise ValueError('invalid market fields')
    observed.add(t); times.add(t)
    moment=dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).replace(tzinfo=None)
    if start<=moment<=end: data[moment]=(q,tq)
   except Exception: r['errors']+=1
  r['missing_hours']=len(expected-observed)
  if r['errors'] or r['missing_hours']: raise ValueError('invalid archive '+name)
 join={}; res['joins'][symbol]=join; native_prices={}; canonical_prices={}
 for label,path in [('native',f'F:/ZackBot2_data/C1H-NATIVE-DATA-20261112/inputs/cohort9_1h/{symbol}.csv'),('canonical',f'C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean/{symbol}_1h.csv')]:
  p=pathlib.Path(path); native_times=set()
  with p.open(newline='') as f:
   for row in csv.DictReader(f):
    t=dt.datetime.fromisoformat(row['t']) if label=='native' else dt.datetime.fromtimestamp(int(row['t'])/1000,dt.timezone.utc).replace(tzinfo=None)
    if start<=t<=end:
     if t in native_times: raise ValueError('duplicate futures timestamp')
     native_times.add(t)
     (native_prices if label=='native' else canonical_prices)[t]=tuple(Decimal(row[k]) for k in ('o','h','l','c','qv'))
  join[label]={'path':path,'sha256':H(p.read_bytes()),'rows_in_window':len(native_times),'spot_matched':len(native_times&data.keys()),'futures_without_spot':len(native_times-data.keys()),'spot_without_futures':len(data.keys()-native_times)}
 common=native_prices.keys()&canonical_prices.keys()
 join['native_canonical_field_differences']={k:sum(native_prices[t][i]!=canonical_prices[t][i] for t in common) for i,k in enumerate(('o','h','l','c','qv'))}
(P/'data_validation.json').write_text(json.dumps(res,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'validated_archives':len(res['archives']),'errors':sum(x['errors'] for x in res['archives']),'missing_hours':sum(x['missing_hours'] for x in res['archives']),'joins':res['joins']}))
