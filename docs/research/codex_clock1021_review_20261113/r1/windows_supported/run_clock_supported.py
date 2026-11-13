"""Bounded, serial Windows evidence runs. No source edits or installations."""
import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(r'F:\ZackBot2_data\CLOCK-1021-REVIEW-20261113')
OUT = ROOT / 'supported_window_20261114'
OUT.mkdir(exist_ok=True)
HEADS = {'head_r1':'6e259ea5acf2379fe09bff933d7c5b05cfaa269e',
         'base':'9cb4341f2d94c21434492515b8e85f2b42c148ce'}
DEADLINE = 14400
receipt = []
now = lambda: datetime.datetime.now(ZoneInfo('Africa/Cairo')).isoformat()

def write_receipt():
    temp = OUT / 'receipt.tmp'
    temp.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8',newline='\n')
    temp.replace(OUT / 'receipt.json')

for kind in ('tests','buildcheck'):
    for name,expected in HEADS.items():
        repo = ROOT / name
        actual = subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
        assert actual == expected
        assert not subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True).strip()
        appdata = OUT / (name+'_'+kind+'_appdata')
        roaming = OUT / (name+'_'+kind+'_roaming')
        appdata.mkdir(exist_ok=True); roaming.mkdir(exist_ok=True)
        env = dict(os.environ,LOCALAPPDATA=str(appdata),APPDATA=str(roaming),
                   PYTHONIOENCODING='utf-8',ZB_NOPAUSE='1')
        env.pop('PYTEST_ADDOPTS',None)
        env['PATH'] = str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
        if kind == 'tests':
            cmd = [sys.executable,'-m','pytest','-q','-p','no:cacheprovider','-rfE','-n','6',
                   '--junitxml='+str(OUT/(name+'_tests.xml')),'tests']
        else:
            cmd = ['cmd','/d','/c',str(repo/'build_app.bat'),'buildcheck']
        row = {'name':name,'kind':kind,'head':actual,'started_cairo':now(),
               'deadline_s':DEADLINE,'python':sys.executable,'command':cmd,
               'localappdata':str(appdata),'no_install':True,
               'dependencies':{p:importlib.metadata.version(p) for p in ('pytest','pytest-xdist','pandas','numpy','requests')}}
        receipt.append(row);write_receipt();print(json.dumps(row),flush=True)
        start=time.monotonic()
        with (OUT/(name+'_'+kind+'.log')).open('wb') as log:
            proc = subprocess.Popen(cmd,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT)
            row['pid']=proc.pid;write_receipt()
            try:
                code=proc.wait(timeout=DEADLINE)
                row['timed_out']=False
            except subprocess.TimeoutExpired:
                # Only this controller's direct child and its private review descendants.
                killed=subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,text=True,timeout=60)
                row['deadline_tree_cleanup_rc']=killed.returncode
                proc.wait(timeout=60)
                code=124;row['timed_out']=True
        row.update(exit_code=code,finished_cairo=now(),seconds=round(time.monotonic()-start,2),
                   clean_at_end=not subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True).strip())
        write_receipt();print(json.dumps(row),flush=True)
print('SUPPORTED_WINDOWS_RUNS_FINISHED',flush=True)
