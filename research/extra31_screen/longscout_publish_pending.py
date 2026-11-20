"""Manually launched research-only publisher; no task scheduler or bot access.

Pushes only this research branch and posts queued PR347 results. Five-minute
network cadence; disk receipts prevent duplicate posts after a lost response.
Stops at the owner's Cairo cutoff or an own-directory STOP file.
"""
import json
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
CACHE=Path('C:/Dev/ZackBot_research_cache')
STATE=CACHE/'longscout_codex_publication_20261120'
BRANCH='codex/longscout-v91-batch-20261120'
REPO='MoeAZack/ZackBot2'
DEADLINE=datetime(2026,11,23,16,0,tzinfo=ZoneInfo('Africa/Cairo'))


def command(args,timeout=60):
    r=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=timeout)
    if r.returncode:raise RuntimeError('command unavailable')
    return r.stdout.strip()


def pending():
    entries=[]
    for folder in CACHE.glob('longscout_codex_v[0-9]*_20261120'):
        request,receipt=folder/'github_result_request.json',folder/'github_receipt.json'
        if not request.exists() or not receipt.exists():continue
        r=json.loads(receipt.read_text(encoding='utf-8'))
        if r.get('publication_pending'):
            body=json.loads(request.read_text(encoding='utf-8'))['body']
            assert body.startswith('<!-- agent: codex -->') and r['head'] in body
            version=int(folder.name.split('_v',1)[1].split('_')[0])
            assert f'LONGSCOUT V{version},' in body
            entries.append((version,request,receipt,r))
    return sorted(entries)


def cycle():
    assert command(['git','branch','--show-current'])==BRANCH
    entries=pending()
    head=command(['git','rev-parse','HEAD'])
    for _,_,_,r in entries:command(['git','merge-base','--is-ancestor',r['head'],head])
    command(['git','push','origin',f'{head}:refs/heads/{BRANCH}'],timeout=45)
    command(['git','update-ref','refs/longscout/last-confirmed-remote',head])
    (STATE/'network.json').write_text(json.dumps(dict(last_successful_push=head,cairo=datetime.now(ZoneInfo('Africa/Cairo')).isoformat())),encoding='utf-8')
    if not entries:return []
    since=(datetime.now(timezone.utc)-timedelta(days=4)).isoformat().replace('+00:00','Z')
    pages=json.loads(command(['gh','api','--paginate','--slurp',f'repos/{REPO}/issues/347/comments?since={since}&per_page=100']))
    comments=[c for page in pages for c in page]
    published=[]
    for version,request,receipt,r in entries:
        found=next((c for c in comments if r['head'] in c.get('body','') and f'LONGSCOUT V{version},' in c.get('body','') and c.get('body','').startswith('<!-- agent: codex -->')),None)
        if found is None:
            found=json.loads(command(['gh','api','--method','POST',f'repos/{REPO}/issues/347/comments','--input',str(request)]))
        r.update(id=found['id'],html_url=found['html_url'],publication_pending=False,published_cairo=datetime.now(ZoneInfo('Africa/Cairo')).isoformat())
        receipt.write_text(json.dumps(r,indent=1)+'\n',encoding='utf-8')
        published.append(dict(version=version,url=r['html_url']))
    return published


def main():
    STATE.mkdir(exist_ok=True)
    reason=None
    while datetime.now(ZoneInfo('Africa/Cairo'))<DEADLINE and not (STATE/'STOP').exists():
        try:
            result=cycle()
            if result:print(json.dumps(dict(published=result)),flush=True)
            if reason is not None:print('Publication connection restored',flush=True)
            reason=None
        except (RuntimeError,subprocess.TimeoutExpired,OSError,ValueError,KeyError,AssertionError) as e:
            new=type(e).__name__
            if new!=reason:print('Publication pending: '+new,flush=True)
            reason=new
        for _ in range(30):
            if datetime.now(ZoneInfo('Africa/Cairo'))>=DEADLINE or (STATE/'STOP').exists():break
            time.sleep(10)
    print('Publisher stopped at own STOP/cutoff',flush=True)


if __name__=='__main__':main()
