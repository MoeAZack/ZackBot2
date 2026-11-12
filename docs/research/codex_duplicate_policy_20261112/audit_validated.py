from pathlib import Path
import json,hashlib,pandas as pd
R=Path('F:/ZackBot2_data/DUP-COIN-SIDE-INDEPENDENT-20261112');sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();count=0
for folder,driver in [('runs-validated','run_overlays_hashed.py'),('runs-path-validated','run_overlays_path_hashed.py')]:
 for p in (R/folder).glob('*/summary.json'):
  s=json.loads(p.read_text());assert s.get('run_fingerprint') and s['code_sha256']['driver']==sha(R/driver) and s['code_sha256']['overlay']==sha(R/'overlay/overlay_sim.py') and s['code_sha256']['preregistration']==sha(R/'preregistration.json');count+=1
assert count==328
(R/'validated_run_audit.json').write_text(json.dumps({'pass':True,'case_count':count,'fresh_hash_pinned_runs':True,'cost_units':'bp per executed side/fill, NOT roundtrip','tape_model':'fixed standalone preselected trades; not full policy-dependent event simulation','generator_provenance':'tape_provenance_validation.json'},indent=2));print('VALIDATED FRESH CASES',count)
# Expand all calendar years (no reset), preserving years with no closes as zero.
expanded=[]
for folder in ['runs-validated','runs-path-validated']:
 for p in (R/folder).glob('*/summary.json'):
  s=json.loads(p.read_text());start=2021 if s['book'].endswith('LONG') else 2024;eq=4000.
  for year in range(start,2027):
   z=s['yearly'].get(str(year),{'pnl':0});pnl=z['pnl'];expanded.append({'intrabar':'worst' if folder=='runs-validated' else 'path','case':s['case'],'book':s['book'],'bps':s['bps'],'scenario':s['scenario'],'policy':s['policy_label'],'year':year,'start_closed_equity':eq,'pnl':pnl,'return_pct':100*pnl/eq if eq>0 else None,'partial_year':year in [start,2026]});eq+=pnl
pd.DataFrame(expanded).to_csv(R/'yearly_all_validated.csv',index=False)
