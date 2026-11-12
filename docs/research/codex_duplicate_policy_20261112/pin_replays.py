from pathlib import Path
import json,hashlib
R=Path('F:/ZackBot2_data/DUP-COIN-SIDE-INDEPENDENT-20261112');sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();validated=[]
for kind,script in [('tapes','generate_tapes.py'),('tapes-path','generate_tapes_path_sensitivity.py')]:
 for folder in (R/kind).iterdir():
  if not folder.is_dir():continue
  m=json.loads((folder/'generation_manifest.json').read_text());assert m['status']=='COMPLETE' and m['script_sha256']==sha(R/script)
  results=json.loads((folder/'generation_results.json').read_text())
  for x in results:
   p=folder/f"{x['strategy']}.{x['bps']}.csv";assert sha(p)==x['csv_sha256'] and x['full_bar_coverage'];validated.append(str(p.relative_to(R)))
pr=json.loads((R/'preregistration.json').read_text())
for receipt in pr['receipts']:assert sha(Path(receipt['path']))==receipt['sha256']
for name,h in pr['source_sha256'].items():assert sha(R/'source'/name)==h
(R/'tape_provenance_validation.json').write_text(json.dumps({'pass':True,'tapes_checked':len(validated),'preregistration_sha256':sha(R/'preregistration.json'),'input_and_model_hashes_match':True,'files':validated},indent=2));print('PROVENANCE PASS',len(validated),'candidate tapes')
for old,new in [('run_overlays.py','run_overlays_hashed.py'),('run_overlays_path_sensitivity.py','run_overlays_path_hashed.py')]:
 s=(R/old).read_text();s=s.replace("ROOT/'runs'","ROOT/'runs-validated'").replace("ROOT/'runs-path'","ROOT/'runs-path-validated'").replace("'results-'+MODE","'results-validated-'+MODE").replace("'results-path-'+MODE","'results-validated-path-'+MODE").replace("'yearly-'+MODE","'yearly-validated-'+MODE").replace("'yearly-path-'+MODE","'yearly-validated-path-'+MODE")
 anchor="summary_rows=[];yearrows=[]"
 replacement="""SHA=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
CODE={'driver':SHA(Path(__file__)),'overlay':SHA(ROOT/'overlay/overlay_sim.py'),'preregistration':SHA(ROOT/'preregistration.json')}
summary_rows=[];yearrows=[]"""
 assert anchor in s;s=s.replace(anchor,replacement)
 anchor="    if (folder/'summary.json').exists():s=json.loads((folder/'summary.json').read_text())"
 tapes='tapes-path' if 'path_' in new else 'tapes'
 replacement="""    tape_hashes={key:SHA(ROOT/'TAPES'/('short15' if key in SC else dataset)/f'{key}.{bps}.csv') for key in keys}
    fingerprint=hashlib.sha256(json.dumps({'code':CODE,'tapes':tape_hashes,'case':case,'keys':keys,'fraction':frac,'maxpos':maxp,'timing':timing,'riskmode':riskmode,'funding':funding,'exclude':exclude},sort_keys=True).encode()).hexdigest()
    if (folder/'summary.json').exists():
     s=json.loads((folder/'summary.json').read_text());assert s.get('run_fingerprint')==fingerprint,'Refusing stale cached result: '+case""".replace('TAPES',tapes)
 assert anchor in s;s=s.replace(anchor,replacement)
 s=s.replace("s['refusal_counts']=", "s['run_fingerprint']=fingerprint;s['input_tape_sha256']=tape_hashes;s['code_sha256']=CODE\n     s['refusal_counts']=")
 (R/new).write_text(s)
