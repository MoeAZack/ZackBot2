"""Manually launched research conveyor for already frozen V97 then V99.

No scheduler/bot/account access. Stop on any screen pass for independent owner-
contract review, or the Cairo cutoff. Root remains the sole integration owner;
do not run other Git index writes while this foreground research driver runs.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
CACHE=Path('C:/Dev/ZackBot_research_cache')
DEADLINE=datetime(2026,11,23,16,tzinfo=ZoneInfo('Africa/Cairo'))
FRESH=str(CACHE/'longscout_fresh_20261114')
EXTRA=str(CACHE/'h5_wide_d01_20261112/clean')


def now():return datetime.now(ZoneInfo('Africa/Cairo'))


def command(args,env=None):
    subprocess.run(args,cwd=ROOT,env=env,check=True)


def complete(version,out):
    cases=json.loads((HERE/f'longscout_v{version}_cases.json').read_text())
    for key in cases['cases']:
        path=out/key/f'{key}_result.json'
        if not path.exists():return False
        try:r=json.loads(path.read_text())
        except (ValueError,OSError):return False
        if 'wide165' not in r or r.get('orders')!=40:return False
    return True


def finish(version,out):
    ledger=json.loads((HERE/'longscout_resume_ledger.json').read_text())
    if ledger['versions'][str(version)]['status']=='PREREGISTERED':
        command([sys.executable,str(HERE/'longscout_finish_version.py'),str(version),str(out),str(out/'tests.log'),'--defer-network'])
    # One-shot supported publisher reconciles a lost response before posting;
    # network failure cannot idle an independent, already frozen research slice.
    try:
        sys.path.insert(0,str(HERE))
        import longscout_publish_pending as P
        P.STATE.mkdir(exist_ok=True)
        result=P.cycle()
        if result:print(json.dumps(dict(published=result)),flush=True)
    except (RuntimeError,subprocess.TimeoutExpired,OSError,ValueError,AssertionError,KeyError):
        print('Publication queued; network unavailable',flush=True)
    ledger=json.loads((HERE/'longscout_resume_ledger.json').read_text())
    return ledger['versions'][str(version)]['batch_passes']>0


def record_running(version,out):
    path=ROOT/'research/longscout_control_state.json'
    state=json.loads(path.read_text())
    state['running']=dict(version=version,output=str(out),driver='longscout_continue_queue.py',start_cairo=now().isoformat())
    path.write_text(json.dumps(state,indent=1)+'\n',encoding='utf-8',newline='\n')


def main():
    v97=CACHE/'longscout_codex_v97_20261120'
    record_running(97,v97)
    while not complete(97,v97):
        if now()>=DEADLINE:raise RuntimeError('Cutoff reached while awaiting existing V97; root must retain partial evidence')
        time.sleep(5)
    if finish(97,v97):
        print('SCREEN PASS V97: STOPPED FOR INDEPENDENT FORWARD-WORTHINESS REVIEW',flush=True);return
    if now()>=DEADLINE:print('Cairo cutoff reached',flush=True);return
    version=99;source='230bb04b958b49ef60bcc24f5b9a284a68072cf9'
    env=dict(os.environ,LONGSCOUT_PREREG_OFFLINE='1',PYTHONIOENCODING='cp1252',LONGSCOUT_CASES='longscout_v99_cases.json')
    ledger=json.loads((HERE/'longscout_resume_ledger.json').read_text())
    if '99' not in ledger['versions']:
        command([sys.executable,str(HERE/'longscout_register_version.py'),'99',source],env=env)
        command(['git','add','research/extra31_screen/longscout_v99_cases.json','research/extra31_screen/longscout_resume_ledger.json','research/longscout_control_state.json'])
        command(['git','-c','user.name=Codex','-c','user.email=codex@users.noreply.github.com','commit','-m','research: preregister V99 peer and liquidity-clock trials before outcomes'])
    out=CACHE/'longscout_codex_v99_20261120'
    out.mkdir(exist_ok=False)
    # Windows own-output junction, never a link into another job's writable data.
    subprocess.run(['powershell','-NoProfile','-Command',
        "New-Item -ItemType Junction -Path 'C:\\Dev\\ZackBot_research_cache\\longscout_codex_v99_20261120\\_data' -Target 'C:\\Dev\\ZackBot_research_cache\\longscout_codex_v91_20261120\\_data' | Out-Null"],check=True)
    with (out/'tests.log').open('wb') as log:
        subprocess.run([sys.executable,'-m','pytest','tests/test_longscout_v99.py','-q','--disable-warnings'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    record_running(99,out)
    cases=json.loads((HERE/'longscout_v99_cases.json').read_text())
    processes=[];logs=[]
    for key in cases['cases']:
        log=(out/f'{key}.log').open('wb');logs.append(log)
        args=[sys.executable,str(HERE/'longscout_flow_run.py'),FRESH,EXTRA,str(out),'--case',key,'--orders','40']
        processes.append(subprocess.Popen(args,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT))
    try:
        while any(p.poll() is None for p in processes):
            if now()>=DEADLINE:
                for p in processes:
                    if p.poll() is None:subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True)
                raise RuntimeError('Cairo cutoff: only own research subprocess trees terminated; retain partial trials')
            time.sleep(5)
        if any(p.returncode for p in processes):raise RuntimeError('A V99 research process failed; inspect own logs before any retry')
    finally:
        for log in logs:log.close()
    passed=finish(99,out)
    print('SCREEN PASS V99: STOPPED FOR INDEPENDENT FORWARD-WORTHINESS REVIEW' if passed else 'V97 AND V99 COMPLETE; NO PASS; ROOT ADVANCES NEXT FROZEN SLICE',flush=True)


if __name__=='__main__':main()
