import csv,json,hashlib,time,shutil
from pathlib import Path
from decimal import Decimal
R=Path('F:/ZackBot2_data/H5-WIDE-DATA-20261112'); S=Path('C:/Dev/h5wide_d01'); T=Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112'); O=json.loads((S/'manifest.json').read_text()); sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
while True:
 try: state=json.loads((R/'status.json').read_text())
 except (OSError,ValueError): time.sleep(1);continue
 if state['phase']=='finished':break
 time.sleep(3)
assert state['state']=='complete' and len(state['symbols'])==40
(T/'clean').mkdir(parents=True,exist_ok=True);(T/'rest_responses').mkdir(exist_ok=True); result={}
for sym,info in O['symbols'].items():
 s=state['symbols'][sym]; p=S/info['clean_csv']; assert sha(p)==info['clean_csv_sha256']; changes=json.loads((R/'rest_responses'/(sym+'-changes.json')).read_text()); changes={int(x['rest'][0]):x for x in changes}; assert len(changes)==3
 output=T/'clean'/(sym+'_1h.csv'); changed=[]; seen=[]
 with p.open(newline='',encoding='utf-8') as src,output.open('w',newline='',encoding='utf-8') as dst:
  rd=csv.DictReader(src); fields=['t','o','h','l','c','v','qv']; assert rd.fieldnames==fields; w=csv.DictWriter(dst,fieldnames=fields); w.writeheader()
  for row in rd:
   t=int(row['t'])
   if t in changes:
    before=row.copy(); r=changes[t]['rest']; row.update(dict(zip(fields,[str(t),r[1],r[2],r[3],r[4],r[5],r[7]]))); seen.append(t)
    if any(Decimal(before[k])!=Decimal(row[k]) for k in fields[1:]):changed.append({'t':t,'before':before,'after':row.copy()})
   w.writerow(row)
 assert len(seen)==3 and sorted(seen)==sorted(changes)
 for rec in s['reconciliation_requests']:
  pth=Path(rec['file']); assert sha(pth)==rec['sha256']; shutil.copy2(pth,T/'rest_responses'/pth.name)
 cp=R/'rest_responses'/(sym+'-changes.json'); shutil.copy2(cp,T/'rest_responses'/cp.name)
 result[sym]={'source_csv_sha256':info['clean_csv_sha256'],'csv_sha256':sha(output),'file':str(output),'first_utc':info['first_open_utc'],'last_utc':info['last_open_utc'],'rows':info['bar_count'],'reconciled_times_ms':sorted(seen),'changed':changed,'requests':s['reconciliation_requests'],'changes_sha256':sha(cp)}
manifest={'id':'H5-WIDE-D01','source_manifest_sha256':sha(S/'manifest.json'),'prereg_sha256':'622635c784e3fc9e37401623dad9f0c5dadeb3b2386d5a305b7cbd6416ba5174','authority':'Original frozen dataset with objective REST replacement of placeholder and immediate neighbors for each symbol. No July warmup added; same per-symbol starts and row counts. Binance checksum-verified daily archives for October supplement monthly archives.','symbols':result}
(T/'input_manifest.json').write_text(json.dumps(manifest,indent=2)); print('COMPLETE',len(result),'files changed',sum(bool(x['changed']) for x in result.values()),'bars changed',sum(len(x['changed']) for x in result.values()),'manifest',sha(T/'input_manifest.json'),flush=True)
