import json,csv,datetime,os,collections
R=r'F:\ZackBot2_data\IVS-REVIEW-20261113'; p=os.path.join(R,'docs','research','ivs1h','inputs'); rp=os.path.join(R,'independent_hourly','independent_report.json'); a=json.load(open(os.path.join(p,'audit.json')));e=json.load(open(os.path.join(p,'expected_manifest.json')));f=json.load(open(os.path.join(p,'fetch_manifest.json')));report=json.load(open(rp))
with open(os.path.join(p,'btcbvol_hourly.csv'),newline='') as z:rows=list(csv.DictReader(z))
start=datetime.datetime.fromisoformat(e['calendar_first']); expected=[(start+datetime.timedelta(hours=i)).strftime('%Y-%m-%d %H:%M') for i in range(724*24)];byday=collections.defaultdict(list)
for row in rows:
 if row['source_file']:byday[row['hour_open_utc'][:10]].append(row)
checks={'hourly_chronological_complete':[r['hour_open_utc'] for r in rows]==expected,'file_bad_and_units_match_validated_raw':all(all(v==0 for v in x['bad'].values()) and x['hours_outside_day']==0 and x['units']==['ms'] for x in a['files'].values()),'audit_zero_bad_totals':all(v==0 for v in a['bad_rows_total'].values()),'audit_units':a['time_units']=={'ms':703},'manifest_byte_total':sum(x['bytes'] for x in f['files'].values())==f['total_bytes']==e['listed_total_zip_bytes'],'cache_bytes_within_cap':sum(os.path.getsize(os.path.join(R,'rawcache',n)) for n in os.listdir(os.path.join(R,'rawcache')) if n.endswith(('.zip','.part')))<=512*1024**2,'file_hours_present_and_last_match':all(x['hours_present']==sum(int(r['rows'])>0 for r in byday[d]) and x['last_us']==max(int(r['last_calc_time_us']) for r in byday[d] if r['last_calc_time_us']) for d,x in a['files'].items())}
report['supplemental_checks']=checks;report['status']='PASS' if report['complete'] and not report['mismatches'] and all(report['audit_reconciliation'].values()) and all(checks.values()) else 'PARTIAL_OR_FAIL'
with open(rp,'w') as z:json.dump(report,z,indent=2,sort_keys=True)
print(checks,report['status'])
