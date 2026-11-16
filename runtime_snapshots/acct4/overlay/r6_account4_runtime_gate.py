"""Bounded authenticated account-4 R6 staging/protection routes. No credentials returned."""
import json,time,math,hashlib
from pathlib import Path
from datetime import datetime,timezone
import r6_paper_overlay as O
import r6_regime_flip as R

ROOT=Path(__file__).resolve().parent
GATE=ROOT/'r6_canary_gate.json'
def checked(e,paused=False):
    if e.live or e.cfg.get('MODE')!='paper' or e.trade.base not in R.TESTNET_ORIGINS:
        raise ValueError('Account 4 PAPER/testnet required')
    if Path(e.dir).resolve()!=ROOT/'runtime'/'ZackBot':raise ValueError('Wrong data folder')
    if e.install_block():raise ValueError('Account 4 install identity is not confirmed')
    if paused and not e.S.get('ENTRIES_PAUSED'):raise ValueError('Entries must be paused')

def protection(e):
    checked(e)
    with e.lock:
        positions=e.trade.positions(critical=True,retry=False)
        if not isinstance(positions,dict):raise ValueError('Unknown exchange positions')
        hedge=e.trade.hedge_mode()
        lots=list(e.state['lots'].values())
        orders={s:e.trade.open_stop_orders(s,strict_algo=True,retry=False) for s in sorted({l['symbol'] for l in lots})}
        issues=[]; rows=[]; owned={}
        for l in lots:
            sym,side=l['symbol'],l['side'];qty=float(l['qty']);owned[(sym,side)]=owned.get((sym,side),0)+qty
            stop=next((x for x in orders[sym] if x['tag']==l.get('stop_id')),None)
            tol=max(float(e.rules[sym]['step'])*.51,1e-10)
            ok=bool(stop and stop.get('position_side')==side and stop.get('side')==('SELL' if side=='LONG' else 'BUY') and not stop.get('close_position') and stop.get('qty') is not None and abs(float(stop['qty'])-qty)<=tol and stop.get('type')=='STOP_MARKET' and stop.get('status') in ('NEW','ACCEPTED','PENDING_NEW'))
            if not ok:issues.append(l['sleeve']+'|'+sym+'|'+side+': stop mismatch')
            rows.append(dict(sleeve=l['sleeve'],symbol=sym,side=side,qty=qty,stop_price=l.get('stop'),stop=stop,verified=ok))
        for k in set(positions)|set(owned):
            tol=float(e.rules[k[0]]['step'])*.99 if k[0] in e.rules else 0
            if abs(float(positions.get(k,0))-owned.get(k,0))>tol:issues.append('|'.join(k)+': position ownership mismatch')
        for name in ('unconfirmed_entries','resting_entries','pending_entries'):
            if e.state.get(name):issues.append(name+' not empty')
        if not hedge:issues.append('Hedge mode is off')
        return dict(at_utc=datetime.now(timezone.utc).isoformat(),mode='PAPER',host=e.trade.base,hedge=bool(hedge),lots=rows,positions=[dict(symbol=k[0],side=k[1],qty=v) for k,v in positions.items()],issues=issues,passed=not issues)

def handle(app,path,body):
    if app.PORT!=8768:raise ValueError('Account 4 only')
    e=app.APP.engine
    checked(e)
    if path.endswith('/protection'):
        if body:raise ValueError('No parameters')
        return protection(e)
    if path.endswith('/drill-flat'):
        checked(e,True)
        with e.lock:
            pos=e.trade.positions(critical=True,retry=False)
            orders=e.trade.open_stop_orders('DOGEUSDT',strict_algo=True,retry=False)
            p={k[1]:v for k,v in pos.items() if k[0]=='DOGEUSDT'}
            return dict(passed=not p and not orders and not any(l['symbol']=='DOGEUSDT' for l in e.state['lots'].values()),positions=p,working_orders=orders)
    if path.endswith('/stage'):
        checked(e,True)
        with e.lock:
            O.add_sleeve(e,False)
        return dict(staged=True,enabled=False,symbols=len(R.SYMBOLS),paced=bool(getattr(e.data,'_r6_paced',False)))
    if path.endswith('/paused-cycle'):
        checked(e,True)
        with e.lock:
            sl=next(s for s in e.S['SLEEVES'] if s['id']=='EX14')
            if sl['enabled']:raise ValueError('EX14 must remain disabled during gate')
            before=set(e.state['lots']);t=time.monotonic()
            e.cycle('4h','R6 paused canary gate')
            sig={s:e.signals.get('EX14|'+s) for s in R.SYMBOLS}
            covered=[s for s,v in sig.items() if v and float(v.get('atr',0))>0 and float(v.get('close',0))>0]
            added=list(set(e.state['lots'])-before)
            proof=protection(e)
            receipt=dict(at_utc=datetime.now(timezone.utc).isoformat(),seconds=time.monotonic()-t,symbols_evaluated=len(covered),missing=sorted(set(R.SYMBOLS)-set(covered)),candle_opens=sorted({v['time'] for v in sig.values() if v}),new_lots=added,protection=proof,settings_sha256=hashlib.sha256(Path(e.F['settings']).read_bytes()).hexdigest())
            receipt['passed']=len(covered)==163 and not added and proof['passed'] and e.S.get('ENTRIES_PAUSED') and not sl['enabled']
            GATE.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
            return receipt
    if path.endswith('/enable'):
        checked(e,True)
        with e.lock:
            old=json.loads(GATE.read_text())
            if not old.get('passed') or time.time()-datetime.fromisoformat(old['at_utc']).timestamp()>1800:raise ValueError('Fresh successful paused cycle required')
            if old['settings_sha256']!=hashlib.sha256(Path(e.F['settings']).read_bytes()).hexdigest():raise ValueError('Settings changed after paused gate')
            proof=protection(e)
            if not proof['passed']:raise ValueError('Protection gate failed')
            sl=next(s for s in e.S['SLEEVES'] if s['id']=='EX14')
            why=O.slot_problem(sl) or O.risk_problem(e,sl,e.equity())
            if why:raise ValueError(why)
            O.set_enabled(e,True)
            return dict(enabled=True,max_pos=sl['max_pos'],risk_budget_usd=e.last_eq*sl['share']*sl['risk'],protection=proof)
    if path.endswith('/drill-size'):
        checked(e,True)
        with e.lock:
            if any(l['symbol']=='DOGEUSDT' for l in e.state['lots'].values()):raise ValueError('DOGE already held; choose a separate drill contract')
            d=e.candles('DOGEUSDT','4h');atr=float(d.atr.iloc[-1]);px=float(e.trade.marks()['DOGEUSDT']);r=e.rules['DOGEUSDT'];eq=e.equity()
            q=max(float(r['min_qty']),math.ceil(float(r['min_notional'])/px/float(r['step']))*float(r['step']))
            pct=max(.01,math.ceil(q*3*atr/eq*100*1.2/.01)*.01)
            if eq*pct/100>8:raise ValueError('Minimum drill size would exceed $8')
            return dict(symbol='DOGEUSDT',side='LONG',risk_percent=pct,risk_budget_usd=eq*pct/100,stop_atr=3,minimum_notional=r['min_notional'])
    raise ValueError('Unknown bounded R6 route')
