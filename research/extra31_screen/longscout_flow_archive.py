"""Verified official USD-M archive supplement for the frozen extra31 only.

Monthly archives are used only for fully present months; otherwise only fully
present UTC daily archives are requested. No account or authenticated API.
"""
import argparse
import calendar
import csv
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import longscout_fetch_flow_hourly as H

BASE='https://data.binance.vision/data/futures/um'
OUT=Path('C:/Dev/ZackBot_research_cache/longscout_codex_flow_archive_20261120')
CONTRACT=Path(__file__).with_name('contract.json')
HEADER=['open_time','open','high','low','close','volume','close_time','quote_volume','count','taker_buy_volume','taker_buy_quote_volume','ignore']


def plan(symbol,hours):
    if not re.fullmatch(r'[A-Z0-9]+',symbol):raise ValueError('invalid symbol')
    dates={datetime.fromtimestamp(t/1000,timezone.utc).date() for t in hours}
    months=sorted({(d.year,d.month) for d in dates});result=[]
    for y,m in months:
        days=calendar.monthrange(y,m)[1]
        allhours=[int(datetime(y,m,d,tzinfo=timezone.utc).timestamp()*1000)+h*H.HOUR for d in range(1,days+1) for h in range(24)]
        if all(t in hours for t in allhours):
            name=f'{symbol}-1h-{y:04}-{m:02}.zip'
            result.append((f'{BASE}/monthly/klines/{symbol}/1h/{name}',allhours))
        else:
            for d in sorted(x for x in dates if (x.year,x.month)==(y,m)):
                day=int(datetime(d.year,d.month,d.day,tzinfo=timezone.utc).timestamp()*1000)
                ts=list(range(day,day+H.DAY,H.HOUR))
                if not all(t in hours for t in ts):raise ValueError('partial UTC day in expected set')
                name=f'{symbol}-1h-{d.isoformat()}.zip'
                result.append((f'{BASE}/daily/klines/{symbol}/1h/{name}',ts))
    return result


def decode(zipbytes,checksum,filename,expected):
    try: line=checksum.decode('ascii').strip()
    except UnicodeError:raise ValueError('invalid checksum encoding')
    m=re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?([^\s/\\]+)',line)
    if not m or m.group(2)!=filename or H.digest(zipbytes)!=m.group(1).lower():raise ValueError('official checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(zipbytes)) as z:
        infos=z.infolist()
        if len(infos)!=1 or infos[0].is_dir() or not infos[0].filename.endswith('.csv') or infos[0].file_size>10_000_000 or z.testzip() is not None:
            raise ValueError('invalid ZIP contents or CRC')
        with z.open(infos[0]) as raw:
            rows=csv.reader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))
            if next(rows,None)!=HEADER:raise ValueError('unexpected archive CSV header')
            parsed=[]
            for row in rows:
                if len(row)!=12:raise ValueError('invalid archive CSV row')
                for i in (0,6,8):
                    if not re.fullmatch(r'\d+',row[i]):raise ValueError('invalid integer field')
                    row[i]=int(row[i])
                parsed.append(row)
    return H.validate(parsed,expected)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


def public_open(url,timeout):
    return urllib.request.build_opener(NoRedirect()).open(url,timeout=timeout)


class Downloader:
    def __init__(self,record,opener=public_open,now=lambda:datetime.now(timezone.utc),
                 monotonic=time.monotonic,sleep=time.sleep,requests=0):
        self.record=record;self.opener=opener;self.now=now;self.clock=monotonic;self.sleep=sleep
        self.requests=requests;self.last=None
    def get(self,url):
        if self.requests>=H.MAX_REQUESTS:raise H.StopJob('request cap')
        if self.last is not None:
            wait=1-(self.clock()-self.last)
            if wait>0:self.sleep(wait)
        if self.now().astimezone(H.DEADLINE.tzinfo)>=H.DEADLINE:raise H.StopJob('Cairo deadline')
        pattern=re.escape(BASE)+r'/(monthly|daily)/klines/([A-Z0-9]+)/1h/\2-1h-\d{4}-\d{2}(?:-\d{2})?\.zip(?:\.CHECKSUM)?'
        match=re.fullmatch(pattern,url)
        if not match or ((match.group(1)=='monthly') != bool(re.search(r'-\d{4}-\d{2}\.zip(?:\.CHECKSUM)?$',url))):
            raise ValueError('unapproved archive endpoint')
        n=self.requests+1;self.record(n,url);self.requests=n;self.last=self.clock()
        try:
            with self.opener(url,timeout=30) as response:
                data=response.read();weight=response.headers.get('X-MBX-USED-WEIGHT-1M')
                if weight is not None and int(weight)>=1500:self.sleep(60)
        except urllib.error.HTTPError as e:
            if 300<=e.code<400 or e.code in (403,418,429,451):raise H.StopJob('HTTP '+str(e.code)) from e
            raise
        return n,data


