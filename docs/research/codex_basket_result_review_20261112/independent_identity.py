import os,sys,json,hashlib,collections,math
import pandas as pd
import basket as K
P=r'F:\ZackBot2_data\C1H-VENUE-RECHECK-20261112'; D=os.path.join(P,'inputs','cohort9_1h'); O=os.path.dirname(__file__)
def sha(p):
 with open(p,'rb') as f:return hashlib.sha256(f.read()).hexdigest()
manp=os.path.join(P,'input_manifest.json'); rp=os.path.join(P,'exchange_rules.json')
assert sha(manp)=='c11ed2566727c38d0d9236d7290a01aabb4a53a0f3b15388ea8fed7ba8909351'
assert sha(rp)=='d1ef2a9c60db9858774d68903648c334ca807a66c23dc85cce13fd8add96d2af'
man=json.load(open(manp))['cohort9_1h']
for s in K.COINS: assert sha(os.path.join(D,s+'.csv'))==man[s]['input_sha256']
rules=json.load(open(rp)); r1=K.load_1h(D); r4={s:K.to_4h(x) for s,x in r1.items()}; K.check_window(r1,r4); K.register_h5()
real=K.B.run; capture={}
def wrapped(*a,**kw):
 result=real(*a,**kw); capture.update(book=a[0],fills=list(kw['fill_log']),tr=result[0]); return result
K.B.run=wrapped
out={}; stats=[]; details=[]
for sl in K.SLEEVES:
 for cost in K.COSTS:
  for path in K.PATHS:
   raw=r1 if sl[3]=='1h' else r4
   lots,_,_=K.native_tape(raw,sl,cost,path,rules,sl[3]); key=f'{sl[0]}|{cost}|{path}'; out[key]=lots
   groups=collections.defaultdict(list)
   for f in capture['fills']: groups[(f['sym'],f['pos_i'])].append(f)
   trades={(r.sym,int(r.i_in)):r for r in capture['tr'].itertuples()}; errs=[]; qdiff=[]; parts=0; fixed8=0
   for k,t in trades.items():
    fs=groups[k]; e=next(f for f in fs if f['kind']=='entry'); exits=[f for f in fs if f['kind']=='exit']; parts+=len(exits)>1
    q=e['qty']; qty=q; fund=0.; exits_by_i=collections.defaultdict(list)
    for x in exits: exits_by_i[x['i']].append(x)
    closes=capture['book'].arr[k[0]]['c']; fpb=0.0001/8*(1 if sl[3]=='1h' else 4)
    for i in range(k[1],int(t.i_out)+1):
     fund += qty*closes[i]*fpb
     for x in exits_by_i[i]:
      before=qty-x['qty']; rounded=K.F.round_step(before,rules['symbols'][k[0]]['step']) if rules['symbols'][k[0]].get('step') else before
      if len(exits)>1 and abs(before-rounded)>1e-12: fixed8+=1
      qty=rounded
    fee=(cost-2)/10000; entry=e['ref']*(1+e['side']*0.0002)
    cash=-q*entry*fee
    for x in exits:
     px=x['ref']*(1-e['side']*0.0002); cash += e['side']*(px-entry)*x['qty']-x['qty']*px*fee
    err=(cash-fund)-float(t.pnl); errs.append(abs(err)); qdiff.append(abs(q-sum(x['qty'] for x in exits)))
    details.append(dict(tape=key,symbol=k[0],i_in=k[1],native_pnl=float(t.pnl),cash=cash,funding=fund,error=err,exit_count=len(exits)))
   stats.append(dict(tape=key,lots=len(lots),closed=len(trades),open=len(lots)-len(trades),partial=parts,fixed8_remainder_changes=fixed8,max_abs_error=max(errs,default=0),max_qty_difference=max(qdiff,default=0)))
   print(stats[-1],flush=True)
with open(os.path.join(O,'native_lots.json'),'w') as f:json.dump(out,f,default=str,separators=(',',':'))
with open(os.path.join(O,'independent_identity.json'),'w') as f:json.dump(dict(stats=stats,details=details,hashes={os.path.basename(p):sha(p) for p in [__file__,os.path.join(O,'basket.py'),os.path.join(O,'h5_hourly_long_pr338.py'),os.path.join(O,'backtest.py'),os.path.join(O,'strategies.py'),os.path.join(O,'feasibility.py'),os.path.join(O,'prot.py'),manp,rp,os.path.join(O,'native_lots.json')]}),f,indent=1)

