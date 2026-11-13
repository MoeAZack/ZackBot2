"""Offline CLOCK-1021 injection/parity review. No real account, key, network or runtime imported."""
import ast,collections,hashlib,importlib.util,itertools,json,logging,pathlib,threading,types
ROOT=pathlib.Path(r'F:\ZackBot2_data\CLOCK-1021-REVIEW-20261113')
OUT=ROOT/'independent_r1';OUT.mkdir(exist_ok=True)
def module(name,folder):
    sp=importlib.util.spec_from_file_location(name,ROOT/folder/'binance_client.py');m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m
BASE=module('clock_base','base');HEAD=module('clock_head_r1','head_r1')
class Clock:
    def __init__(self):self.now=1_000_000.;self.sleeps=[]
    def time(self):return self.now
    def monotonic(self):return self.now
    def sleep(self,x):self.sleeps.append(x);self.now+=x
class Response:
    def __init__(self,status,data):self.status_code=status;self.data=data;self.headers={};self.content=b'fixture'
    def json(self):return self.data
def client(m,events,scripted_path=None):
    clock=Clock();m.time=clock;m.random=types.SimpleNamespace(random=lambda:.25);m.new_cid=lambda prefix='zb':prefix+'-FIXTURE-ID'
    calls=[];syncs=[];events=list(events)
    def request(method,url,params=None,timeout=None):
        path=url.split('example.invalid')[-1];p=dict(params or {});calls.append((method,path,p));clock.now+=.025
        if path.endswith('bookTicker'):return Response(200,{'bidPrice':'99','askPrice':'101'})
        event=events.pop(0) if events else 'ok'
        if event=='net':raise m.requests.ConnectionError('fixture network failure')
        if event=='ok':return Response(200,{'orderId':77,'algoId':88,'status':'FILLED','avgPrice':'100','executedQty':'1'})
        if event=='stringok':return Response(200,{'code':'200','msg':'success'})
        if event=='busy':return Response(503,{'code':-1001,'msg':'fixture busy'})
        if event=='rate':return Response(429,{'code':-1003,'msg':'fixture rate limit'})
        if event=='business':return Response(400,{'code':-2010,'msg':'fixture business refusal'})
        if event=='fallback':return Response(400,{'code':-4120,'msg':'Use algo endpoint'})
        if event=='ahead':return Response(400,{'code':-1021,'msg':'Timestamp was ahead of the server time'})
        return Response(400,{'code':-1021,'msg':'Timestamp outside recvWindow'})
    def get(url,timeout=None):
        clock.now+=.05;syncs.append(timeout);return Response(200,{'serverTime':int((clock.now+.3)*1000)})
    c=m.Futures.__new__(m.Futures);c.key='fixture';c.secret=b'fixture-only';c.base='https://example.invalid';c.offset=300;c.rw=6000;c.last_ok=0
    c.s=types.SimpleNamespace(request=request,get=get,headers={});c._synced_at=clock.now
    c._health=m.ExchangeHealth(clock=clock.monotonic);c._audit=dict(lock=threading.Lock(),s=c.s,until=0.)
    return c,clock,calls,syncs

checks={}
for action in ['stop','close','algo_stop']:
    events=(['fallback'] if action=='algo_stop' else [])+['clock']*3+['ok']
    c,clock,calls,syncs=client(HEAD,events)
    if action=='close':value=c.close('ADAUSDT','LONG','1',cid='CLOSE-FIXTURE')
    else:value=c.stop('ADAUSDT','LONG','1','99',cid='STOP-FIXTURE',acid='ALGO-FIXTURE')
    rows=[(m,p,q) for m,p,q in calls if not (q.get('type')=='STOP_MARKET' and action=='algo_stop' and p=='/fapi/v1/order')]
    windows=[q['recvWindow'] for _,_,q in rows]
    ids=[q.get('newClientOrderId') or q.get('clientAlgoId') for _,_,q in rows]
    assert windows==[6000,10000,20000,30000] and len(set(ids))==1
    assert len(syncs)==6
    checks[action]=dict(requests=len(rows),windows=windows,same_client_id=len(set(ids))==1,sync_timeouts=syncs,definitive_success=True)
