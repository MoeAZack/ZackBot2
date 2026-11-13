"""Record collection counts only; no tests are executed."""
import json,os,subprocess,sys
from pathlib import Path
ROOT=Path(r'F:\ZackBot2_data\CLOCK-1021-REVIEW-20261113')
OUT=ROOT/'supported_window_20261114'
rows=[]
for name in ('head_r1','base'):
    repo=ROOT/name
    env=dict(os.environ,LOCALAPPDATA=str(OUT/(name+'_collection_appdata')),
             APPDATA=str(OUT/(name+'_collection_roaming')),PYTHONIOENCODING='utf-8')
    env.pop('PYTEST_ADDOPTS',None)
    row={'name':name,'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()}
    with (OUT/(name+'_collected.txt')).open('wb') as f:
        result=subprocess.run([sys.executable,'-m','pytest','--collect-only','-q','-p','no:cacheprovider','tests'],
                              cwd=repo,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=180)
    row['exit_code']=result.returncode;rows.append(row)
    assert result.returncode==0,row
(OUT/'collection_receipt.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(rows))