def verify_resume(out,contract,report,files,pool):
    if json.loads((out/'contract.json').read_text())!=contract:raise ValueError('archive contract differs')
    if report.get('status')=='ARCHIVE31_COMPLETE' or report.get('stop_reason') in ('HTTP 403','HTTP 418','HTTP 429','HTTP 451'):
        raise ValueError('completed release or access stop')
    if re.fullmatch(r'HTTP 3\d\d',str(report.get('stop_reason'))):raise ValueError('redirect stop')
    states=report.get('symbols');n=report.get('requests')
    if not isinstance(states,dict) or set(states)!=set(contract['extra31']) or type(n) is not int or not 0<=n<=H.MAX_REQUESTS:raise ValueError('invalid progress')
    attempts=sorted(p.name for p in out.glob('report_attempt*.json'))
    if attempts!=[f'report_attempt{i:03}.json' for i in range(1,len(attempts)+1)]:raise ValueError('attempt sequence')
    hashes=report.get('attempt_hashes',[])
    if len(hashes)!=len(attempts) or any(H.sha(out/name)!=h for name,h in zip(attempts,hashes)):
        raise ValueError('attempt report hash mismatch')
    expected_root={'contract.json','progress.json','raw','daily','requests',*attempts}
    if (out/'STOP').is_file():expected_root.add('STOP')
    if {p.name for p in out.iterdir()}!=expected_root:raise ValueError('unknown output files')
    ledger={}
    names=[f'{i:06}.json' for i in range(1,n+1)]
    if sorted(p.name for p in (out/'requests').iterdir())!=names:raise ValueError('request ledger mismatch')
    for i,name in enumerate(names,1):
        x=json.loads((out/'requests'/name).read_text())
        if x.get('sequence')!=i:raise ValueError('invalid request ledger')
        ledger[i]=x['url']
    reuse={};known_raw=set();known_daily=set()
    for s,state in states.items():
        if state.get('input_sha256')!=files[s] or state.get('status') not in ('NOT_RUN','RUNNING','STOPPED','FAILED','PASS'):raise ValueError('invalid state')
        expected=H.complete_hours(Path(pool)/(s+'_1h.csv')) if state.get('objects') or state.get('segments') or state['status']=='PASS' else None
        allowed={url:ts for url,ts in plan(s,expected)} if expected else {}
        objects={}
        for obj in state.get('objects',[]):
            seq=obj.get('sequence');url=obj.get('url');name=obj.get('file')
            if type(seq) is not int or ledger.get(seq)!=url or name!=f'{seq:06}.bin' or name in known_raw:raise ValueError('invalid object identity')
            if url.removesuffix('.CHECKSUM') not in allowed:raise ValueError('unexpected object URL')
            blob=(out/'raw'/name).read_bytes()
            if H.digest(blob)!=obj.get('sha256'):raise ValueError('object hash mismatch')
            known_raw.add(name);objects[seq]=(url,blob)
        valid={}
        for seg in state.get('segments',[]):
            url=seg.get('url');z=objects.get(seg.get('zip_sequence'));c=objects.get(seg.get('checksum_sequence'))
            if url not in allowed or not z or not c or z[0]!=url or c[0]!=url+'.CHECKSUM':raise ValueError('invalid segment identity')
            if seg.get('status')=='PASS':
                if url in valid:raise ValueError('duplicate passing segment')
                valid[url]=decode(z[1],c[1],url.rsplit('/',1)[-1],{t:expected[t] for t in allowed[url]})
            elif seg.get('status')!='RECEIVED':raise ValueError('invalid segment state')
        reuse[s]=valid
        if state['status']=='PASS':
            if set(valid)!=set(allowed):raise ValueError('passing symbol missing segment')
            daily=H.aggregate([r for url in allowed for r in valid[url]])
            path=out/'daily'/f'{s}.csv';known_daily.add(path.name)
            if H.sha(path)!=state.get('daily_sha256'):raise ValueError('daily hash mismatch')
            with path.open(newline='',encoding='utf-8') as f:rows=list(csv.reader(f))
            if rows!=[['t','trade_count','taker_buy_qv']]+[[str(v) for v in row] for row in daily] or len(daily)!=state.get('days'):
                raise ValueError('daily content mismatch')
    if {p.name for p in (out/'raw').iterdir()}!=known_raw or {p.name for p in (out/'daily').iterdir()}!=known_daily:raise ValueError('unknown raw or daily artifact')
    return reuse,len(attempts)+1


