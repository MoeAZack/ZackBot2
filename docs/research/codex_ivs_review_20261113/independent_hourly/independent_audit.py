import os,json,csv,hashlib,urllib.request,urllib.error,zipfile,io,time,datetime,collections,concurrent.futures,shutil,decimal
R=r'F:\ZackBot2_data\IVS-REVIEW-20261113'; I=os.path.join(R,'docs','research','ivs1h','inputs'); C=os.path.join(R,'rawcache'); O=os.path.join(R,'independent_hourly')
def get(u):
 for i in range(3):
  try:
   with urllib.request.urlopen(u,timeout=35) as x:return x.read()
  except urllib.error.HTTPError as e:
   if e.code in (403,418,429) or i==2:raise
   time.sleep(i+1)
  except (urllib.error.URLError,TimeoutError,OSError):
   if i==2:raise
   time.sleep(i+1)
def work(arg):
 day,m,f=arg; check=get(m['checksum_url']).decode('ascii').split(); name=os.path.basename(m['zip_url']); assert len(check)==2 and check[1]==name
 path=os.path.join(C,name)
 if os.path.exists(path):
  with open(path,'rb') as z:data=z.read()
 else:
  data=get(m['zip_url'])
  if hashlib.sha256(data).hexdigest()!=check[0]:raise ValueError('STOP checksum mismatch '+day)
  with open(path+'.part','wb') as z:z.write(data)
  os.replace(path+'.part',path)
 digest=hashlib.sha256(data).hexdigest()
 if digest!=check[0]:raise ValueError('STOP checksum mismatch '+day)
 assert digest==f['sha256'] and len(data)==f['bytes']==m['zip_listed_bytes'] and name==f['file']
 start=int(datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.timezone.utc).timestamp()*1000); a=[{'n':0,'distinct':set(),'run':0,'maxrun':0,'last':None,'time':None} for _ in range(24)]; prev=-1; count=0
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  assert z.namelist()==[name[:-4]+'.csv']
  with z.open(z.namelist()[0]) as raw:
   rd=csv.reader(io.TextIOWrapper(raw,encoding='utf-8',newline='')); assert next(rd)==['calc_time','symbol','base_asset','quote_asset','index_value']
   for row in rd:
    assert len(row)==5
    t,s,b,q,v=row; assert (s,b,q)==('BTCBVOLUSDT','BTCBVOL','USDT') and len(t)==13 and t.isdigit()
    t=int(t); assert start<=t<start+86400000 and t>prev; prev=t; val=decimal.Decimal(v); assert val.is_finite() and val>0
    x=a[(t-start)//3600000]; x['n']+=1; x['distinct'].add(val); x['run']=x['run']+1 if x['last']==val else 1; x['maxrun']=max(x['maxrun'],x['run']); x['last']=val; x['str']=v; x['time']=t*1000; count+=1
 result=[]
 for h,x in enumerate(a):
  t=(start+h*3600000)*1000; reasons=[]
  if x['n']<3000:reasons.append('count_lt_3000')
  if x['time'] is None or x['time']<t+3540000000:reasons.append('stale_last')
  result.append({'hour_open_utc':(datetime.datetime.fromtimestamp(start/1000,datetime.timezone.utc)+datetime.timedelta(hours=h)).strftime('%Y-%m-%d %H:%M'),'hour_open_us':str(t),'value':x.get('str','') if not reasons else '', 'last_calc_time_us':str(x['time']) if x['time'] is not None else '', 'unique_valid_count':str(x['n']),'rows':str(x['n']),'n_distinct_values':str(len(x['distinct'])),'max_const_run':str(x['maxrun']),'valid':'0' if reasons else '1','reason':'|'.join(reasons) if reasons else 'ok','source_file':name,'source_sha256':digest})
 return day,result,count,digest
def main():
 e=json.load(open(os.path.join(I,'expected_manifest.json'))); f=json.load(open(os.path.join(I,'fetch_manifest.json'))); a=json.load(open(os.path.join(I,'audit.json')))
 with open(os.path.join(I,'btcbvol_hourly.csv'),newline='') as z:table=list(csv.DictReader(z))
 report={'verified_files':0,'row_total':0,'mismatches':[],'file_failures':[],'input_sha256':{},'verified_dates':[]}; reason=collections.Counter(); hours={}
 for n in ['expected_manifest.json','fetch_manifest.json','audit.json','btcbvol_hourly.csv']:
  with open(os.path.join(I,n),'rb') as z:report['input_sha256'][n]=hashlib.sha256(z.read()).hexdigest()
 assert set(e['files'])==set(f['files'])==set(a['files']) and len(e['files'])==703 and sum(x['zip_listed_bytes'] for x in e['files'].values())<=512*1024**2 and shutil.disk_usage(C).free>=2*1024**3
 assert sum(os.path.getsize(os.path.join(C,n)) for n in os.listdir(C) if n.endswith(('.zip','.part')))<=512*1024**2
 dates=[]; d=datetime.date.fromisoformat(e['calendar_first']); end=datetime.date.fromisoformat(e['calendar_last'])
 while d<=end:dates.append(d.isoformat());d+=datetime.timedelta(days=1)
 assert len(dates)==724 and len(table)==17376 and set(dates)-set(e['files'])==set(e['missing_dates'])==set(f['missing_dates'])==set(a['missing_dates'])
 table={r['hour_open_utc']:r for r in table}; assert len(table)==17376
 items=iter(sorted(e['files'].items())); futures={}
 def queue(pool):
  try:day,m=next(items)
  except StopIteration:return
  futures[pool.submit(work,(day,m,f['files'][day]))]=day
 try:
  with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
   for _ in range(4):queue(pool)
   while futures:
    done,_=concurrent.futures.wait(futures,return_when=concurrent.futures.FIRST_COMPLETED)
    for future in done:
     day=futures.pop(future)
     try:day,rows,n,digest=future.result()
     except Exception as ex:report['file_failures'].append({'date':day,'error':repr(ex)});raise
     if a['files'][day]['sha256']!=digest or a['files'][day]['rows']!=n:report['mismatches'].append({'date':day,'audit_file_mismatch':True})
     for row in rows:
      hour=row['hour_open_utc']; obs=table.get(hour); diff={k:[v,obs.get(k) if obs else None] for k,v in row.items() if not obs or obs.get(k)!=v}
      if diff:report['mismatches'].append({'hour':hour,'fields':diff})
      hours[hour]=row; reason.update(row['reason'].split('|'))
     report['verified_files']+=1; report['row_total']+=n; report['verified_dates'].append(day)
     if report['verified_files']%100==0:print('verified',report['verified_files'],'rows',report['row_total'],'mismatches',len(report['mismatches']),flush=True)
     queue(pool)
 except Exception as ex:report['fatal']=repr(ex)
 for day in e['missing_dates']:
  for h in range(24):
   hour=day+' '+str(h).zfill(2)+':00'; t=int(datetime.datetime.strptime(hour,'%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone.utc).timestamp()*1000000)
   row={'hour_open_utc':hour,'hour_open_us':str(t),'value':'','last_calc_time_us':'','unique_valid_count':'0','rows':'0','n_distinct_values':'0','max_const_run':'0','valid':'0','reason':'missing_day','source_file':'','source_sha256':''}
   if table.get(hour)!=row:report['mismatches'].append({'hour':hour,'missing_day_mismatch':True})
   hours[hour]=row;reason.update(['missing_day'])
 report['reason_counts']=dict(reason);report['reconstructed_hours']=len(hours);report['complete']=report['verified_files']==703 and not report['file_failures'] and len(hours)==17376
 report['audit_reconciliation']={'rows_match':report['row_total']==a['rows_total'],'reasons_match':dict(reason)==a['reason_counts'],'hours_match':len(hours)==a['hours_total'],'valid_match':reason['ok']==a['hours_valid']}
 report['status']='PASS' if report['complete'] and not report['mismatches'] and all(report['audit_reconciliation'].values()) else 'PARTIAL_OR_FAIL'
 with open(os.path.join(O,'independent_report.json'),'w') as z:json.dump(report,z,indent=2,sort_keys=True)
 print(json.dumps({k:v for k,v in report.items() if k not in ('verified_dates','mismatches')},indent=2));print('mismatches',len(report['mismatches']),flush=True)
if __name__=='__main__':main()
