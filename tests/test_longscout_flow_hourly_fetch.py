"""Synthetic-only tests; no network or acquisition."""
import csv
import io
import json
import sys
import urllib.error
from datetime import datetime,timezone
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'research'/'extra31_screen'))
import longscout_fetch_flow_hourly as M


def fixture(path,days=2):
    with path.open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['t','o','h','l','c','v','qv'])
        for i in range(days*24):w.writerow([i*M.HOUR,'0.000001','0.0000011','0.0000009','0.000001',2,200])
    return M.complete_hours(path)


def row(t): return [t,'0.000001','0.0000011','0.0000009','0.000001','2',t+M.HOUR-1,'200',4,'1','90','0']


def test_hourly_completeness_exact_reconciliation_and_aggregation(tmp_path):
    p=tmp_path/'input.csv';D=fixture(p)
    records=M.validate([row(t) for t in D],D)
    assert M.aggregate(records)==[(0,96,'2160'),(M.DAY,96,'2160')]
    lines=p.read_text().splitlines();p.write_text('\n'.join(lines[:5]+lines[6:])+'\n')
    assert min(M.complete_hours(p))==M.DAY
    p.write_text(p.read_text()+lines[-1]+'\n')
    with pytest.raises(ValueError,match='duplicate'):M.complete_hours(p)


@pytest.mark.parametrize('idx,value',[(0,1),(6,1),(1,'0.0000011'),(5,'2.1'),(7,'201'),
                                      (8,0),(8,-1),(8,1.5),(8,True),(9,'-1'),(9,'2.000001'),
                                      (9,'NaN'),(10,'-1'),(10,'201')])
def test_bad_fields_are_rejected(tmp_path,idx,value):
    D=fixture(tmp_path/'input.csv',1);t=next(iter(D));bad=row(t);bad[idx]=value
    with pytest.raises(ValueError):M.validate([bad],{t:D[t]})


def test_missing_extra_unordered_and_split_chunks(tmp_path):
    D=fixture(tmp_path/'input.csv',1);ts=list(D)
    with pytest.raises(ValueError):M.validate([row(t) for t in ts[:-1]],D)
    with pytest.raises(ValueError):M.validate([row(t) for t in reversed(ts)],D)
    with pytest.raises(ValueError):M.aggregate(M.validate([row(t) for t in ts[:-1]],{t:D[t] for t in ts[:-1]}))
    assert [len(x) for x in M.chunks(range(0,1501*M.HOUR,M.HOUR))]==[1500,1]
    assert len(M.chunks([0,M.HOUR,3*M.HOUR]))==2


class Response:
    def __init__(self,weight):self.headers={'X-MBX-USED-WEIGHT-1M':str(weight)}
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self):return b'[]'


def test_pacing_weight_cap_and_deadline():
    clock=[0.];sleeps=[];calls=[];checkpoints=[]
    def sleep(n):sleeps.append(n);clock[0]+=n
    def opener(url,timeout):calls.append(url);return Response(1500 if len(calls)==2 else 1)
    f=M.Fetcher(opener=opener,monotonic=lambda:clock[0],sleep=sleep,checkpoint=lambda:checkpoints.append(1),
                now=lambda:datetime(2026,11,20,tzinfo=timezone.utc))
    q,b=f.get('BTCUSDT',[0]);f.get('BTCUSDT',[M.HOUR])
    assert q['interval']=='1h' and q['limit']==1 and b==b'[]' and sleeps==[1,60] and len(checkpoints)==2
    f.requests=6000
    with pytest.raises(M.StopJob,match='cap'):f.get('BTCUSDT',[0])
    g=M.Fetcher(opener=opener,now=lambda:datetime(2026,11,23,14,tzinfo=timezone.utc))
    with pytest.raises(M.StopJob,match='deadline'):g.get('BTCUSDT',[0])
    assert g.requests==0


@pytest.mark.parametrize('status',[418,429])
def test_rate_error_stop(status):
    def fail(url,timeout):raise urllib.error.HTTPError(url,status,'blocked',{},io.BytesIO())
    f=M.Fetcher(opener=fail,now=lambda:datetime(2026,11,20,tzinfo=timezone.utc))
    with pytest.raises(M.StopJob,match=str(status)):f.get('BTCUSDT',[0])


def test_three_consecutive_transport_failures_stop():
    def fail(url,timeout):raise urllib.error.URLError('offline')
    f=M.Fetcher(opener=fail,sleep=lambda _:None,now=lambda:datetime(2026,11,20,tzinfo=timezone.utc))
    for _ in range(2):
        with pytest.raises(urllib.error.URLError):f.get('BTCUSDT',[0])
    with pytest.raises(M.StopJob,match='three consecutive'):f.get('BTCUSDT',[0])
    assert f.requests==3


