"""Bounded public-data-only workload probe. No engine/install/settings/credentials."""
import pathlib,sys,importlib.util,time,json,hashlib
root=pathlib.Path('F:/ZackBot2_data/R6-PAPER-01-REVIEW-20261114');sys.path.insert(0,str(root));sys.dont_write_bytecode=True
import r6_regime_flip as R,r6_paper_overlay as O
src=pathlib.Path('F:/ZackBot2_data/EXPLORATION-account4/source/binance_client.py')
spec=importlib.util.spec_from_file_location('r6_probe_client',src);BC=importlib.util.module_from_spec(spec);spec.loader.exec_module(BC)
c=BC.Futures('', '', BC.MAINNET)
assert c.base=='https://fapi.binance.com'
report={'head':'c337bed948573fffe62b345812735c380501150d','public_origin':c.base,'client_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'symbols_sha256':R.symbols_sha256(),'requests':[],'sleeps':[],'scope':'Standalone public market-data client only. No engine, account, credential, scheduler or slot. Does not measure delay inside a running engine lock.'}
start=time.monotonic()
def pause(s):report['sleeps'].append(s);time.sleep(s)
O.install_weight_pacer(c,sleep=pause)
def get(path,params):
    assert path in ('/fapi/v1/exchangeInfo','/fapi/v1/klines')
    t=time.monotonic();response,data=c._once('GET',c.base+path,params,False,timeout=15)
    row={'path':path,'symbol':params.get('symbol'),'elapsed_s':time.monotonic()-t,'http':response.status_code,'used_weight_1m':response.headers.get('X-MBX-USED-WEIGHT-1M')}
    report['requests'].append(row)
    if response.status_code!=200:raise RuntimeError(f'public_http_{response.status_code}: {data.get("code") if isinstance(data,dict) else "non-json"}')
    if row['used_weight_1m'] is None:raise RuntimeError('public_weight_header_missing')
    return data
try:
    info=get('/fapi/v1/exchangeInfo',{})
    listed={x['symbol'] for x in info['symbols'] if x.get('status')=='TRADING' and x.get('contractType')=='PERPETUAL'}
    missing=sorted(set(R.SYMBOLS)-listed);report['missing_public_trading_symbols']=missing
    if missing:raise RuntimeError('frozen_symbols_not_trading_on_public_data_origin')
    cutoff=int(time.time()//14400)*14400*1000-1
    import pandas as pd
    problems=[];cpu=0.
    for i,s in enumerate(R.SYMBOLS):
        if time.monotonic()-start>300:raise RuntimeError('bounded_probe_300s_deadline')
        raw=get('/fapi/v1/klines',{'symbol':s,'interval':'4h','limit':1500,'endTime':cutoff})
        closed=[x for x in raw if int(x[6])<time.time()*1000]
        d=pd.DataFrame({'t':pd.to_datetime([int(x[0]) for x in closed],unit='ms'),'c':[float(x[4]) for x in closed]})
        ok,why=R.frame_check(d,time.time())
        # The entry-window clause is expected outside a decision window; inspect history separately.
        if len(d)<R.R1_CONTIG_BARS or not d.t.is_unique or not d.t.is_monotonic_increasing or not d.t.iloc[-R.R1_CONTIG_BARS:].diff().iloc[1:].eq(R.STEP).all():problems.append(s)
        t=time.monotonic();R.raw_signals(d);cpu+=time.monotonic()-t
        report['requests'][-1].update(rows=len(closed),last_open_utc=str(d.t.iloc[-1]),raw_sha256=hashlib.sha256(json.dumps(raw,separators=(',',':')).encode()).hexdigest())
        if i%40==0:print('public-only symbols checked',i+1,flush=True)
    report.update(status='COMPLETE' if not problems else 'INCOMPLETE',history_problems=problems,raw_signal_compute_s=cpu,theoretical_4h_weight=1630,theoretical_max_d01_extra_weight=815)
except Exception as ex:
    report.update(status='INCOMPLETE',reason=str(ex),exception=type(ex).__name__)
finally:
    report['elapsed_s']=time.monotonic()-start;report['observed_at_utc']=pd.Timestamp.now(tz='UTC').isoformat() if 'pd' in globals() else None
    out=root/'public_workload_probe.json';out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k!='requests'},indent=2));print('requests',len(report['requests']))
