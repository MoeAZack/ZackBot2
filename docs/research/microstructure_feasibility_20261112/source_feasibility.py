import csv, datetime as dt, hashlib, io, json, pathlib, urllib.error, urllib.request, zipfile
P=pathlib.Path('F:/ZackBot2_data/MICROSTRUCTURE-FEASIBILITY-20261112'); P.mkdir(parents=True,exist_ok=True)
base='https://data.binance.vision/data/futures/um/daily/aggTrades'; symbols=['BTCUSDT','ETHUSDT']; date='2026-09-01'; limit=100_000_000
files=[]
for s in symbols:
 n=f'{s}-aggTrades-{date}.zip'; u=f'{base}/{s}/{n}'; files.append({'symbol':s,'url':u,'checksum_url':u+'.CHECKSUM'})
manifest={'purpose':'source data feasibility only; no PnL or strategy signal/parameter counts','source':'USD-M daily aggregate trades','official_reference':'https://github.com/binance/binance-public-data','date_utc':date,'files':files,'download_cap_bytes':limit,'policy':'HEAD both first; stop if sum exceeds cap or length missing; stream downloads with shared byte cap; verify official SHA256; stream parse compressed CSV; no credentials','schema':['agg_trade_id','price','quantity','first_trade_id','last_trade_id','timestamp_ms','buyer_is_maker'],'known_limitation':'aggregated fills are not individual trades or a complete order book; missing data or sequence uncertainty blocks authority'}
mp=P/'manifest.json'
if mp.exists(): raise SystemExit('manifest exists; refuse re-freeze')
mp.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
H=lambda b:hashlib.sha256(b).hexdigest()
def get(url,method='GET'):
 return urllib.request.urlopen(urllib.request.Request(url,method=method,headers={'User-Agent':'ZackBot-public-microstructure-feasibility'}),timeout=30)
res={'manifest_sha256':H(mp.read_bytes()),'files':[],'downloaded_bytes':0}
for e in files:
 r=dict(e); res['files'].append(r)
 try:
  with get(e['url'],'HEAD') as response:
   r['head_status']=response.status; length=response.headers.get('Content-Length'); r['content_length']=int(length) if length is not None else None
 except Exception as exc: r['head_error']=repr(exc)
def save(): (P/'result.json').write_text(json.dumps(res,indent=2)+'\n',encoding='utf8')
save()
if any(r.get('content_length') is None for r in res['files']) or sum(r['content_length'] for r in res['files'])>limit:
 res['status']='BLOCKED_MISSING_LENGTH_OR_OVER_CAP'; save(); print(json.dumps(res,indent=2)); raise SystemExit(0)
for r in res['files']:
 try:
  with get(r['checksum_url']) as response:
   check=response.read(512); res['downloaded_bytes']+=len(check)
  r['checksum_file_sha256']=H(check); r['expected_sha256']=check.decode().split()[0]; name=r['url'].rsplit('/',1)[-1]; path=P/name; digest=hashlib.sha256()
  with get(r['url']) as source, path.open('xb') as target:
   while True:
    chunk=source.read(1024*1024)
    if not chunk: break
    res['downloaded_bytes']+=len(chunk)
    if res['downloaded_bytes']>limit: raise ValueError('download cap exceeded, incomplete')
    digest.update(chunk); target.write(chunk)
  r['archive_sha256']=digest.hexdigest(); r['checksum_ok']=r['archive_sha256']==r['expected_sha256']
  if not r['checksum_ok']: raise ValueError('checksum mismatch')
  day0=int(dt.datetime(2026,9,1,tzinfo=dt.timezone.utc).timestamp()*1000); day1=day0+86400000
  with zipfile.ZipFile(path) as z:
   r['members']=z.namelist()
   if len(r['members'])!=1: raise ValueError('unexpected members')
   stats={'rows':0,'invalid':0,'headers':0,'agg_id_gaps':0,'agg_id_duplicates_or_reverse':0,'raw_id_gap_events':0,'raw_id_overlap_events':0,'time_reverse':0,'outside_day':0,'maker_true':0,'maker_false':0,'max_event_gap_ms':0}; prev_id=prev_last=prev_t=None
   with z.open(r['members'][0]) as f:
    for row in csv.reader(io.TextIOWrapper(f,encoding='utf8')):
     if row and row[0].lower() in ('agg_trade_id','aggregate_trade_id','a'): stats['headers']+=1; continue
     try:
      if len(row)!=7: raise ValueError('columns')
      aid=int(row[0]); price=float(row[1]); qty=float(row[2]); first=int(row[3]); last=int(row[4]); t=int(row[5]); flag=row[6].strip().lower()
      if not(price>0 and qty>0 and first<=last and flag in ('true','false')): raise ValueError('fields')
      stats['rows']+=1; stats['maker_'+flag]+=1
      if not day0<=t<day1: stats['outside_day']+=1
      if prev_id is not None:
       if aid<=prev_id: stats['agg_id_duplicates_or_reverse']+=1
       elif aid!=prev_id+1: stats['agg_id_gaps']+=1
       if first>prev_last+1: stats['raw_id_gap_events']+=1
       elif first<=prev_last: stats['raw_id_overlap_events']+=1
       if t<prev_t: stats['time_reverse']+=1
       stats['max_event_gap_ms']=max(stats['max_event_gap_ms'],t-prev_t)
      else: stats.update(first_event_ms=t,first_agg_id=aid,first_trade_id=first)
      prev_id,prev_last,prev_t=aid,last,t
     except Exception: stats['invalid']+=1
   stats.update(last_event_ms=prev_t,last_agg_id=prev_id,last_trade_id=prev_last); r['parse']=stats
 except Exception as exc: r['error']=repr(exc)
 save()
res['status']='COMPLETE_WITH_REPORTED_LIMITATIONS' if all(r.get('checksum_ok') and 'parse' in r for r in res['files']) else 'INCOMPLETE'
save(); print(json.dumps(res,indent=2))