for action in ['open','maker']:
    c,clock,calls,syncs=client(HEAD,['clock']*5)
    try:
        if action=='open':c.open('ADAUSDT','LONG','1',cid='ENTRY-FIXTURE')
        else:c._order(dict(symbol='ADAUSDT',side='BUY',positionSide='LONG',type='LIMIT',timeInForce='GTX',quantity='1',price='99',newClientOrderId='MAKER-FIXTURE'),adds_risk=True)
    except HEAD.ClockRejected:pass
    else:raise AssertionError('Entry did not exhaust its one retry')
    assert len(calls)==2 and [r[2]['recvWindow'] for r in calls]==[6000,6000] and len(syncs)==2
    assert len({r[2].get('newClientOrderId') for r in calls})==1
    checks[action]=dict(requests=len(calls),windows=[r[2]['recvWindow'] for r in calls],same_client_id=True,exception='ClockRejected',lookups=sum(r[0]=='GET' for r in calls))

# Invoke the actual maker method, isolated from the rest of Engine, with our real fake-transport Futures.
engine_source=(ROOT/'head_r1/engine.py').read_text(encoding='utf-8-sig');tree=ast.parse(engine_source)
klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Engine')
names={'_clock_alert','_replace_stop','_market_close','_maker_place','notify','_prov_set','_clock_ok'}
nodes=[n for n in klass.body if isinstance(n,ast.FunctionDef) and n.name in names]
ns=dict(time=HEAD.time,log=logging.getLogger('offline_clock_review'),BinanceError=HEAD.BinanceError,AmbiguousOrder=HEAD.AmbiguousOrder,
        new_cid=lambda prefix='zb':prefix+'-FIXTURE-ID',STOP_MISSING_NOTES={},_cid_of=lambda x:x)
exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'pinned_engine_methods','exec'),ns)
class E:
    CLOCK_NOTIFY_S=600;BOOK_PROBE_URGENT=('stop_failed',)
for k in names:setattr(E,k,ns[k])
def engine(c):
    e=E();e.trade=c;e.dry=False;e.rules={'ADAUSDT':{'step':1.,'tick':.01}};e.health={'incidents':{}}
    e.state={'lots':{}};e._rd=lambda x,step:round(x,8);e._fmt=lambda x,step:str(x);e.clock=lambda:1e6
    def err(message,key=None):
        if key:e.health['incidents'][key]={'open':True,'msg':message}
    e.err=err;e.notify=lambda msg:None;e.persist_block=lambda:None;e._save_wal=lambda:None
    e.save_state=lambda:None;e._stop_fired_guard=lambda lot:None;e._book_probe=lambda s:None
    e._exec_of=lambda o,q,s:float(o['executedQty']);e._fill_px=lambda o:float(o['avgPrice']);e._book_snapshot=lambda *a,**k:None
    e._fill=lambda *a,**k:{};e._apply_close=lambda lot,q,px,why,**kw:lot.update(qty=lot['qty']-q) or 0
    e.resolve=lambda key,note:e.health['incidents'][key].update(open=False)
    e._stopv_due=lambda *a:None;e._stopv_last={};e._cancel_or_park=lambda *a:None
    return e
c,clock,calls,syncs=client(HEAD,['clock']*3);ns['time']=clock;e=engine(c)
rec=dict(symbol='ADAUSDT',side='LONG',qty=1.,filled=0.,n=0,cost=0.)
e._maker_place(rec)
posts=[row for row in calls if row[0]=='POST'];assert len(posts)==2 and all(r[2]['recvWindow']==6000 for r in posts)
checks['actual_engine_maker']=dict(post_attempts=2,windows=[r[2]['recvWindow'] for r in posts],same_client_id=len({r[2]['newClientOrderId'] for r in posts})==1,status=rec['status'])

