import csv,datetime as dt,hashlib,io,json,pathlib,zipfile,calendar
P=pathlib.Path('F:/ZackBot2_data/SPOTFLOW-D01-20261112'); O=pathlib.Path('F:/ZackBot2_data/SPB-D01-20261113')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def ms(s): return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000)
def iso(t): return dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).isoformat()
m=json.loads((P/'data_manifest_frozen.json').read_text()); v=json.loads((P/'data_validation.json').read_text()); start,end=map(ms,m['window_utc']); issues=[]; results={}; hashes={}
if sha(P/'data_manifest_frozen.json')!=v['manifest_sha256']: issues.append('manifest hash mismatch')
expected={(x['symbol'],x['month']):x for x in v['archives']}
if len(expected)!=225 or len(m['entries'])!=225: issues.append('archive count mismatch')
for sym in m['symbols']:
 times=[]; prev=None; rows=0; units={'ms':0,'us':0}; errors={}
 def err(k,d):
  errors[k]=errors.get(k,0)+1
  if errors[k]<=3: issues.append(f'{sym} {k}: {d}')
 for month in m['months']:
  p=P/f'{sym}-1h-{month}.zip'; digest=sha(p); hashes[p.name]=digest
  if digest!=expected[(sym,month)]['sha256']: err('archive_hash',p.name)
  if digest!=(P/(p.name+'.CHECKSUM')).read_text().split()[0].lower(): err('checksum',p.name)
  y,mo=map(int,month.split('-')); u=1000 if y>=2025 else 1; label='us' if u==1000 else 'ms'; lo=int(dt.datetime(y,mo,1,tzinfo=dt.timezone.utc).timestamp()*1000); hi=lo+calendar.monthrange(y,mo)[1]*24*3600000
  with zipfile.ZipFile(p) as z:
   names=z.namelist()
   if names!=[p.stem+'.csv']: err('zip_schema',str(names))
   with z.open(names[0]) as f:
    for r in csv.reader(io.TextIOWrapper(f,encoding='utf-8-sig',newline='')):
     if len(r)!=12: err('columns',str(len(r))); continue
     try:
      raw=int(r[0]); endraw=int(r[6]); o,h,l,c=map(float,r[1:5]); t=raw//u
      if raw%u or not lo<=t<hi or t%3600000: err('open_timestamp',str(raw))
      if endraw!=raw+3600000*u-1: err('close_endpoint',str(raw))
      if not (0<l<=o<=h and l<=c<=h): err('ohlc',str(raw))
      if prev is not None and t-prev!=3600000: err('spot_gap_or_order',f'{prev},{t}')
      prev=t; rows+=1; units[label]+=1
      if start<=t<=end: times.append(t)
     except ValueError as e: err('parse',str(e))
 cp=pathlib.Path(v['joins'][sym]['canonical']['path']); digest=sha(cp)
 if digest!=v['joins'][sym]['canonical']['sha256']: err('canonical_hash',str(cp))
 ft=[]; prior=None; count=0
 with cp.open(newline='') as f:
  reader=csv.DictReader(f)
  if reader.fieldnames!=['t','o','h','l','c','v','qv']: err('canonical_schema',str(reader.fieldnames))
  for r in reader:
   t=int(r['t']); count+=1
   if t%3600000: err('canonical_unaligned',str(t))
   if prior is not None and t<=prior: err('canonical_order',str(t))
   prior=t
   if start<=t<=end: ft.append(t)
 a,b=set(times),set(ft)
 if len(a)!=len(times) or len(b)!=len(ft) or a!=b: err('window_join',f'{len(a-b)},{len(b-a)}')
 results[sym]={'spot_rows':rows,'spot_units_rows':units,'window_rows':len(times),'first_utc':iso(times[0]),'last_utc':iso(times[-1]),'canonical_rows':count,'canonical_window_rows':len(ft),'matched':len(a&b),'spot_only':len(a-b),'futures_only':len(b-a),'canonical_path':str(cp),'canonical_sha256':digest,'errors':errors}
out={'scope':'input audit only; no signal counts, basis distributions, trades or economic outcomes','manifest_sha256':sha(P/'data_manifest_frozen.json'),'validation_sha256':sha(P/'data_validation.json'),'window_inclusive_utc':m['window_utc'],'spot_column_mapping':m['field_mapping'],'archive_count':len(hashes),'spot_archive_sha256':hashes,'symbols':results,'issues':issues,'limitations':['Hash comparison uses frozen validation and local checksum files; no independent download or point-in-time publication verification.','OHLC validity does not prove simultaneous executions or historical exchange rules.','Original nine-coin survivor universe.'],'status':'PASS' if not issues else 'FAIL'}
(O/'independent_input_audit.json').write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({k:out[k] for k in ('status','issues','manifest_sha256','validation_sha256','symbols')},indent=2))
