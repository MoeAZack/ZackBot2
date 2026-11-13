"""Keep the two research jobs alive; collect read-only overlap metrics. No bot process/API mutations."""
import json,subprocess,time,os,msvcrt
from pathlib import Path
from datetime import datetime,timezone
BASE=Path('F:/ZackBot2_data');ROOT=BASE/'research-job-guard';PY='C:/Users/princ/AppData/Local/Programs/Python/Python312/python.exe'
TASKS=['ZackBot-FWD-01-Research','ZackBot-FWD-01-PROT-PAIR']
QUERY="Get-ScheduledTask -TaskName 'ZackBot-FWD-01-Research','ZackBot-FWD-01-PROT-PAIR' | ForEach-Object {[pscustomobject]@{name=$_.TaskName;state=$_.State.ToString()}} | ConvertTo-Json -Compress"
FLAGS=0x08000000

def ps(text):
 r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',text],capture_output=True,text=True,timeout=30,creationflags=FLAGS)
 if r.returncode:raise RuntimeError('research_task_query_failed')
 return r.stdout.strip()
def write(obj):
 p=ROOT/'health.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2));os.replace(tmp,p)
def main():
 lock=(ROOT/'guard.lock').open('a+b');lock.write(b'1');lock.flush();lock.seek(0)
 try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
 except OSError:return
 collector=None;next_collect=0;last_collector=None;hourly=None
 while True:
  info={'utc':datetime.now(timezone.utc).isoformat(),'pid':os.getpid(),'tasks':[]}
  try:
   rows=json.loads(ps(QUERY))
   for row in rows:
    if row['name'] not in TASKS:raise ValueError('unexpected_task')
    # Respect Disabled; only restart these research tasks, never a trading instance.
    if row['state']=='Ready':
     ps("Start-ScheduledTask -TaskName '"+row['name']+"'");row['action']='started'
     with (ROOT/'restart-events.jsonl').open('a') as event:event.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'task':row['name'],'action':'start_stopped_research_task'})+'\n')
    info['tasks'].append(row)
   if (BASE/'H5-HOURLY-FWD-20261112/RETIRED.json').exists():
    info['h5_hourly_status']='RETIRED'
    info['h5_forward_record']='FWD-BOOKS H5: freeze 8f83494b plus A1'
   else:
    if hourly is None or hourly.poll() is not None:
     hourly=subprocess.Popen([PY,str(BASE/'H5-HOURLY-FWD-20261112/forward.py')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=FLAGS)
    info['h5_hourly_pid']=hourly.pid
   if collector is not None and collector.poll() is not None:
    last_collector={'exit_code':collector.returncode,'finished_utc':datetime.now(timezone.utc).isoformat()};collector=None
   if collector is None and time.monotonic()>=next_collect:
    collector=subprocess.Popen([PY,str(BASE/'same-side-overlap/overlap_collector.py')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=FLAGS);next_collect=time.monotonic()+900
   info['collector']=last_collector;info['collector_running']=collector is not None;info['status']='RUNNING'
  except Exception as exc:info.update(status='ERROR',error=type(exc).__name__)
  write(info);time.sleep(30)
if __name__=='__main__':main()