def lot():return dict(symbol='ADAUSDT',sleeve='FIX',side='LONG',qty=1.,avg=100.,stop=99.,stop_id=None,stop_dirty=False)
c,clock,calls,syncs=client(HEAD,[]);ns['time']=clock;e=engine(c);l=lot()
def refused(*a,**k):raise HEAD.ClockRejected(-1021,'fixture exhausted')
c.stop=refused
class FailedThread:
    def __init__(self,*a,**k):pass
    def start(self):raise RuntimeError('fixture thread start failed')
ns['threading']=types.SimpleNamespace(Thread=FailedThread)
e.S={'TELEGRAM_ON':True,'TELEGRAM_CHAT':'fixture'};e.cfg={'TELEGRAM_TOKEN':'fixture-only-not-a-real-token'};e.live=False
e.notify=types.MethodType(ns['notify'],e)
try:e._replace_stop(l)
except RuntimeError as ex:alert_throw={'exception':str(ex),'stop_dirty':l['stop_dirty'],'returned_false':False}
else:alert_throw={'stop_dirty':l['stop_dirty'],'returned_false':True}
assert alert_throw['stop_dirty'] and alert_throw['returned_false']
checks['alert_failure_contained_safety_state_preserved']=alert_throw
c,clock,calls,syncs=client(HEAD,[]);ns['time']=clock;e=engine(c);l=lot();c.close=refused
try:e._market_close(l,1.,'stop_failed')
except HEAD.ClockRejected:pass
assert e.health['incidents']['clock-1021']['open']
c.close=lambda *a,**k:dict(status='FILLED',executedQty='1',avgPrice='100')
e._market_close(l,1.,'stop_failed')
assert l['qty']==0 and not e.health['incidents']['clock-1021']['open']
checks['close_only_incident_recovers']={'closed_qty':1,'remaining_qty':l['qty'],'incident_open':False}
c,clock,calls,syncs=client(HEAD,[]);ns['time']=clock;e=engine(c);c.stop=refused
e._send=lambda fn,*a,**k:fn(*a,**k)
unconfirmed={'symbol':'ADAUSDT','side':'LONG','plan':{'px':100.,'stop_dist':1.},'prov':None}
e._prov_set('FIX-UNCONFIRMED',unconfirmed,1.)
assert e.health['incidents']['clock-1021']['open']
c.stop=types.MethodType(HEAD.Futures.stop,c)
e._prov_set('FIX-UNCONFIRMED',unconfirmed,1.)
assert unconfirmed['prov'] and not e.health['incidents']['clock-1021']['open']
checks['provisional_stop_clock_alert_and_recovery']={'clock_incident_raised':True,'resolved_on_recovery':True,'protected':True}

# Both 3-second /time samples and a 3-second signed response fit the declared transport timeouts.
# The current deadline check is before the next resync, not after it.
c,clock,calls,syncs=client(HEAD,['clock']*4);orig_get=c.s.get;orig_req=c.s.request;sent=[]
def slow_get(*a,**k):clock.now+=2.95;return orig_get(*a,**k)
def slow_req(*a,**k):sent.append(clock.now);clock.now+=2.975;return orig_req(*a,**k)
c.s.get=slow_get;c.s.request=slow_req
try:c.close('ADAUSDT','LONG','1',cid='DEADLINE-FIXTURE')
except HEAD.ClockRejected:pass
after_first=[round(t-(sent[0]+3.),3) for t in sent[1:]]
assert max(after_first)<=20
checks['late_clock_retry_not_sent']={'retry_start_seconds_after_first_rejection':after_first,'declared_deadline_seconds':20,'last_retry_started_after_deadline':False}

