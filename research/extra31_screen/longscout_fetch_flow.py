"""Acquire public daily USD-M flow fields, reconciling every day to pinned 1h data.

No authenticated endpoints. A pre-existing output directory is never reused.
The input audit hash and all 166 input file hashes are mandatory. A bounded
--max-symbols run is a probe and cannot report COMPLETE.
"""
import argparse
import csv
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

DAY=86_400_000
HOUR=3_600_000
URL='https://fapi.binance.com/fapi/v1/klines'
DEADLINE=datetime(2026,11,23,16,0,tzinfo=ZoneInfo('Africa/Cairo'))
EXPECTED_OUTPUT=Path('C:/Dev/ZackBot_research_cache/longscout_codex_flow_20261120')
MAX_REQUESTS=500


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def number(value):
    try: x=Decimal(str(value))
    except (InvalidOperation, ValueError): raise ValueError('invalid decimal')
    if not x.is_finite(): raise ValueError('nonfinite decimal')
    return x


def near(a,b,absolute=Decimal('0.000001')):
    a,b=number(a),number(b)
    return abs(a-b)<=max(absolute,max(abs(a),abs(b))*Decimal('1e-9'))


def hourly_days(path):
    """Return only full UTC days, requiring unique aligned 1h timestamps."""
    groups={}; seen=set()
    with Path(path).open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f)
        if not {'t','o','h','l','c','v','qv'}.issubset(reader.fieldnames or []): raise ValueError('input columns')
        for row in reader:
            t=int(row['t'])
            if t%HOUR or t in seen: raise ValueError('invalid or duplicate input hour')
            seen.add(t); day=t//DAY*DAY
            vals=[number(row[k]) for k in ('o','h','l','c','v','qv')]
            if min(vals[:4])<=0 or min(vals[4:])<0: raise ValueError('invalid input values')
            groups.setdefault(day,[]).append((t,vals))
    result={}
    for day,rows in groups.items():
        rows.sort()
        if [t for t,_ in rows] != list(range(day,day+DAY,HOUR)): continue
        v=[x for _,x in rows]
        result[day]=(v[0][0],max(x[1] for x in v),min(x[2] for x in v),v[-1][3],
                     sum((x[4] for x in v),Decimal(0)),sum((x[5] for x in v),Decimal(0)))
    if not result: raise ValueError('no complete days')
    return dict(sorted(result.items()))


def runs(days,limit=1000):
    """Split into contiguous requests so excluded partial days are never requested."""
    out=[]; current=[]
    for d in sorted(days):
        if current and (d!=current[-1]+DAY or len(current)>=limit):
            out.append(current); current=[]
        current.append(d)
    if current: out.append(current)
    return out


def validate(rows,expected):
    if not isinstance(rows,list) or len(rows)!=len(expected): raise ValueError('missing or extra daily rows')
    output=[]; prev=None
    for row,day in zip(rows,expected):
        if not isinstance(row,list) or len(row)!=12: raise ValueError('invalid kline field count')
        if type(row[0]) is not int or row[0]!=day or (prev is not None and row[0]<=prev): raise ValueError('timestamp mismatch')
        if type(row[6]) is not int or row[6]!=day+DAY-1: raise ValueError('daily close timestamp')
        vals=[number(row[i]) for i in (1,2,3,4,5,7)]
        if not all(near(a,b,Decimal('1e-12') if i<4 else Decimal('1e-6'))
                   for i,(a,b) in enumerate(zip(vals,expected[day]))): raise ValueError('OHLCV reconciliation mismatch')
        count=row[8]
        if type(count) is not int or count<0: raise ValueError('invalid trade count')
        taker=number(row[10]); taker_base=number(row[9]); base=vals[4]; qv=vals[5]
        if qv>0 and count==0: raise ValueError('positive quote volume with zero trades')
        if taker_base<0 or (taker_base>base and not near(taker_base,base)): raise ValueError('invalid taker base')
        if qv<0 or taker<0 or (taker>qv and not near(taker,qv)): raise ValueError('invalid taker quote')
        output.append((day,count,str(taker))); prev=day
    return output


class StopJob(Exception): pass