def test_audit_and_exact_universe(tmp_path):
    pool=tmp_path/'pool';pool.mkdir();syms=['BTCUSDT']+[f'COIN{i}' for i in range(165)]
    for s in syms:(pool/(s+'_1h.csv')).write_text('synthetic')
    audit=tmp_path/'audit.json';audit.write_text(json.dumps({'copied_files':{s:M.sha(pool/(s+'_1h.csv')) for s in syms}}))
    assert len(M.load_contract(pool,audit,M.sha(audit)))==166
    (pool/'COIN0_1h.csv').write_text('changed')
    with pytest.raises(ValueError,match='hash mismatch'):M.load_contract(pool,audit,M.sha(audit))


def test_probe_report_checkpoint_and_hashed_raw(tmp_path,monkeypatch):
    import gzip
    out=tmp_path/'output';source=tmp_path/'input.csv';D=fixture(source,1)
    syms=['BTCUSDT']+[f'COIN{i:03}' for i in range(165)]
    monkeypatch.setattr(M,'EXPECTED_OUTPUT',out)
    monkeypatch.setattr(M,'load_contract',lambda *args:{s:'0'*64 for s in syms})
    monkeypatch.setattr(M,'complete_hours',lambda *args:D)
    class FakeFetcher:
        def __init__(self,checkpoint):self.requests=0;self.checkpoint=checkpoint
        def get(self,s,ts):
            q={'symbol':s,'interval':'1h','startTime':ts[0],'endTime':ts[-1]+M.HOUR-1,'limit':len(ts)}
            self.before_request(q,self.requests+1);self.requests+=1;self.checkpoint()
            return q,json.dumps([row(t) for t in ts]).encode()
    monkeypatch.setattr(M,'Fetcher',FakeFetcher)
    rc=M.main(['--data-pool',str(tmp_path),'--input-audit',str(source),'--audit-sha256','0'*64,
               '--out',str(out),'--max-symbols','1'])
    report=json.loads((out/'report_attempt001.json').read_text());assert rc==1 and report['status']=='INCOMPLETE' and report['probe']
    item=report['symbols']['BTCUSDT'];assert item['status']=='PASS' and item['days']==1
    meta=item['chunks'][0];blob=(out/'raw'/meta['file']).read_bytes()
    assert M.digest(blob)==meta['gzip_sha256'] and M.digest(gzip.decompress(blob))==meta['raw_sha256']
    assert json.loads((out/'progress.json').read_text())==report
    with pytest.raises(FileExistsError):M.main(['--data-pool',str(tmp_path),'--input-audit',str(source),
                                              '--audit-sha256','0'*64,'--out',str(out),'--max-symbols','1'])
    args=['--data-pool',str(tmp_path),'--input-audit',str(source),'--audit-sha256','0'*64,
          '--out',str(out),'--max-symbols','1','--resume']
    assert M.main(args)==1
    second=json.loads((out/'report_attempt002.json').read_text())
    assert second['requests']==1 and second['symbols']['BTCUSDT']['status']=='PASS'
    assert json.loads((out/'report_attempt001.json').read_text())==report
    dest=out/'daily'/'BTCUSDT.csv';old=dest.read_bytes();dest.write_bytes(old+b'bad')
    with pytest.raises(ValueError,match='daily CSV hash'):M.main(args)
    dest.write_bytes(old)
    raw=out/'raw'/meta['file'];old=raw.read_bytes();raw.write_bytes(old+b'bad')
    with pytest.raises(ValueError,match='gzip hash'):M.main(args)
    raw.write_bytes(old)
    progress=out/'progress.json';p=json.loads(progress.read_text());state=p['symbols']['BTCUSDT']
    state['status']='STOPPED';state['chunks'][0]['status']='RECEIVED';p['stop_reason']='transport interruption'
    dest.unlink();progress.write_text(json.dumps(p))
    assert M.main(args)==1
    third=json.loads((out/'report_attempt003.json').read_text())
    assert third['requests']==2 and len(third['symbols']['BTCUSDT']['chunks'])==2
    assert raw.read_bytes()==old and (out/'raw'/third['symbols']['BTCUSDT']['chunks'][1]['file']).exists()
    contract=out/'contract.json';c=json.loads(contract.read_text());c['source_sha256']='0'*64;contract.write_text(json.dumps(c))
    with pytest.raises(ValueError,match='contract differs'):M.main(args)