# Legacy parity: primary request/retry traces, outcome and sleeps on fresh-offset clients, no -1021.
# Periodic refresh is separately intentional; it necessarily adds /time reads when the offset is stale.
events=['ok','stringok','business','busy','rate','net'];parity_count=0;differences=[]
def run(m,seq,method,signed,retry,critical):
    c,clock,calls,syncs=client(m,seq)
    try:
        value=c._req(method,'/fapi/v2/account',{'fixture':1},signed=signed,retry=retry,critical=critical)
        outcome=('return',value)
    except Exception as ex:outcome=(type(ex).__name__,str(ex))
    trace=[(a,b,{k:v for k,v in q.items() if k!='signature'}) for a,b,q in calls]
    return outcome,trace,clock.sleeps,syncs
for length in range(1,5):
    for seq in itertools.product(events,repeat=length):
        for method in ['GET','DELETE','POST']:
            for signed in [False,True]:
                for retry in [False,True]:
                    for critical in [False,True]:
                        a=run(BASE,seq,method,signed,retry,critical);b=run(HEAD,seq,method,signed,retry,critical);parity_count+=1
                        if a!=b and len(differences)<10:differences.append(dict(seq=seq,method=method,signed=signed,retry=retry,critical=critical,base=a,head=b))
assert not differences,differences
checks['legacy_non1021_parity']={'cases':parity_count,'differences':0,'scope':'Fresh offset; outcome, primary request fields except signature, retry count and sleeps. Periodic stale sync is an intentional new side effect.'}

base_src=(ROOT/'base/engine.py').read_text(encoding='utf-8-sig')
def method_src(s,name):
    t=ast.parse(s);c=next(n for n in t.body if isinstance(n,ast.ClassDef) and n.name=='Engine');n=next(n for n in c.body if isinstance(n,ast.FunctionDef) and n.name==name);return ast.get_source_segment(s,n)
assert method_src(base_src,'_create_lot')==method_src(engine_source,'_create_lot')
checks['create_lot_identical']={'sha256':hashlib.sha256(method_src(engine_source,'_create_lot').encode()).hexdigest(),'identical':True}

# Alert failures must not replace the definitive close refusal.
c,clock,calls,syncs=client(HEAD,[]);ns['time']=clock;e=engine(c);l=lot()
original_error=HEAD.ClockRejected(-1021,'fixture original close refusal')
def close_refusal(*a,**k):raise original_error
def alert_failure(*a,**k):raise RuntimeError('fixture alert backend unavailable')
c.close=close_refusal;e.err=alert_failure;e.notify=alert_failure
try:e._market_close(l,1.,'stop_failed')
except HEAD.ClockRejected as caught:assert caught is original_error
else:raise AssertionError('Original close exception lost')
checks['close_original_exception_preserved_despite_alert_errors']=True
# A transient-retry wait remains independent of the extra clock retry budget.
c,clock,calls,syncs=client(HEAD,['clock','rate','ok']);base_req=c.s.request;send_times=[]
def rate_wait(*a,**k):
    send_times.append(clock.now);response=base_req(*a,**k)
    if response.status_code==429:response.headers['Retry-After']='30'
    return response
c.s.request=rate_wait
c._req('DELETE','/fapi/v1/order',{'symbol':'ADAUSDT','origClientOrderId':'FIX-CANCEL'},signed=True)
elapsed=[round(t-(send_times[0]+.025),3) for t in send_times[1:]]
assert elapsed[-1]>20
checks['mixed_transient_latency_qualification']={'retry_start_seconds_after_first_clock_rejection':elapsed,'outcome':'success','meaning':'Existing network/rate retry budget can extend total call duration; 20+15s is not a universal bound.'}

result={'head':'6e259ea5acf2379fe09bff933d7c5b05cfaa269e','base':'9cb4341f2d94c21434492515b8e85f2b42c148ce','offline_only':True,'checks':checks}
(OUT/'independent_result.json').write_text(json.dumps(result,indent=2,default=str))
print(json.dumps(result,indent=2))