class Fetcher:
    def __init__(self, opener=urllib.request.urlopen, monotonic=time.monotonic, sleep=time.sleep,
                 now=lambda:datetime.now(timezone.utc)):
        self.opener=opener; self.monotonic=monotonic; self.sleep=sleep; self.now=now
        self.requests=0; self.last=None

    def get(self,symbol,days):
        if self.requests>=MAX_REQUESTS: raise StopJob('request cap')
        if self.last is not None:
            wait=1-(self.monotonic()-self.last)
            if wait>0: self.sleep(wait)
        if self.now().astimezone(ZoneInfo('Africa/Cairo'))>=DEADLINE: raise StopJob('Cairo deadline')
        q=urllib.parse.urlencode(dict(symbol=symbol,interval='1d',startTime=days[0],endTime=days[-1]+DAY-1,limit=len(days)))
        self.requests+=1; self.last=self.monotonic()
        try:
            with self.opener(URL+'?'+q,timeout=30) as response:
                body=response.read()
                weight=response.headers.get('X-MBX-USED-WEIGHT-1M')
                if weight is not None and int(weight)>=1500: self.sleep(60)
        except urllib.error.HTTPError as e:
            if e.code in (418,429): raise StopJob('HTTP '+str(e.code)) from e
            raise
        return body


def load_contract(pool,audit_path,audit_sha):
    if not re.fullmatch(r'[0-9a-f]{64}',audit_sha) or sha(audit_path)!=audit_sha: raise ValueError('audit SHA mismatch')
    audit=json.loads(Path(audit_path).read_text(encoding='utf-8'))
    files=audit['copied_files']
    if not isinstance(files,dict) or len(files)!=166 or 'BTCUSDT' not in files: raise ValueError('universe must be 165 plus BTC')
    expected={s+'_1h.csv' for s in files}
    if {p.name for p in Path(pool).glob('*_1h.csv')}!=expected: raise ValueError('pool universe differs from audit')
    for s,h in files.items():
        if not re.fullmatch(r'[A-Z0-9]+',s) or not re.fullmatch(r'[0-9a-f]{64}',h): raise ValueError('invalid audit entry')
        if sha(Path(pool)/(s+'_1h.csv'))!=h: raise ValueError(s+': input hash mismatch')
    return files


def exclusive_json(path,obj):
    with Path(path).open('x',encoding='utf-8') as f: json.dump(obj,f,indent=2,sort_keys=True)


def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument('--data-pool',required=True); ap.add_argument('--input-audit',required=True)
    ap.add_argument('--audit-sha256',required=True); ap.add_argument('--out',required=True)
    ap.add_argument('--max-symbols',type=int)
    a=ap.parse_args(argv)
    if Path(a.out).resolve()!=EXPECTED_OUTPUT.resolve(): raise SystemExit('output path must be the dedicated flow folder')
    if a.max_symbols is not None and not 1<=a.max_symbols<=166: raise SystemExit('max-symbols must be 1..166')
    files=load_contract(a.data_pool,a.input_audit,a.audit_sha256)
    out=Path(a.out); out.mkdir(parents=False,exist_ok=False)
    (out/'raw').mkdir(); (out/'daily').mkdir()
    fetch=Fetcher(); probe=a.max_symbols is not None
    statuses={s:{'status':'NOT_RUN','input_sha256':files[s]} for s in sorted(files)}
    contract=dict(audit_sha256=a.audit_sha256,source_sha256=sha(__file__),symbols=sorted(files),
                  endpoint=URL,limit=1000,deadline_cairo=DEADLINE.isoformat(),probe=probe)
    exclusive_json(out/'contract.json',contract)
    halted=None
    for s in sorted(files)[:a.max_symbols or len(files)]:
        try:
            expected=hourly_days(Path(a.data_pool)/(s+'_1h.csv'))
            records=[]; raw_hashes=[]
            for i,chunk in enumerate(runs(expected)):
                body=fetch.get(s,chunk)
                raw=out/'raw'/f'{s}_{i:03}.json'
                with raw.open('xb') as f: f.write(body)
                raw_hashes.append(dict(file=raw.name,sha256=sha(raw)))
                records.extend(validate(json.loads(body),{k:expected[k] for k in chunk}))
            dest=out/'daily'/f'{s}.csv'
            with dest.open('x',newline='',encoding='utf-8') as f:
                writer=csv.writer(f); writer.writerow(['t','trade_count','taker_buy_qv']); writer.writerows(records)
            statuses[s].update(status='PASS',days=len(records),raw=raw_hashes,daily_sha256=sha(dest))
        except StopJob as e:
            statuses[s].update(status='STOPPED',error=str(e)); halted=str(e); break
        except (ValueError,TypeError,KeyError,OSError,urllib.error.URLError) as e:
            statuses[s].update(status='FAILED',error=f'{type(e).__name__}: {e}')
    complete=not probe and halted is None and all(x['status']=='PASS' for x in statuses.values())
    report=dict(status='COMPLETE' if complete else ('STOPPED' if halted else 'INCOMPLETE'),
                probe=probe,requests=fetch.requests,stop_reason=halted,symbols=statuses)
    exclusive_json(out/'report.json',report)
    print(report['status'], 'requests', fetch.requests, 'passed',sum(x['status']=='PASS' for x in statuses.values()))
    return 0 if complete else 1


if __name__=='__main__': raise SystemExit(main())
