"""Register an already source-frozen version, before any market outcomes.

Usage: python longscout_register_version.py <version> <source_commit>
No market data, runtime or network access. Caller commits and pushes outputs.
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'research')]


def main():
    version,commit = int(sys.argv[1]),sys.argv[2]
    assert datetime.now(ZoneInfo('Africa/Cairo')) < datetime(2026,11,23,16,0,tzinfo=ZoneInfo('Africa/Cairo')), 'Research cutoff reached'
    module = f'longscout_v{version}'
    subprocess.run(['git','diff','--exit-code',commit,'--',f'research/{module}.py',f'tests/test_{module}.py'],cwd=ROOT,check=True)
    M = importlib.import_module(module)
    offline=os.environ.get('LONGSCOUT_PREREG_OFFLINE')=='1'
    if offline:
        subprocess.run(['git','merge-base','--is-ancestor',commit,'origin/codex/longscout-v91-batch-20261120'],cwd=ROOT,check=True)
    ledger_path = HERE/'longscout_resume_ledger.json'
    if ledger_path.exists(): ledger = json.loads(ledger_path.read_text())
    else:
        old = json.loads((HERE/'longscout_v91_ledger.json').read_text())
        ledger = dict(prior_v90_trials=281,prior_screen_passes=11,registered_trials=283,completed_trials=283,
                      historical_screen_passes=11,versions={'91':dict(status='COMPLETE',batch_passes=0,results_head='65a3fc082f5a8657e40c8c43a7bb757d14383caf',candidates=old['candidates'])})
    assert str(version) not in ledger['versions'], 'Never overwrite a registered version'
    prior = ledger['registered_trials']
    table = json.loads((HERE/'longscout_v91_cases.json').read_text())
    table.update(id=f'LONGSCOUT-V{version}',status='FROZEN before any market outcome of these rules',
                 approval='Owner follow-up20Nov2026: continueV92 onward until worthwhile screen pass or23Nov16:00 Africa/Cairo; all inbox limits persist',
                 sources={module:dict(commit=commit,modules=['longscout_v1',module])},
                 multiple_testing=f'{len(M.CANDIDATES)} candidates; prior{prior} registered trials; all prior/future screens share a consumed calendar, not untouched confirmation.',cases={})
    table['preregistered_cairo']=datetime.now(ZoneInfo('Africa/Cairo')).isoformat()
    table['publication']='SOURCE_PREVIOUSLY_PUSHED; case manifest locally committed before market execution; remote publication queued during workstation connection outage' if offline else 'Source and cases committed and pushed before market execution'
    candidates=[]
    for trial,(key,v) in enumerate(M.CANDIDATES.items(),prior+1):
        assert v['research_only'] and v['tf'] in ('4h','1d')
        table['cases'][key]=dict(source=module,commit=commit,key=key,tf=v['tf'],side=v['sides'],management=v['mgmt'],params=v.get('params',{}),eligible_coins='fresh134+extra31',prior_exposure='No market outcome before this freeze; synthetic checks only',label='NEW_CANDIDATE_RULE_V1_WIDE165_EXPLICIT_FEES',trial=trial)
        candidates.append(dict(trial=trial,key=key,verdict='PREREGISTERED_NOT_RUN'))
    ledger['registered_trials']=prior+len(candidates)
    ledger['versions'][str(version)]=dict(status='PREREGISTERED',source_freeze=commit,batch_passes=None,candidates=candidates)
    (HERE/f'longscout_v{version}_cases.json').write_text(json.dumps(table,indent=1)+'\n',encoding='utf-8',newline='\n')
    ledger_path.write_text(json.dumps(ledger,indent=1)+'\n',encoding='utf-8',newline='\n')
    print(f'V{version}: registered trials{prior+1}..{ledger["registered_trials"]}')


if __name__=='__main__': main()
