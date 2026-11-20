"""Synthetic-only archive verification; never performs HTTP requests."""
import calendar
import csv
import io
import json
import sys
import urllib.error
import zipfile
from datetime import datetime,timezone
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'research'/'extra31_screen'))
import longscout_flow_archive as M
H=M.H


def t(y,m,d,h=0):return int(datetime(y,m,d,h,tzinfo=timezone.utc).timestamp()*1000)


def expected(start,n):
    return {start+i*H.HOUR:tuple(H.num(x) for x in ['1','1.1','.9','1','2','200']) for i in range(n)}


def bundle(D,header=None):
    s=io.StringIO(newline='');w=csv.writer(s);w.writerow(header or M.HEADER)
    for x in D:w.writerow([x,'1','1.1','.9','1','2',x+H.HOUR-1,'200',4,'1','90','0'])
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('data.csv',s.getvalue())
    data=b.getvalue();return data,(H.digest(data)+'  COIN-1h-2024-03-01.zip\n').encode()


def test_full_month_or_complete_days_only():
    start=t(2024,3,1);full=expected(start,31*24)
    p=M.plan('COIN',full);assert len(p)==1 and '/monthly/' in p[0][0] and len(p[0][1])==31*24
    del full[t(2024,3,15)]
    with pytest.raises(ValueError,match='partial UTC day'):M.plan('COIN',full)
    partial=expected(t(2024,3,5),48);p=M.plan('COIN',partial)
    assert len(p)==2 and all('/daily/' in url and len(ts)==24 for url,ts in p)
    with pytest.raises(ValueError):M.plan('../bad',partial)


def test_checksum_crc_rows_and_aggregation():
    D=expected(t(2024,3,1),24);z,c=bundle(D)
    records=M.decode(z,c,'COIN-1h-2024-03-01.zip',D)
    assert H.aggregate(records)==[(t(2024,3,1),96,'2160')]
    with pytest.raises(ValueError,match='checksum'):M.decode(z,c.replace(b'COIN',b'EVIL'),'COIN-1h-2024-03-01.zip',D)
    with pytest.raises(ValueError,match='checksum'):M.decode(z+b'x',c,'COIN-1h-2024-03-01.zip',D)
    bad,c2=bundle(D,['wrong']*12)
    with pytest.raises(ValueError,match='header'):M.decode(bad,c2,'COIN-1h-2024-03-01.zip',D)
    changed=dict(D);changed[next(iter(changed))]=(H.num('1.01'),)+changed[next(iter(changed))][1:]
    with pytest.raises(ValueError,match='OHLCV mismatch'):M.decode(z,c,'COIN-1h-2024-03-01.zip',changed)


def test_observed_aave_archive_schema():
    # Header and one representative row from the official March 2024 archive.
    line='1709251200000,105.880,108.290,105.860,108.280,38367.8,1709254799999,4102544.5930,21319,19677.4,2104573.0110,0\n'
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('AAVEUSDT-1h-2024-03.csv',','.join(M.HEADER)+'\n'+line)
    raw=b.getvalue();checksum=(H.digest(raw)+'  AAVEUSDT-1h-2024-03.zip\n').encode()
    D={1709251200000:tuple(H.num(x) for x in ['105.880','108.290','105.860','108.280','38367.8','4102544.5930'])}
    assert M.decode(raw,checksum,'AAVEUSDT-1h-2024-03.zip',D)==[(1709251200000,21319,H.num('2104573.0110'))]


def test_redirect_is_not_followed():
    handler=M.NoRedirect()
    assert handler.redirect_request(None,None,302,'found',{},'https://elsewhere.invalid/') is None
    def redirect(url,timeout):raise urllib.error.HTTPError(url,302,'found',{},io.BytesIO())
    f=M.Downloader(lambda *a:None,opener=redirect,now=lambda:datetime(2026,11,20,tzinfo=timezone.utc))
    with pytest.raises(H.StopJob,match='HTTP 302'):f.get(M.BASE+'/daily/klines/COIN/1h/COIN-1h-2024-03-01.zip')
    assert f.requests==1


class Response:
    headers={'X-MBX-USED-WEIGHT-1M':'1500'}
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self):return b'public'


