import test_books as X
import json, hashlib
from pathlib import Path
F=X.FB; raw1,raw4=X.data(); T=X.T
with X.Synth([T-X.H1],[T-2*X.H1]):
 row,rs=X.ev(raw1,raw4,T+12*X.H1)
out={'pre_T_signal_only':{n:{'diagnostic_signals':row['books'][n]['signals']['BTCUSDT'],'admissions':[{'symbol':r['sym'],'entry':r['t_in']} for r in rs[n+'_15_path_alpha']]} for n in ('H5','EMA')}}
rev={s:d.iloc[::-1].copy() for s,d in raw1.items()}
dup={s:X.pd.concat([d,d.iloc[-1:]]) for s,d in raw1.items()}
out['reversed_coverage']=F.coverage(rev,X.W0,T,T+12*X.H1,X.H1,T)[0]
out['duplicate_coverage']=F.coverage(dup,X.W0,T,T+12*X.H1,X.H1,T)[0]
p=Path('F:/ZackBot2_data/H5-HOURLY-FWD-20261112'); c=json.loads((p/'cursor.json').read_text())
out['h5_files']={f.name:{'cursor_hash_matches':hashlib.sha256(f.read_bytes()).hexdigest()==c['data_sha256'].get(f.name),'last_open':str(X.pd.to_datetime(json.loads(f.read_text())[-1][0],unit='ms'))} for f in (p/'data').glob('*.json')}
out['h5_heartbeat']=json.loads((p/'heartbeat.json').read_text());out['h5_previous_summary_hash_matches']=hashlib.sha256((p/'rows'/str(c['previous_boundary'])/'summary.json').read_bytes()).hexdigest()==c['summary_sha256']
print(json.dumps(out,default=str,indent=2));Path(__file__).with_suffix('.json').write_text(json.dumps(out,default=str,indent=2))
