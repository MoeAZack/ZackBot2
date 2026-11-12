"""Queue research-only next batch behind current fixed two-worker scan."""
import json,time,subprocess,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
R=Path(__file__).resolve().parent;gate=Path('F:/ZackBot2_data/C1H-EXIT-GRID-20261112/progress.json')
def save(status,**extra):
 p=R/'queue-state.json';t=p.with_suffix('.tmp');t.write_text(json.dumps(dict(status=status,at_cairo=datetime.now(ZoneInfo('Africa/Cairo')).isoformat(),**extra),indent=2));t.replace(p)
while True:
 try:d=json.loads(gate.read_text())
 except (FileNotFoundError,json.JSONDecodeError):time.sleep(15);continue
 if d['status']=='COMPLETE':
  assert d['done']==d['total']==1134 and not d['failed'];break
 if d['status']=='COMPLETED_WITH_ERRORS':save('BLOCKED_RESEARCH_ERRORS',grid=d);raise SystemExit(1)
 save('QUEUED_BEHIND_GRID',grid_done=d['done'],grid_total=d['total']);time.sleep(15)
save('STARTING_CROSS_ASSET')
with (R/'matrix.stdout.log').open('w') as o,(R/'matrix.stderr.log').open('w') as e:p=subprocess.run([sys.executable,str(R/'matrix.py')],stdout=o,stderr=e)
save('FINISHED' if p.returncode==0 else 'RESEARCH_ERROR',exit_code=p.returncode)