def main(argv=None):
    ap=argparse.ArgumentParser();ap.add_argument('--data-pool',required=True);ap.add_argument('--input-audit',required=True)
    ap.add_argument('--audit-sha256',required=True);ap.add_argument('--out',required=True);ap.add_argument('--resume',action='store_true')
    a=ap.parse_args(argv)
    if Path(a.out).resolve()!=OUT.resolve():raise SystemExit('dedicated archive folder required')
    files=H.load_contract(a.data_pool,a.input_audit,a.audit_sha256)
    frozen=json.loads(CONTRACT.read_text());extra=frozen['universe']['extra31']
    if len(extra)!=31 or len(set(extra))!=31 or not set(extra).issubset(files) or 'BTCUSDT' in extra:raise ValueError('invalid frozen extra31')
    contract=dict(source_sha256=H.sha(__file__),validator_sha256=H.sha(H.__file__),frozen_contract_sha256=H.sha(CONTRACT),
                  audit_sha256=a.audit_sha256,extra31=sorted(extra),source=BASE,deadline_cairo=H.DEADLINE.isoformat(),request_cap=H.MAX_REQUESTS)
    out=Path(a.out)
    if a.resume:
        report=json.loads((out/'progress.json').read_text());reuse,attempt=verify_resume(out,contract,report,files,a.data_pool)
        states=report['symbols'];report['status']='INCOMPLETE';report['stop_reason']=None
    else:
        out.mkdir(parents=False,exist_ok=False)
        for sub in ('raw','daily','requests'):(out/sub).mkdir()
        states={s:dict(status='NOT_RUN',input_sha256=files[s],objects=[],segments=[]) for s in sorted(extra)}
        report=dict(status='INCOMPLETE',requests=0,stop_reason=None,symbols=states);reuse={};attempt=1
        H.write_json(out/'contract.json',contract)
    report['attempt']=attempt
    def checkpoint():
        report['requests']=fetch.requests;tmp=out/'progress.tmp';H.write_json(tmp,report,'w');tmp.replace(out/'progress.json')
    def record(n,url):
        H.write_json(out/'requests'/f'{n:06}.json',dict(sequence=n,url=url));fetch.requests=n;checkpoint()
    fetch=Downloader(record,requests=report['requests']);checkpoint()
    for s in sorted(extra):
        if states[s]['status']=='PASS':continue
        states[s]['status']='RUNNING';checkpoint()
        try:
            expected=H.complete_hours(Path(a.data_pool)/(s+'_1h.csv'));records=[]
            for url,ts in plan(s,expected):
                if url in reuse.get(s,{}):records.extend(reuse[s][url]);continue
                pair=[]
                for target in (url+'.CHECKSUM',url):
                    if (out/'STOP').exists():raise H.StopJob('STOP file')
                    seq,blob=fetch.get(target);name=f'{seq:06}.bin'
                    with (out/'raw'/name).open('xb') as f:f.write(blob)
                    states[s]['objects'].append(dict(sequence=seq,url=target,file=name,sha256=H.digest(blob)))
                    checkpoint();pair.append((seq,blob))
                seg=dict(url=url,checksum_sequence=pair[0][0],zip_sequence=pair[1][0],status='RECEIVED')
                states[s]['segments'].append(seg);checkpoint()
                records.extend(decode(pair[1][1],pair[0][1],url.rsplit('/',1)[-1],{t:expected[t] for t in ts}))
                seg['status']='PASS';checkpoint()
            daily=H.aggregate(records);dest=out/'daily'/f'{s}.csv'
            with dest.open('x',newline='',encoding='utf-8') as f:
                w=csv.writer(f);w.writerow(['t','trade_count','taker_buy_qv']);w.writerows(daily)
            states[s].update(status='PASS',days=len(daily),daily_sha256=H.sha(dest));checkpoint()
        except H.StopJob as e:
            states[s].update(status='STOPPED',error=str(e));report['stop_reason']=str(e);break
        except (ValueError,TypeError,KeyError,OSError,urllib.error.URLError,zipfile.BadZipFile) as e:
            states[s].update(status='FAILED',error=f'{type(e).__name__}: {e}');checkpoint()
    complete=report['stop_reason'] is None and all(x['status']=='PASS' for x in states.values())
    report['status']='ARCHIVE31_COMPLETE' if complete else ('STOPPED' if report['stop_reason'] else 'INCOMPLETE')
    checkpoint();attempt_file=out/f'report_attempt{attempt:03}.json';H.write_json(attempt_file,report)
    report.setdefault('attempt_hashes',[]).append(H.sha(attempt_file));checkpoint()
    if complete:H.write_json(out/'report.json',report)
    print(report['status'],'requests',fetch.requests,'passed',sum(x['status']=='PASS' for x in states.values()))
    return 0 if complete else 1


if __name__=='__main__':raise SystemExit(main())
