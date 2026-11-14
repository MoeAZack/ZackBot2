"""Final WINPATH-01 native gates: isolated tests/buildcheck, no install or account action."""
import datetime,json,os,subprocess,sys,time
from pathlib import Path
from zoneinfo import ZoneInfo
REPO=Path(r'F:\ZackBot2_data\WINPATH-01-REVIEW-20261114')
OUT=Path(r'F:\ZackBot2_data\WINPATH-01-REVIEW-20261114-evidence\final_615e14b3')
OUT.mkdir(exist_ok=True)
HEAD='615e14b394400a2bbe188d3b4d7dee5991e7e1d6'
rows=[]
def save():
    p=OUT/'receipt.tmp';p.write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8',newline='\n');p.replace(OUT/'receipt.json')
for kind in ('tests','buildcheck'):
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()==HEAD
    assert not subprocess.check_output(['git','status','--porcelain'],cwd=REPO,text=True).strip()
    app=OUT/(kind+'_appdata');roam=OUT/(kind+'_roaming');app.mkdir(exist_ok=True);roam.mkdir(exist_ok=True)
    env=dict(os.environ,LOCALAPPDATA=str(app),APPDATA=str(roam),PYTHONIOENCODING='utf-8',ZB_NOPAUSE='1')
    env.pop('PYTEST_ADDOPTS',None)
    env['PATH']=str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
    cmd=([sys.executable,'-m','pytest','-q','-p','no:cacheprovider','-rfE','-n','6','--junitxml='+str(OUT/'tests.xml'),'tests']
         if kind=='tests' else ['cmd','/d','/c',str(REPO/'build_app.bat'),'buildcheck'])
    now=lambda:datetime.datetime.now(ZoneInfo('Africa/Cairo')).isoformat()
    row={'kind':kind,'head':HEAD,'command':cmd,'started_cairo':now(),'deadline_s':14400,'localappdata':str(app),'no_install':True}
    rows.append(row);save();print(json.dumps(row),flush=True)
    start=time.monotonic()
    with (OUT/(kind+'.log')).open('wb') as f:
        p=subprocess.Popen(cmd,cwd=REPO,env=env,stdout=f,stderr=subprocess.STDOUT);row['pid']=p.pid;save()
        try:rc=p.wait(timeout=14400);row['timed_out']=False
        except subprocess.TimeoutExpired:
            k=subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,text=True,timeout=60)
            p.wait(timeout=60);rc=124;row.update(timed_out=True,deadline_tree_cleanup_rc=k.returncode)
    row.update(exit_code=rc,seconds=round(time.monotonic()-start,2),finished_cairo=now(),
               clean_at_end=not subprocess.check_output(['git','status','--porcelain'],cwd=REPO,text=True).strip())
    save();print(json.dumps(row),flush=True)
print('WINPATH_FINAL_WINDOWS_FINISHED',flush=True)
