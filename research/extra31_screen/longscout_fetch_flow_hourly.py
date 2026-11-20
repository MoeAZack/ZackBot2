"""Public hourly flow acquisition with full reconciliation to pinned hourly bars.

Fresh dedicated output or explicit, verified resume only. Partial results are
never COMPLETE. No authenticated endpoint or account information is used.
"""
import argparse
import csv
import gzip
import hashlib
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

HOUR=3_600_000
DAY=24*HOUR
LIMIT=1500
MAX_REQUESTS=6000
URL='https://fapi.binance.com/fapi/v1/klines'
DEADLINE=datetime(2026,11,23,16,tzinfo=ZoneInfo('Africa/Cairo'))
EXPECTED_OUTPUT=Path('C:/Dev/ZackBot_research_cache/longscout_codex_flow_hourly_20261120')


def digest(data): return hashlib.sha256(data).hexdigest()


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''): h.update(part)
    return h.hexdigest()


def num(x):
    try: v=Decimal(str(x))
    except (InvalidOperation,ValueError): raise ValueError('invalid numeric value')
    if not v.is_finite(): raise ValueError('nonfinite numeric value')
    return v


def near(a,b,absolute):
    a,b=num(a),num(b)
    return abs(a-b)<=max(absolute,max(abs(a),abs(b))*Decimal('1e-9'))


def load_contract(pool,audit_path,audit_sha):
    if not re.fullmatch(r'[0-9a-f]{64}',audit_sha) or sha(audit_path)!=audit_sha: raise ValueError('audit SHA mismatch')
    files=json.loads(Path(audit_path).read_text(encoding='utf-8'))['copied_files']
    if not isinstance(files,dict) or len(files)!=166 or 'BTCUSDT' not in files: raise ValueError('universe must be 165 plus BTC')
    if {p.name for p in Path(pool).glob('*_1h.csv')}!={s+'_1h.csv' for s in files}: raise ValueError('pool universe mismatch')
    for s,h in files.items():
        if not re.fullmatch(r'[A-Z0-9]+',s) or not re.fullmatch(r'[0-9a-f]{64}',h): raise ValueError('invalid audit entry')
        if sha(Path(pool)/(s+'_1h.csv'))!=h: raise ValueError(s+': input hash mismatch')
    return files


