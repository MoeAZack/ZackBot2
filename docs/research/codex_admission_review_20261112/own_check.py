from pathlib import Path
import sys,json,ast,hashlib,collections
import numpy as np,pandas as pd
P=Path(__file__).parent;E=Path('F:/ZackBot2_data/BASKET-NATIVE-IDENTITY-20261112');sys.path.insert(0,str(E))
import backtest as B,strategies as S,feasibility as F,h5_hourly_long_pr338 as H5
S.STRATEGIES[H5.KEY]=H5.RESEARCH
raw1={};prices={};D=Path('F:/ZackBot2_data/C1H-VENUE-RECHECK-20261112');man=json.loads((D/'input_manifest.json').read_text())['cohort9_1h'];rules=json.loads((D/'exchange_rules.json').read_text());start=pd.Timestamp('2024-11-17 12:00');end=pd.Timestamp('2026-09-28');split=pd.Timestamp('2025-10-01');half=start+(end-start)/2
assert hashlib.sha256((D/'input_manifest.json').read_bytes()).hexdigest()=='c11ed2566727c38d0d9236d7290a01aabb4a53a0f3b15388ea8fed7ba8909351'
for s in sorted(man):
 f=D/'inputs/cohort9_1h'/(s+'.csv');assert hashlib.sha256(f.read_bytes()).hexdigest()==man[s]['input_sha256'];d=pd.read_csv(f);d.t=pd.to_datetime(d.t);d=d[d.t<end];raw1[s]=d;prices[s]=dict(zip(d.t,d.c))
raw4={}
for s,d in raw1.items():
 q=d.set_index('t');g=q.resample('4h');v=g.agg({'o':'first','h':'max','l':'min','c':'last','v':'sum','qv':'sum'});raw4[s]=v[g.size()==4].reset_index()
# Use our separately authored and already verified ledger; never import author controls/replay.
t=ast.parse(Path('F:/ZackBot2_data/BASKET-RESULT-REVIEW-20261112/own_seed0.py').read_text());t.body=[n for n in t.body if isinstance(n,ast.FunctionDef) and n.name=='simulate'];env=dict(Path=Path,np=np,pd=pd,collections=collections,start=start,end=end,split=split,half=half,hours=pd.date_range(start,end,freq='h'),prices=prices,names=['BEAR','EMA','HOT','H5'],caps={'H5':1,'EMA':2},P=P);exec(compile(t,'own_validated_ledger','exec'),env)
orig=F.risk_qty
fixed=lambda m,r,px,atr,sd:dict(orig(m,8.,px,atr,sd))
def rq(m,r,px,atr,sd):
 z=fixed(m,r,px,atr,sd);z['qty']=min(z['qty'],3333.33/px);return z
