"""Pure synthetic checks; never makes network requests."""
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
import longscout_fetch_flow as M


def hourly(path,days=2):
    with path.open('w',newline='') as f:
        w=csv.writer(f); w.writerow(['t','o','h','l','c','v','qv'])
        for i in range(days*24): w.writerow([i*M.HOUR,100,102,98,101,2,200])
    return M.hourly_days(path)


def row(day=0):
    return [day,'100','102','98','101','48',day+M.DAY-1,'4800',24,'12','2300','0']


def test_hourly_complete_days_and_missing_hours(tmp_path):
    p=tmp_path/'in.csv'; D=hourly(p)
    assert list(D)==[0,M.DAY]
    assert M.validate([row(),row(M.DAY)],D)==[(0,24,'2300'),(M.DAY,24,'2300')]
    lines=p.read_text().splitlines(); p.write_text('\n'.join(lines[:5]+lines[6:])+'\n')
    assert list(M.hourly_days(p))==[M.DAY]
    p.write_text(p.read_text()+lines[-1]+'\n')
    with pytest.raises(ValueError,match='duplicate'): M.hourly_days(p)


@pytest.mark.parametrize('index,value',[(0,1),(6,1),(8,-1),(8,0),(8,1.5),(8,True),
                                       (9,'-1'),(9,'49'),(9,'NaN'),(10,'-1'),(10,'5000'),
                                       (7,'4700'),(4,'102'),(5,'47')])
def test_reject_bad_kline(tmp_path,index,value):
    D=hourly(tmp_path/'in.csv',1); bad=row(); bad[index]=value
    with pytest.raises(ValueError): M.validate([bad],D)


def test_microprice_mismatch_and_zero_activity(tmp_path):
    D=hourly(tmp_path/'in.csv',1)
    D[0]=tuple(M.number(v) for v in ['0.000001','0.0000011','0.0000009','0.000001','0','0'])
    zero=[0,'0.000001','0.0000011','0.0000009','0.000001','0',M.DAY-1,'0',0,'0','0','0']
    assert M.validate([zero],D)==[(0,0,'0')]
    bad=zero.copy(); bad[4]='0.0000011'
    with pytest.raises(ValueError,match='reconciliation'): M.validate([bad],D)


def test_reject_missing_extra_bad_order_and_tolerance(tmp_path):
    D=hourly(tmp_path/'in.csv')
    with pytest.raises(ValueError): M.validate([row()],D)
    with pytest.raises(ValueError): M.validate([row(M.DAY),row()],D)
    with pytest.raises(ValueError): M.validate([row()[:-1],row(M.DAY)],D)
    ok=row();ok[10]='4800.000001'; M.validate([ok],{0:D[0]})
    assert M.runs([0,M.DAY,3*M.DAY,4*M.DAY],2)==[[0,M.DAY],[3*M.DAY,4*M.DAY]]


class Response:
    def __init__(self,weight='1'): self.headers={'X-MBX-USED-WEIGHT-1M':weight}
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return b'[]'


def test_public_request_limit_pacing_weight_and_deadline():
    calls=[]; sleeps=[]; clock=[0.]
    def sleep(n): sleeps.append(n);clock[0]+=n
    def opener(url,timeout):
        calls.append(url); return Response('1500' if len(calls)==2 else '1')
    f=M.Fetcher(opener=opener,monotonic=lambda:clock[0],sleep=sleep,
                now=lambda:datetime(2026,11,20,tzinfo=timezone.utc))
    f.get('BTCUSDT',[0]); f.get('BTCUSDT',[M.DAY])
    assert calls[0].startswith(M.URL+'?') and 'symbol=BTCUSDT' in calls[0]
    assert sleeps==[1,60] and f.requests==2
    f.requests=500
    with pytest.raises(M.StopJob,match='cap'):f.get('BTCUSDT',[0])
    g=M.Fetcher(opener=opener,now=lambda:datetime(2026,11,23,14,0,tzinfo=timezone.utc))
    with pytest.raises(M.StopJob,match='deadline'):g.get('BTCUSDT',[0])
    assert g.requests==0


@pytest.mark.parametrize('status',[418,429])
def test_rate_error_stops_entire_job(status):
    def fail(url,timeout): raise urllib.error.HTTPError(url,status,'blocked',{},io.BytesIO())
    f=M.Fetcher(opener=fail,now=lambda:datetime(2026,11,20,tzinfo=timezone.utc))
    with pytest.raises(M.StopJob,match=str(status)): f.get('BTCUSDT',[0])
    assert f.requests==1


def test_contract_hash_and_universe(tmp_path):
    pool=tmp_path/'pool';pool.mkdir()
    names=['BTCUSDT']+[f'COIN{i}' for i in range(165)]
    for s in names:(pool/(s+'_1h.csv')).write_text('synthetic')
    audit=tmp_path/'audit.json'
    audit.write_text(json.dumps({'copied_files':{s:M.sha(pool/(s+'_1h.csv')) for s in names}}))
    digest=M.sha(audit)
    assert len(M.load_contract(pool,audit,digest))==166
    (pool/'COIN0_1h.csv').write_text('changed')
    with pytest.raises(ValueError,match='hash mismatch'): M.load_contract(pool,audit,digest)
    with pytest.raises(ValueError,match='audit SHA'): M.load_contract(pool,audit,'0'*64)
