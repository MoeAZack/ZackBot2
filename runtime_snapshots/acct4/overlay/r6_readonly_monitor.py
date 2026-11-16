"""Durable, account4-only read-only canary evidence. No orders/settings/scheduler writes."""
import argparse,datetime,hashlib,json,math,os,subprocess,time,urllib.request,urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo
import r6_panel_client as C
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'runtime'/'ZackBot';OUT=ROOT/'r6_monitor';OUT.mkdir(exist_ok=True)
LEDGER=OUT/'state.json'; CAIRO=ZoneInfo('Africa/Cairo')
OPEN_KINDS={'entry','entry_market','entry_maker','entry_fallback','pyramid_add','safety_order'}
def now():return datetime.datetime.now(datetime.timezone.utc)
def cairo(t=None):return (t or now()).astimezone(CAIRO).isoformat()
def read(p,default):
    try:return json.loads(p.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default
def save(p,d):
    tmp=p.with_suffix(p.suffix+'.new');tmp.write_text(json.dumps(d,indent=2)+'\n',encoding='utf8');os.replace(tmp,p)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'MISSING'
def emit(kind,data):
    r={'at_cairo':cairo(),'kind':kind,**data}
    with (OUT/'events.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(r,default=str)+'\n')
    return r
def post(st,key,title,data):
    if key in st.setdefault('posted',[]):return
    body='<!-- agent: codex -->\nR6 CANARY MONITOR: '+title+'\n\n'+json.dumps(data,indent=2,default=str)+'\n\nRead-only evidence. No account, slot, capacity or schedule action. Funding research remains STOPPED. LANES: R6 forward observation; economic follow-up awaits the bounded review.\n'
    p=OUT/'post.txt';p.write_text(body,encoding='utf8')
    try:
        r=subprocess.run(['gh','issue','comment','219','--repo','MoeAZack/ZackBot2','--body-file',str(p)],capture_output=True,text=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if r.returncode:raise RuntimeError('GitHub publication unavailable')
        st['posted'].append(key);st.setdefault('pending_posts',{}).pop(key,None);emit('posted',{'event':key,'url':r.stdout.strip()})
    except Exception as ex:
        st.setdefault('pending_posts',{})[key]={'title':title,'data':data}
        emit('publication_pending',{'event':key,'error_type':type(ex).__name__})
def net_result(row,state):
    why=[]
    if row.get('fill_px_partial') or row.get('px_unmeasured'):why.append('fill_prices_incomplete')
    if row.get('fees_actual_partial') or row.get('fees_actual') is None:why.append('fees_incomplete')
    if row.get('funding_partial') or state.get('funding_err'):why.append('funding_incomplete')
    closed=datetime.datetime.fromisoformat(row['closed']).timestamp()*1000
    if float(state.get('funding_read_ms') or 0)<closed+120000:why.append('awaiting_post_close_funding_poll')
    # The native successful income poll is the observation basis; missing/unreadable coverage never becomes zero.
    funding=row.get('funding_actual')
    if funding is None:
        if row.get('funding_by_asset') or row.get('funding_rows'):why.append('funding_unpriced')
        elif 'funding_incomplete' not in why and 'awaiting_post_close_funding_poll' not in why:funding=0.0
    fills=row.get('fills') or [];refs={i:r for i,r in row.get('fill_refs',[])};actual=row.get('px_actual') or {};verified=set(row.get('px_unlinked_ok') or [])
    q=0.;avg=0.;gross=0.;en=[];ex=[];sd=1 if row['side']=='LONG' else -1
    for i,f in enumerate(fills):
        px=actual.get(refs.get(i))
        if px is None and i in verified:px=f[3]
        if px is None:why.append('fill_price_missing');continue
        n=float(f[2]);px=float(px)
        if f[1] in OPEN_KINDS:
            avg=(avg*q+px*n)/(q+n);q+=n;en.append((n,px))
        else:gross+=sd*(px-avg)*n;q-=n;ex.append((n,px))
    if not en or not ex or abs(q)>1e-6:why.append('fill_quantity_incomplete')
    risk=float(row.get('risk_usd') or 0)
    if risk<=0:why.append('risk_missing')
    if why:return {'status':'INCOMPLETE','why':sorted(set(why)),'net':None,'R':None}
    net=gross-float(row['fees_actual'])+float(funding)
    return dict(status='MEASURED_AS_OF_NATIVE_INCOME_POLL',entry=sum(n*p for n,p in en)/sum(n for n,p in en),exit=sum(n*p for n,p in ex)/sum(n for n,p in ex),gross=gross,fees=float(row['fees_actual']),funding=float(funding),net=net,R=net/risk,funding_read_ms=state['funding_read_ms'])
def expectation(row,entry_bar):
    reason=row.get('exit_reason');closed=datetime.datetime.fromisoformat(row['closed']);cut=int(closed.timestamp()//14400)*14400
    decision=datetime.datetime.fromtimestamp(cut-14400,datetime.timezone.utc)
    due=datetime.datetime.fromisoformat(entry_bar)+datetime.timedelta(hours=480)
    if due.tzinfo is None:due=due.replace(tzinfo=datetime.timezone.utc)
    out={'native_reason':reason,'canonical_deadline_due':decision>=due,'deadline_bar_open_cairo':cairo(due)}
    if reason=='stop':return {**out,'classification':'stop','research_comparison':'stop path; exact slippage/fill recorded separately'}
    if reason not in ('exit_signal','time_exit'):return {**out,'classification':'operator_or_other','research_comparison':'not a frozen-rule exit'}
    try:
        url='https://fapi.binance.com/fapi/v1/klines?'+urllib.parse.urlencode({'symbol':row['symbol'],'interval':'4h','limit':1500,'endTime':cut*1000-1})
        with urllib.request.urlopen(url,timeout=20) as f:bars=json.load(f)
        tail=bars[-768:];assert len(tail)==768 and all(int(b[0])-int(a[0])==14400000 for a,b in zip(tail,tail[1:]))
        daily={}
        for b in bars:
            t=datetime.datetime.fromtimestamp(int(b[0])/1000,datetime.timezone.utc)
            daily.setdefault(t.date(),[]).append((t.hour,float(b[4])))
        dc=[sorted(v)[-1][1] for _,v in sorted(daily.items()) if sorted(x[0] for x in v)==[0,4,8,12,16,20]];assert len(dc)>=120
        m=sum(dc[-100:])/100;old=sum(dc[-120:-20])/100
        regime=1 if dc[-1]>m and m>old else -1 if dc[-1]<m and m<old else 0
        leave=regime!=(1 if row['side']=='LONG' else -1)
        label='regime_leave_and_deadline' if leave and out['canonical_deadline_due'] else 'regime_leave' if leave else '120_bar_deadline' if out['canonical_deadline_due'] else 'UNEXPLAINED_EXIT'
        return {**out,'classification':label,'regime':regime,'research_comparison':'public closed-candle reconstruction; not an archived exact-runtime input replay'}
    except Exception as e:return {**out,'classification':'UNVERIFIED','error_type':type(e).__name__}
def protected_hashes():
    roots={'account1':Path('C:/Users/princ/AppData/Local/ZackBot'),'account2':Path('REDACTED_VALUE'),'account3':Path('REDACTED_VALUE')}
    return {a+'/'+n:digest(p/n) for a,p in roots.items() for n in ('settings.json','install.json','cohort.json')}
def tick(st):
    st['heartbeat_cairo']=cairo();st['mode']='READ_ONLY'
    for key,item in list(st.get('pending_posts',{}).items()):post(st,key,item['title'],item['data'])
    scan_log(st)
    s=C.api('/api/status',timeout=20);assert s['mode']=='PAPER'
    st.pop('observer_error',None)
    sl=next(x for x in s['settings']['SLEEVES'] if x['id']=='EX14');lots=[x for x in s['lots'] if x['sleeve']=='EX14']
    st.setdefault('entry_bars',{})
    state=read(DATA/'state.json',{})
    for key,l in state.get('lots',{}).items():
        if l.get('sleeve')=='EX14':st['entry_bars'][key]=l.get('r6_entry_bar') or datetime.datetime.fromisoformat(l['opened']).replace(minute=0,second=0,microsecond=0,hour=(datetime.datetime.fromisoformat(l['opened']).hour//4)*4).isoformat()
    invariant=sl['max_pos']==1 and abs(float(sl['risk'])-.024)<1e-9 and len(sl['symbols'])==163 and len(lots)<=1
    if not invariant:post(st,'invariant-'+hashlib.sha256(json.dumps(sl,sort_keys=True).encode()).hexdigest()[:12],'CANARY LIMIT CHANGED',{'max_pos':sl.get('max_pos'),'risk':sl.get('risk'),'symbols':len(sl.get('symbols',[])),'open_R6':len(lots)})
    other=[x for x in s['settings']['SLEEVES'] if x['id']!='EX14'];pin=hashlib.sha256(json.dumps(other,sort_keys=True).encode()).hexdigest()
    if pin!=st['other13_hash']:post(st,'other13-'+pin,'OTHER 13 SLEEVES CHANGED',{'fingerprint':pin})
    h=protected_hashes()
    for k,v in h.items():
        if v!=st['account_hashes'][k]:post(st,'account-file-'+k+v,'ACCOUNT 1–3 FILE CHANGED',{'file_scope':k,'old_sha256':st['account_hashes'][k],'new_sha256':v})
    for sym in ('XRPUSDT','ONEUSDT'):
        p=[{k:l.get(k) for k in ('sleeve','symbol','side','qty','stop','protected')} for l in s['lots'] if l['symbol']==sym]
        if p!=st['watched_positions'].get(sym):
            post(st,'position-'+sym+hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest(),'XRP/ONE POSITION CHANGE',{'symbol':sym,'before':st['watched_positions'].get(sym),'after':p,'note':'May be normal stop/partial management; no monitor trade action.'});st['watched_positions'][sym]=p
    cycle=s.get('health',{}).get('last_cycle_ok',{}).get('4h')
    due=cycle!=st.get('last_cycle')
    if due or time.time()-st.get('protection_checked_s',0)>=300 or any(not l.get('protected') for l in lots):
        proof=C.api('/api/testnet/r6/protection',{},timeout=25)['msg'];st['protection_checked_s']=time.time();st['last_protection']=proof['passed']
        save(OUT/'latest_protection.json',proof)
        if not proof['passed']:post(st,'protection-'+hashlib.sha256(json.dumps(proof['issues']).encode()).hexdigest(),'PROTECTION / OWNERSHIP ALERT',{'issues':proof['issues'],'R6_open':len(lots)})
    if due:
        sig=C.api('/api/signals',timeout=20)['signals'];ss={q:sig.get('EX14|'+q) for q in sl['symbols']}
        ct=datetime.datetime.fromisoformat(cycle); decision=(ct.replace(minute=0,second=0,microsecond=0,hour=(ct.hour//4)*4)-datetime.timedelta(hours=4)).strftime('%Y-%m-%d %H:%M:%S')
        valid=[q for q,v in ss.items() if v and v.get('time')==decision]
        missed=C.api('/api/missed',timeout=20).get('missed',[]);all_ref=[x for x in missed if x.get('sleeve')=='EX14' and x.get('candle')==decision];ref=[x for x in all_ref if x.get('code')=='max_positions']
        row=emit('cycle',{'cycle_ok_utc':cycle,'decision_candle_utc':decision,'symbols_evaluated':len(valid),'panel_entry_signal_snapshot':[q for q,v in ss.items() if q in valid and (v['le'] or v['se'])],'max1_refusals':sorted({x['symbol'] for x in ref}),'open_lots_observed':[dict(symbol=l['symbol'],side=l['side'],qty=l['qty']) for l in lots],'new_entries':[dict(symbol=l['symbol'],side=l['side'],qty=l['qty']) for l in lots if datetime.datetime.fromisoformat(l['opened'])>=ct.replace(minute=0,second=0,microsecond=0,hour=(ct.hour//4)*4)],'protection_passed':st.get('last_protection')})
        row['refused_decisions']=[{k:x.get(k) for k in ('symbol','side','code')} for x in all_ref]
        row['observed_entry_decision_count']=len({(x['symbol'],x['side']) for x in all_ref+row['new_entries']})
        row['signal_note']='Panel flags are an observation-time snapshot, not an archived raw-signal count; admission/refusal records preserve executed decisions.'
        save(OUT/('cycle_'+str(int(ct.timestamp()))+'.json'),row)
        if len(valid)!=163:post(st,'coverage-'+str(cycle),'SYMBOL COVERAGE ALERT',row)
        st['last_cycle']=cycle
    hist=C.api('/api/history',timeout=20).get('history',[])
    closed=[r for r in hist if r.get('sleeve')=='EX14']
    for r in closed:
        result=net_result(r,state);eid=r['id'];version=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
        if st.setdefault('closed_versions',{}).get(eid)==version:continue
        # Do not issue a stream of incomplete changes just because the latest successful poll timestamp advances.
        simple={k:v for k,v in result.items() if k!='funding_read_ms'};version=hashlib.sha256(json.dumps(simple,sort_keys=True).encode()).hexdigest()
        if st['closed_versions'].get(eid)==version:continue
        bar=st['entry_bars'].get(eid) or datetime.datetime.fromisoformat(r['opened']).replace(minute=0,second=0,microsecond=0,hour=(datetime.datetime.fromisoformat(r['opened']).hour//4)*4).isoformat()
        report={'symbol':r['symbol'],'side':r['side'],'opened_cairo':cairo(datetime.datetime.fromisoformat(r['opened'])),'closed_cairo':cairo(datetime.datetime.fromisoformat(r['closed'])),'booked_entry_pending_measurement':r.get('avg_entry',r.get('entry')),'booked_exit_pending_measurement':r.get('exit'),'result':result,'exit_expectation':expectation(r,bar)}
        emit('close',report);post(st,'close-'+eid+version,'EX14 CLOSE / COST REVISION',report);st['closed_versions'][eid]=version
    if len(closed)>=8:post(st,'checkpoint-8','OWNER REVIEW CHECKPOINT APPROACHING',{'closed_forward_trades':len(closed),'limit':'Do not authorize an extension beyond approximately 10 without moe. Monitoring never changes trade settings.'})
    if len(closed)>=10:post(st,'checkpoint-10','MOE REQUIRED BEFORE CANARY EXTENSION',{'closed_forward_trades':len(closed),'no_extension_authorized':True,'monitoring_only':True})
    save(LEDGER,st)

def scan_log(st):
    # Publish only a category and line fingerprint, never arbitrary log text.
    p=DATA/'bot.log';off=st.get('log_offset',0)
    if p.exists():
        if p.stat().st_size<off:off=0
        with p.open('rb') as f:f.seek(off);lines=f.read(262144);st['log_offset']=f.tell()
        for line in lines.decode('utf8','replace').splitlines():
            low=line.lower()
            if ('warning' in low or 'error' in low) and any(x in low for x in ('rate limit','rate-limit','429','418','candles ','data unavailable','fetch fail','klines')):
                fingerprint=hashlib.sha256(line.encode()).hexdigest();key='fetch-'+fingerprint
                emit('data_warning',{'log_line_sha256':fingerprint});post(st,key,'FETCH / RATE WARNING',{'log_line_sha256':fingerprint,'local_log':str(p)})
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--once',action='store_true');a=ap.parse_args()
    import msvcrt
    lock=(OUT/'monitor.lock').open('a+b');lock.seek(0);lock.write(b'1');lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:return
    st=read(LEDGER,{})
    if not st:
        s=C.api('/api/status',timeout=20)
        before=read(Path('REDACTED_VALUE'),{})
        assert len(before['sleeves'])==13
        st={'started_cairo':cairo(),'other13_hash':hashlib.sha256(json.dumps(before['sleeves'],sort_keys=True).encode()).hexdigest(),'account_hashes':protected_hashes(),'watched_positions':{sym:[{k:l.get(k) for k in ('sleeve','symbol','side','qty','stop','protected')} for l in s['lots'] if l['symbol']==sym] for sym in ('XRPUSDT','ONEUSDT')},'log_offset':(DATA/'bot.log').stat().st_size,'posted':[]}
    st['pid']=os.getpid();save(LEDGER,st)
    while True:
        try:tick(st)
        except Exception as ex:
            emit('observer_error',{'error_type':type(ex).__name__});st['heartbeat_cairo']=cairo();st['observer_error']=type(ex).__name__;save(LEDGER,st)
            post(st,'observer-error-'+type(ex).__name__,'OBSERVATION INCOMPLETE',{'error_type':type(ex).__name__,'action':'No trading action; retry at next check.'})
        if a.once:break
        time.sleep(300)
if __name__=='__main__':main()
