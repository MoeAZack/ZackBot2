import csv, datetime as dt, hashlib, io, json, pathlib, urllib.request, zipfile
from decimal import Decimal

ROOT=pathlib.Path('F:/ZackBot2_data/SPOT-PERP-FEASIBILITY-20261112')
ROOT.mkdir(parents=True,exist_ok=True)
BASE='https://data.binance.vision/data/spot/monthly/klines'
items=[]
for sym in ('BTCUSDT','ETHUSDT'):
 for month in ('2024-12','2025-01'):
  fn=f'{sym}-1h-{month}.zip'; url=f'{BASE}/{sym}/1h/{fn}'
  items.append({'symbol':sym,'month':month,'url':url,'checksum_url':url+'.CHECKSUM','rest_hours_utc':[f'{month}-01T00:00:00Z',f'{month}-16T12:00:00Z',f'{month}-31T23:00:00Z']})
manifest={'scope':'phase0 data only; no feature selection or economics','official_format':'https://github.com/binance/binance-public-data','spot_unit_rule':'2024 milliseconds; 2025 microseconds','archives':items,'native_futures':'F:/ZackBot2_data/C1H-NATIVE-DATA-20261112/inputs/cohort9_1h/{symbol}.csv','canonical_futures':'F:/ZackBot2_data/C1H-20261112/inputs/cohort9_1h/{symbol}.csv','fields':['open_time','open','high','low','close','base_volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore'],'rest_endpoint':'https://data-api.binance.vision/api/v3/klines','rest_policy':'first/middle/last fixed complete hours; unresolved if unavailable or disagreement'}
mp=ROOT/'phase0-manifest.json'
if mp.exists(): raise RuntimeError('refusing to overwrite frozen manifest')
mp.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
def sha(b): return hashlib.sha256(b).hexdigest()
def fetch(url):
 req=urllib.request.Request(url,headers={'User-Agent':'ZackBot-phase0-data-quality/1.0'})
 with urllib.request.urlopen(req,timeout=20) as r: return r.read()
report={'manifest_sha256':sha(mp.read_bytes()),'archives':[]}
for item in items:
 result=dict(item); report['archives'].append(result)
 try:
  b=fetch(item['url']); c=fetch(item['checksum_url']); (ROOT/pathlib.PurePosixPath(item['url']).name).write_bytes(b); (ROOT/(pathlib.PurePosixPath(item['url']).name+'.CHECKSUM')).write_bytes(c)
  result.update(archive_sha256=sha(b),checksum_file_sha256=sha(c),expected_sha256=c.decode().split()[0],checksum_match=sha(b)==c.decode().split()[0])
  if not result['checksum_match']: raise ValueError('checksum mismatch')
  z=zipfile.ZipFile(io.BytesIO(b)); result['members']=z.namelist(); rows=list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]),encoding='utf-8')))
  factor=1000 if item['month']>='2025-01' else 1
  start=int(dt.datetime.fromisoformat(item['month']+'-01').replace(tzinfo=dt.timezone.utc).timestamp()*1000); end=int((dt.datetime(2025,1,1,tzinfo=dt.timezone.utc) if item['month']=='2024-12' else dt.datetime(2025,2,1,tzinfo=dt.timezone.utc)).timestamp()*1000)
  errors=[]; bytime={}
  for n,row in enumerate(rows,1):
   try:
    if len(row)!=12: raise ValueError('field count')
    raw=int(row[0]); close=int(row[6]); t=raw//factor
    if raw%factor or close%factor != factor-1 or t%3600000 or close//factor!=t+3599999 or not start<=t<end: raise ValueError('time/bounds/unit')
    o,h,l,c1,v,q,tb,tq=map(Decimal,(row[1],row[2],row[3],row[4],row[5],row[7],row[9],row[10])); trades=int(row[8])
    if not (0<l<=min(o,c1)<=max(o,c1)<=h and v>=0 and q>=0 and trades>=0 and 0<=tb<=v and 0<=tq<=q): raise ValueError('OHLC/volume/trades/taker')
    if t in bytime: raise ValueError('duplicate')
    bytime[t]=row
   except Exception as e: errors.append({'row':n,'error':str(e)})
  expected=set(range(start,end,3600000)); result.update(rows=len(rows),valid_unique=len(bytime),expected_hours=len(expected),missing_hours=len(expected-set(bytime)),unexpected_hours=len(set(bytime)-expected),errors=errors[:20],error_count=len(errors),first_raw=rows[0][0],last_raw=rows[-1][0])
  for label,template in [('native',manifest['native_futures']),('canonical',manifest['canonical_futures'])]:
   p=pathlib.Path(template.format(symbol=item['symbol'])); times=set()
   with p.open(newline='',encoding='utf-8') as f:
    for row in csv.DictReader(f):
     t=int(dt.datetime.fromisoformat(row['t']).replace(tzinfo=dt.timezone.utc).timestamp()*1000)
     if start<=t<end: times.add(t)
   result[label+'_join']={'path':str(p),'sha256':sha(p.read_bytes()),'futures_unique_hours':len(times),'matched_hours':len(times&set(bytime)),'spot_without_futures':len(set(bytime)-times),'futures_without_spot':len(times-set(bytime))}
  checks=[]
  for s in item['rest_hours_utc']:
   t=int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000); url=f"{manifest['rest_endpoint']}?symbol={item['symbol']}&interval=1h&startTime={t}&endTime={t+3599999}&limit=1"; chk={'hour_utc':s,'url':url}; checks.append(chk)
   try:
    b=fetch(url); chk['response_sha256']=sha(b); data=json.loads(b); chk['rows']=len(data)
    if len(data)!=1 or t not in bytime: chk['status']='UNRESOLVED_MISSING'
    else:
     archived=bytime[t]; live=data[0]; diffs=[i for i in (1,2,3,4,5,7,8,9,10) if Decimal(str(live[i]))!=Decimal(archived[i])]; chk.update(status='MATCH' if not diffs and int(live[0])==t and int(live[6])==t+3599999 else 'UNRESOLVED_MISMATCH',different_indices=diffs)
   except Exception as e: chk.update(status='UNRESOLVED_FETCH',error=str(e))
  result['rest_checks']=checks
 except Exception as e: result['fatal_error']=str(e)
 (ROOT/'phase0-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,indent=2))
