import json,time,sys,subprocess
from pathlib import Path
R=Path(__file__).resolve().parent
p=Path('F:/ZackBot2_data/C1H-XASSET-20261112/queue-state.json')
while True:
 try:d=json.loads(p.read_text())
 except (FileNotFoundError,json.JSONDecodeError):time.sleep(15);continue
 if d['status']=='FINISHED':break
 if d['status'] in ['RESEARCH_ERROR','BLOCKED_RESEARCH_ERRORS']:raise SystemExit('Prior batch failed; no follow-on started')
 (R/'queue-state.json').write_text(json.dumps({'status':'QUEUED_AFTER_CROSSASSET'}));time.sleep(15)
with (R/'matrix.stdout.log').open('w') as o,(R/'matrix.stderr.log').open('w') as e:q=subprocess.run([sys.executable,str(R/'matrix.py')],stdout=o,stderr=e)
(R/'queue-state.json').write_text(json.dumps({'status':'FINISHED' if q.returncode==0 else 'RESEARCH_ERROR','exit_code':q.returncode}))