F.risk_qty=rq;rows=[];proofs=[];identity_errors=[]
try:
 for name,key,raw,tf,cap in [('H5',H5.KEY,raw1,1,1),('EMA','ema_st',raw4,4,2)]:
  for path in ['path','worst']:
   book=B.Book(raw);fills=[];B.FEE=.0013;B.SLIP=.0002;cfg={'key':key,'share':1.,'risk':.002,'max_pos':cap,'sides':'long','symbols':sorted(man)}
   trades,cv=B.run(book,[cfg],start=4000.,max_lev=10.,daily_halt=.03,t0=start,t1=end,warmup=220,fund_per_bar=.0001/8*tf,pessimistic=path,exchange_rules=rules,fill_log=fills,risk_rules={'open_risk_cap':{'mode':'enforce','pct':2.5}})
   grouped=collections.defaultdict(list)
   for f in fills:grouped[(f['sym'],f['pos_i'])].append(f)
   closed={(r.sym,int(r.i_in)):r for r in trades.itertuples()};lots=[];T=pd.to_datetime(book.t)
   for (s,ii),fs in grouped.items():
    entry=[f for f in fs if f['kind']=='entry'];assert len(entry)==1;e=entry[0];sd=e['side'];q=e['qty'];ep=e['ref']*(1+sd*.0002);ex=[f for f in fs if f['kind']=='exit'];yes=(s,ii) in closed;last=int(closed[(s,ii)].i_out) if yes else len(T)-1
    funding=0.;remain=q
    for j in range(ii,last+1):
     funding+=remain*book.arr[s]['c'][j]*(.0001/8*tf);remain-=sum(x['qty'] for x in ex if x['i']==j)
    cash=-ep*q*.0013
    for x in ex:
     xp=x['ref']*(1-sd*.0002);cash+=sd*(xp-ep)*x['qty']-xp*x['qty']*.0013
    if yes:identity_errors.append(abs(cash-funding-float(closed[(s,ii)].pnl)))
    lot={'sleeve':name,'sym':s,'side':sd,'t_in':str(T[ii]),'t_out':str(T[last]+pd.Timedelta(hours=tf) if yes else end),'e_fill':ep,'qty':q,'stop_dist':e['stop_dist'],'risk_usd':q*e['stop_dist'],'closed':yes,'id':f'{name}|{s}|{T[ii].isoformat()}','exits':[{'ref':x['ref'],'qty':x['qty'],'t':str(T[x['i']]+pd.Timedelta(hours=tf))} for x in ex]};lots.append(lot)
   row=env['simulate'](lots,name,15,path);row['sleeve']=name;row['order']='alpha';row['native_final_marked_equity']=float(cv.iloc[-1]);rows.append(row)
   ids={l['id'] for l in lots};ov=pd.read_csv(Path('F:/ZackBot2_data/BASKET-RESULT-REVIEW-20261112')/f'own_records_{path}.csv');base=set(ov.loc[(ov.sleeve==name)&(ov.status=='admitted'),'id']);cmp=pd.read_csv(P/'overlay_compare.csv');claim=cmp[(cmp.sleeve==name)&(cmp.cost==15)&(cmp.path==path)].iloc[0]
   got={'sleeve':name,'path':path,'admitted':len(ids),'overlay_admitted':len(base),'common':len(ids&base),'newly_admitted':sorted(ids-base),'missed':sorted(base-ids)};got['ids_match_claim']=got['newly_admitted']==json.loads(claim.newly_admitted) and got['missed']==json.loads(claim.missed);proofs.append(got)
   # No guard effect claimed without a separate native rerun.
   uf=[];B.run(book,[cfg],start=4000.,max_lev=10.,daily_halt=1.,t0=start,t1=end,warmup=220,fund_per_bar=.0001/8*tf,pessimistic=path,exchange_rules=rules,fill_log=uf);uid={f'{name}|{f["sym"]}|{T[f["pos_i"]].isoformat()}' for f in uf if f['kind']=='entry'};row['guard_changed_entries']=len(uid^ids)
finally:F.risk_qty=orig
ref=pd.read_csv(P/'controls.csv');errors=[];largest=0
for r in rows:
 z=ref[(ref.sleeve==r['sleeve'])&(ref.cost==15)&(ref.path==r['path'])&(ref.order=='alpha')].iloc[0]
 for k,v in r.items():
  if k not in z.index or not isinstance(v,(float,int)):continue
  delta=abs(float(v)-float(z[k]));largest=max(largest,delta)
  if delta>1e-7:errors.append({'sleeve':r['sleeve'],'path':r['path'],'metric':k,'error':delta,'own':v,'ref':float(z[k])})
proof={'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'rows':len(rows),'max_metric_error':largest,'mismatches':errors,'max_native_identity_error':max(identity_errors),'closed_lot_identities':len(identity_errors),'overlay_comparisons':proofs,'imports_author_controls':False,'guard_changed_entries':[r['guard_changed_entries'] for r in rows]}
pd.DataFrame(rows).to_csv(P/'own_alpha15_rows.csv',index=False);(P/'independent_proof.json').write_text(json.dumps(proof,indent=2));print(json.dumps(proof),flush=True)
