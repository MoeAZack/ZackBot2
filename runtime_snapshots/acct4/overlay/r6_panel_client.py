"""Account-4-only local panel client. Session remains in memory, never printed."""
import json,urllib.request,urllib.error,hashlib,hmac,secrets,sys
from pathlib import Path
ROOT=Path('REDACTED_VALUE')
BASE='http://127.0.0.1:8768'
def api(path,body=None,timeout=60):
    s=json.loads((ROOT/'session.json').read_text(encoding='utf-8-sig'))
    assert s.get('port')==8768
    token=s['token']
    data=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(BASE+path,data=data,headers={'X-ZB-Token':token,'Origin':BASE,'Content-Type':'application/json'},method='GET' if body is None else 'POST')
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r: result=json.load(r)
    except urllib.error.HTTPError as ex:
        raise RuntimeError('Panel HTTP '+str(ex.code)) from None
    if isinstance(result,dict) and result.get('ok') is False:raise RuntimeError(str(result.get('error','Panel refused')))
    return result
def identity():
    s=json.loads((ROOT/'session.json').read_text(encoding='utf-8-sig')); n=secrets.token_hex(16)
    p=api('/api/ping?nonce='+n,timeout=10)
    assert p.get('app')=='zackbot' and hmac.compare_digest(p.get('proof',''),hmac.new(s['token'].encode(),n.encode(),hashlib.sha256).hexdigest())
    return s['pid']
if __name__=='__main__':
    try:
        pid=identity();s=api('/api/status',timeout=20);assert s['mode']=='PAPER'
        print(json.dumps({'pid':pid,'mode':s['mode'],'paused':s['settings']['ENTRIES_PAUSED'],'error':s.get('error'),'health':{k:s.get('health',{}).get(k) for k in ['engine','exchange','last_cycle_ok','unprotected','untracked','orphans']},'last_cycle':s.get('last_cycle'),'lots':[{k:l.get(k) for k in ['key','sleeve','symbol','side','qty','stop','protected','stop_state','stop_age_s']} for l in s.get('lots',[])]},indent=2))
    except Exception as ex:
        print(json.dumps({'panel_available':False,'exception':type(ex).__name__,'error':str(ex)}));sys.exit(1)