def complete_hours(path):
    groups={}; seen=set()
    with Path(path).open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f)
        if not {'t','o','h','l','c','v','qv'}.issubset(reader.fieldnames or []): raise ValueError('input columns')
        for row in reader:
            t=int(row['t'])
            if t%HOUR or t in seen: raise ValueError('unaligned or duplicate hour')
            seen.add(t)
            v=tuple(num(row[k]) for k in ('o','h','l','c','v','qv'))
            if min(v[:4])<=0 or min(v[4:])<0: raise ValueError('invalid input OHLCV')
            groups.setdefault(t//DAY*DAY,{})[t]=v
    out={}
    for day in sorted(groups):
        if set(groups[day])==set(range(day,day+DAY,HOUR)):
            out.update(sorted(groups[day].items()))
    if not out: raise ValueError('no complete days')
    return out


def chunks(hours):
    out=[]; current=[]
    for t in sorted(hours):
        if current and (t!=current[-1]+HOUR or len(current)>=LIMIT):
            out.append(current);current=[]
        current.append(t)
    if current: out.append(current)
    return out


def validate(rows,expected):
    if not isinstance(rows,list) or len(rows)!=len(expected): raise ValueError('missing or extra hourly rows')
    out=[];previous=None
    for row,t in zip(rows,expected):
        if not isinstance(row,list) or len(row)!=12: raise ValueError('invalid field count')
        if type(row[0]) is not int or row[0]!=t or (previous is not None and t<=previous): raise ValueError('hour timestamp mismatch')
        if type(row[6]) is not int or row[6]!=t+HOUR-1: raise ValueError('hour close timestamp mismatch')
        vals=[num(row[i]) for i in (1,2,3,4,5,7)]
        if min(vals[:4])<=0 or min(vals[4:])<0: raise ValueError('invalid OHLCV')
        if not all(near(a,b,Decimal('1e-12') if i<4 else Decimal('1e-6'))
                   for i,(a,b) in enumerate(zip(vals,expected[t]))): raise ValueError('hourly OHLCV mismatch')
        count=row[8]
        if type(count) is not int or count<0 or (vals[5]>0 and count==0): raise ValueError('invalid trade count')
        tb,tq=num(row[9]),num(row[10])
        if tb<0 or tb>vals[4]: raise ValueError('invalid taker base')
        if tq<0 or tq>vals[5]: raise ValueError('invalid taker quote')
        out.append((t,count,tq));previous=t
    return out


def aggregate(records):
    by={}
    for t,n,q in records: by.setdefault(t//DAY*DAY,[]).append((t,n,q))
    result=[]
    for day,rows in sorted(by.items()):
        if [x[0] for x in rows]!=list(range(day,day+DAY,HOUR)): raise ValueError('incomplete verified day')
        result.append((day,sum(x[1] for x in rows),str(sum((x[2] for x in rows),Decimal(0)))))
    return result


class StopJob(Exception): pass


class Fetcher:
    def __init__(self,checkpoint=lambda:None,opener=urllib.request.urlopen,monotonic=time.monotonic,
                 sleep=time.sleep,now=lambda:datetime.now(timezone.utc)):
        self.checkpoint=checkpoint;self.opener=opener;self.monotonic=monotonic;self.sleep=sleep;self.now=now
        self.requests=0;self.last=None;self.transport_failures=0;self.before_request=lambda q,n:None

    def get(self,symbol,ts):
        if self.requests>=MAX_REQUESTS: raise StopJob('request cap')
        if self.last is not None:
            wait=1-(self.monotonic()-self.last)
            if wait>0:self.sleep(wait)
        if self.now().astimezone(ZoneInfo('Africa/Cairo'))>=DEADLINE: raise StopJob('Cairo deadline')
        query=dict(symbol=symbol,interval='1h',startTime=ts[0],endTime=ts[-1]+HOUR-1,limit=len(ts))
        self.before_request(query,self.requests+1)
        self.requests+=1;self.last=self.monotonic();self.checkpoint()
        try:
            with self.opener(URL+'?'+urllib.parse.urlencode(query),timeout=30) as response:
                body=response.read();weight=response.headers.get('X-MBX-USED-WEIGHT-1M')
                if weight is not None and int(weight)>=1500:self.sleep(60)
        except urllib.error.HTTPError as e:
            self.transport_failures=0
            if e.code in (403,418,429,451):raise StopJob('HTTP '+str(e.code)) from e
            raise
        except (urllib.error.URLError,TimeoutError,socket.timeout,ConnectionError,OSError) as e:
            self.transport_failures+=1
            if self.transport_failures>=3:raise StopJob('three consecutive transport failures') from e
            raise
        self.transport_failures=0
        return query,body


def write_json(path,obj,mode='x'):
    with Path(path).open(mode,encoding='utf-8') as f:json.dump(obj,f,indent=2,sort_keys=True)


def verify_resume(out,contract,files,pool,report):
    """Verify every recorded artifact; return validated reusable chunk records."""
    if json.loads((out/'contract.json').read_text())!=contract: raise ValueError('resume contract differs')
    if report.get('status')=='COMPLETE' or report.get('probe')!=contract['probe']:
        raise ValueError('completed release or probe mismatch')
    if report.get('stop_reason') in ('HTTP 403','HTTP 418','HTTP 429','HTTP 451'):
        raise ValueError('access or rate stop requires independent resolution')
    states=report.get('symbols');n=report.get('requests')
    if not isinstance(states,dict) or set(states)!=set(files) or type(n) is not int or not 0<=n<=MAX_REQUESTS:
        raise ValueError('invalid checkpoint state')
    expected_root={'contract.json','progress.json','raw','daily','requests'}
    attempts=sorted(p.name for p in out.glob('report_attempt*.json'))
    if attempts!=[f'report_attempt{i:03}.json' for i in range(1,len(attempts)+1)]: raise ValueError('invalid attempt reports')
    expected_root.update(attempts)
    if {p.name for p in out.iterdir()}!=expected_root: raise ValueError('unknown output artifact')
    reqnames=[f'{i:06}.json' for i in range(1,n+1)]
    if sorted(p.name for p in (out/'requests').iterdir())!=reqnames: raise ValueError('request ledger mismatch')
    ledger={}
    for i,name in enumerate(reqnames,1):
        q=json.loads((out/'requests'/name).read_text())
        if q.get('sequence')!=i or q.get('symbol') not in files:raise ValueError('invalid request ledger')
        ledger[i]=q
    for name in attempts:
        r=json.loads((out/name).read_text())
        if r.get('attempt')!=int(name[14:17]) or not 0<=r.get('requests',-1)<=n: raise ValueError('invalid attempt report')
    known_raw=set();known_daily=set();reuse={}
    for s,state in states.items():
        if state.get('input_sha256')!=files[s] or state.get('status') not in ('NOT_RUN','RUNNING','STOPPED','FAILED','PASS'):
            raise ValueError('invalid symbol status')
        expected=complete_hours(Path(pool)/(s+'_1h.csv')) if state.get('chunks') or state.get('status')=='PASS' else None
        allowed={tuple(c):c for c in chunks(expected)} if expected is not None else {}
        valid={}
        for meta in state.get('chunks',[]):
            name=meta.get('file');q=meta.get('query',{});seq=meta.get('sequence')
            if type(seq) is not int or seq not in ledger or ledger[seq].get('symbol')!=s or name!=f'{s}_{seq:06}.json.gz':
                raise ValueError('invalid chunk identity')
            if name in known_raw or meta.get('status') not in ('RECEIVED','PASS'): raise ValueError('invalid chunk status')
            known_raw.add(name)
            start=q.get('startTime');end=q.get('endTime');limit=q.get('limit')
            if type(start) is not int or type(end) is not int or type(limit) is not int or limit<1 or limit>LIMIT:
                raise ValueError('invalid chunk query')
            ts=tuple(range(start,end-HOUR+2,HOUR))
            query=dict(symbol=s,interval='1h',startTime=start,endTime=end,limit=limit)
            if q!=query or ledger[seq].get('query')!=query or len(ts)!=limit or not ts or ts[-1]+HOUR-1!=end or ts not in allowed:
                raise ValueError('chunk query differs from input')
            blob=(out/'raw'/name).read_bytes()
            if digest(blob)!=meta.get('gzip_sha256'):raise ValueError('gzip hash mismatch')
            body=gzip.decompress(blob)
            if digest(body)!=meta.get('raw_sha256'):raise ValueError('raw hash mismatch')
            if meta['status']=='PASS':
                if ts in valid: raise ValueError('duplicate PASS chunk')
                valid[ts]=validate(json.loads(body),{t:expected[t] for t in ts})
        reuse[s]=valid
        if state['status']=='PASS':
            if set(valid)!=set(allowed):raise ValueError('passing symbol missing chunks')
            dest=out/'daily'/f'{s}.csv';known_daily.add(dest.name)
            if sha(dest)!=state.get('daily_sha256'):raise ValueError('daily CSV hash mismatch')
            records=[r for ts in allowed for r in valid[ts]]
            daily=aggregate(records)
            with dest.open(newline='',encoding='utf-8') as f: rows=list(csv.reader(f))
            if rows!=[['t','trade_count','taker_buy_qv']]+[[str(v) for v in row] for row in daily] or len(daily)!=state.get('days'):
                raise ValueError('daily CSV content mismatch')
    if {p.name for p in (out/'raw').iterdir()}!=known_raw or {p.name for p in (out/'daily').iterdir()}!=known_daily:
        raise ValueError('unknown raw or daily artifact')
    return reuse,len(attempts)+1


def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument('--data-pool',required=True);ap.add_argument('--input-audit',required=True)
    ap.add_argument('--audit-sha256',required=True);ap.add_argument('--out',required=True)
    ap.add_argument('--max-symbols',type=int);ap.add_argument('--resume',action='store_true')
    a=ap.parse_args(argv)
    if Path(a.out).resolve()!=EXPECTED_OUTPUT.resolve():raise SystemExit('output path must be dedicated hourly flow folder')
    if a.max_symbols is not None and not 1<=a.max_symbols<=166:raise SystemExit('max-symbols must be 1..166')
    files=load_contract(a.data_pool,a.input_audit,a.audit_sha256)
    out=Path(a.out)
    contract=dict(audit_sha256=a.audit_sha256,source_sha256=sha(__file__),symbols=sorted(files),
                  endpoint=URL,interval='1h',limit=LIMIT,request_cap=MAX_REQUESTS,deadline_cairo=DEADLINE.isoformat(),
                  probe=a.max_symbols is not None,max_symbols=a.max_symbols)
    if a.resume:
        report=json.loads((out/'progress.json').read_text(encoding='utf-8'))
        reuse,attempt=verify_resume(out,contract,files,a.data_pool,report)
        states=report['symbols'];report['stop_reason']=None;report['status']='INCOMPLETE'
    else:
        out.mkdir(parents=False,exist_ok=False)
        for sub in ('raw','daily','requests'):(out/sub).mkdir()
        states={s:dict(status='NOT_RUN',input_sha256=files[s],chunks=[]) for s in sorted(files)}
        report=dict(status='INCOMPLETE',probe=contract['probe'],requests=0,stop_reason=None,symbols=states)
        write_json(out/'contract.json',contract);reuse={};attempt=1
    report['attempt']=attempt
    def checkpoint():
        report['requests']=fetch.requests
        tmp=out/'progress.tmp'
        write_json(tmp,report,'w');tmp.replace(out/'progress.json')
    fetch=Fetcher(checkpoint=checkpoint);fetch.requests=report['requests']
    fetch.before_request=lambda q,n:write_json(out/'requests'/f'{n:06}.json',dict(sequence=n,symbol=q['symbol'],query=q))
    checkpoint()
    for s in sorted(files)[:a.max_symbols or len(files)]:
        if states[s]['status']=='PASS':continue
        states[s]['status']='RUNNING';checkpoint()
        try:
            expected=complete_hours(Path(a.data_pool)/(s+'_1h.csv'));records=[]
            for ts in chunks(expected):
                if tuple(ts) in reuse.get(s,{}):
                    records.extend(reuse[s][tuple(ts)]);continue
                if fetch.requests>=MAX_REQUESTS:raise StopJob('request cap')
                seq=fetch.requests+1
                query,body=fetch.get(s,ts)
                raw=out/'raw'/f'{s}_{seq:06}.json.gz';z=gzip.compress(body,mtime=0)
                with raw.open('xb') as f:f.write(z)
                meta=dict(file=raw.name,sequence=seq,raw_sha256=digest(body),gzip_sha256=digest(z),query=query,status='RECEIVED')
                states[s]['chunks'].append(meta);checkpoint()
                records.extend(validate(json.loads(body),{t:expected[t] for t in ts}))
                meta['status']='PASS';checkpoint()
            daily=aggregate(records)
            dest=out/'daily'/f'{s}.csv'
            with dest.open('x',newline='',encoding='utf-8') as f:
                w=csv.writer(f);w.writerow(['t','trade_count','taker_buy_qv']);w.writerows(daily)
            states[s].update(status='PASS',days=len(daily),daily_sha256=sha(dest));checkpoint()
        except StopJob as e:
            states[s].update(status='STOPPED',error=str(e));report['stop_reason']=str(e);break
        except (ValueError,TypeError,KeyError,OSError,urllib.error.URLError) as e:
            states[s].update(status='FAILED',error=f'{type(e).__name__}: {e}');checkpoint()
    complete=not report['probe'] and report['stop_reason'] is None and all(x['status']=='PASS' for x in states.values())
    report['status']='COMPLETE' if complete else ('STOPPED' if report['stop_reason'] else 'INCOMPLETE')
    checkpoint();write_json(out/f'report_attempt{attempt:03}.json',report)
    if complete:write_json(out/'report.json',report)
    print(report['status'],'requests',fetch.requests,'passed',sum(x['status']=='PASS' for x in states.values()))
    return 0 if complete else 1


if __name__=='__main__':raise SystemExit(main())