def test_public_rate_limits_deadline_and_cap():
    clock=[0];sleep=[];records=[]
    def nap(x):sleep.append(x);clock[0]+=x
    f=M.Downloader(lambda n,u:records.append((n,u)),opener=lambda *a,**k:Response(),
                   now=lambda:datetime(2026,11,20,tzinfo=timezone.utc),monotonic=lambda:clock[0],sleep=nap)
    u=M.BASE+'/daily/klines/COIN/1h/COIN-1h-2024-03-01.zip'
    f.get(u);f.get(u+'.CHECKSUM');assert sleep==[60,60] and len(records)==2
    f.requests=6000
    with pytest.raises(H.StopJob,match='cap'):f.get(u)
    g=M.Downloader(lambda *a:None,now=lambda:datetime(2026,11,23,14,tzinfo=timezone.utc))
    with pytest.raises(H.StopJob,match='deadline'):g.get(u)


@pytest.mark.parametrize('status',[403,418,429,451])
def test_access_and_rate_error_stop(status):
    def fail(url,timeout):raise urllib.error.HTTPError(url,status,'blocked',{},io.BytesIO())
    f=M.Downloader(lambda *a:None,opener=fail,now=lambda:datetime(2026,11,20,tzinfo=timezone.utc))
    with pytest.raises(H.StopJob,match=str(status)):f.get(M.BASE+'/daily/klines/COIN/1h/COIN-1h-2024-03-01.zip')


def test_resume_rejects_unknown_or_changed_contract(tmp_path):
    for name in ('raw','daily','requests'):(tmp_path/name).mkdir()
    contract={'probe':False,'extra31':['COIN']};(tmp_path/'contract.json').write_text(json.dumps(contract))
    report={'status':'INCOMPLETE','requests':0,'symbols':{'COIN':{'status':'NOT_RUN','input_sha256':'a','objects':[],'segments':[]}}}
    (tmp_path/'progress.json').write_text(json.dumps(report))
    assert M.verify_resume(tmp_path,contract,report,{'COIN':'a'},tmp_path)==({'COIN':{}},1)
    (tmp_path/'unexpected').write_text('x')
    with pytest.raises(ValueError,match='unknown'):M.verify_resume(tmp_path,contract,report,{'COIN':'a'},tmp_path)
    (tmp_path/'unexpected').unlink()
    with pytest.raises(ValueError,match='contract'):M.verify_resume(tmp_path,{'probe':True},report,{'COIN':'a'},tmp_path)


def test_resume_validates_and_reuses_artifacts_without_network(tmp_path,monkeypatch):
    extra=[f'COIN{i:02}' for i in range(31)];out=tmp_path/'release';frozen=tmp_path/'contract-source.json'
    frozen.write_text(json.dumps({'universe':{'extra31':extra}}));D=expected(t(2024,3,1),24)
    monkeypatch.setattr(M,'OUT',out);monkeypatch.setattr(M,'CONTRACT',frozen)
    monkeypatch.setattr(H,'load_contract',lambda *a:{s:'a'*64 for s in extra}|{'BTCUSDT':'b'*64})
    monkeypatch.setattr(H,'complete_hours',lambda *a:D)
    zipbytes,_=bundle(D);phase=[0];calls=[]
    class FakeDownloader:
        def __init__(self,record,requests=0):self.record=record;self.requests=requests
        def get(self,url):
            if phase[0]==0 and self.requests==2:raise H.StopJob('synthetic interruption')
            n=self.requests+1;self.record(n,url);self.requests=n;calls.append(url)
            if url.endswith('.CHECKSUM'):
                filename=url.rsplit('/',1)[-1].removesuffix('.CHECKSUM')
                return n,(H.digest(zipbytes)+'  '+filename+'\n').encode()
            return n,zipbytes
    monkeypatch.setattr(M,'Downloader',FakeDownloader)
    args=['--data-pool',str(tmp_path),'--input-audit',str(frozen),'--audit-sha256','a'*64,'--out',str(out)]
    assert M.main(args)==1
    prior=json.loads((out/'report_attempt001.json').read_text());assert prior['requests']==2
    first=(out/'raw'/'000001.bin').read_bytes();phase[0]=1
    raw=out/'raw'/'000002.bin';original=raw.read_bytes();raw.write_bytes(original+b'changed')
    with pytest.raises(ValueError,match='object hash'):M.main(args+['--resume'])
    raw.write_bytes(original)
    attempt=out/'report_attempt001.json';original=attempt.read_bytes();attempt.write_bytes(original+b'changed')
    with pytest.raises(ValueError,match='attempt report hash'):M.main(args+['--resume'])
    attempt.write_bytes(original)
    assert M.main(args+['--resume'])==0
    report=json.loads((out/'report.json').read_text())
    assert report['status']=='ARCHIVE31_COMPLETE' and report['requests']==62 and len(calls)==62
    assert (out/'raw'/'000001.bin').read_bytes()==first
    with pytest.raises(ValueError,match='completed'):M.main(args+['--resume'])
