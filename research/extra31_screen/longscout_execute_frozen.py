"""One already source-frozen research version; sole Git integration owner.

python longscout_execute_frozen.py VERSION SOURCE_SHA [runner_filename]
Never changes accounts/runtime/schedulers or launches an unfrozen hypothesis.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import longscout_continue_queue as Q


def main():
    version=int(sys.argv[1]);source=sys.argv[2]
    runner=sys.argv[3] if len(sys.argv)>3 else 'longscout_v91_run.py'
    assert runner in ('longscout_v91_run.py','longscout_flow_run.py')
    if Q.now()>=Q.DEADLINE:raise RuntimeError('Cairo cutoff reached')
    env=dict(os.environ,LONGSCOUT_PREREG_OFFLINE='1',PYTHONIOENCODING='cp1252',LONGSCOUT_CASES=f'longscout_v{version}_cases.json')
    Q.command([sys.executable,str(Q.HERE/'longscout_register_version.py'),str(version),source],env=env)
    Q.command(['git','add',f'research/extra31_screen/longscout_v{version}_cases.json','research/extra31_screen/longscout_resume_ledger.json','research/longscout_control_state.json'])
    Q.command(['git','-c','user.name=Codex','-c','user.email=codex@users.noreply.github.com','commit','-m',f'research: preregister V{version} before economic outcomes'])
    # Source is already remotely confirmed; a failed case push stays disclosed.
    try:
        import longscout_publish_pending as P
        P.STATE.mkdir(exist_ok=True);published=P.cycle()
        if published:print(json.dumps(dict(published=published)),flush=True)
    except (RuntimeError,subprocess.TimeoutExpired,OSError,ValueError,AssertionError,KeyError):print('Preregistration local; immutable source previously published',flush=True)
    out=Q.CACHE/f'longscout_codex_v{version}_20261120'
    out.mkdir(exist_ok=False)
    script=f"New-Item -ItemType Junction -Path '{out / '_data'}' -Target 'C:/Dev/ZackBot_research_cache/longscout_codex_v91_20261120/_data' | Out-Null"
    subprocess.run(['powershell','-NoProfile','-Command',script],check=True)
    with (out/'tests.log').open('wb') as log:
        subprocess.run([sys.executable,'-m','pytest',f'tests/test_longscout_v{version}.py','-q','--disable-warnings'],cwd=Q.ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    Q.record_running(version,out)
    cases=json.loads((Q.HERE/f'longscout_v{version}_cases.json').read_text())
    procs=[];logs=[]
    for key in cases['cases']:
        log=(out/f'{key}.log').open('wb');logs.append(log)
        procs.append(subprocess.Popen([sys.executable,str(Q.HERE/runner),Q.FRESH,Q.EXTRA,str(out),'--case',key,'--orders','40'],cwd=Q.ROOT,env=env,stdout=log,stderr=subprocess.STDOUT))
    try:
        while any(p.poll() is None for p in procs):
            if Q.now()>=Q.DEADLINE:
                for p in procs:
                    if p.poll() is None:subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True)
                raise RuntimeError('Cairo cutoff: own research processes stopped; preserve partial ledger evidence')
            time.sleep(5)
        if any(p.returncode for p in procs):raise RuntimeError('Research worker failed; root must inspect, never silently rerun')
    finally:
        for log in logs:log.close()
    passed=Q.finish(version,out)
    print(f'V{version}: '+('SCREEN PASS; ROOT MUST REVIEW FORWARD-WORTHINESS' if passed else 'REJECTED; ADVANCE NEXT UNTOUCHED FAMILY'),flush=True)


if __name__=='__main__':main()
