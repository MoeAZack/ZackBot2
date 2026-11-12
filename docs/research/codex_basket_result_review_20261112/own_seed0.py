from pathlib import Path
import json,hashlib,collections
import numpy as np,pandas as pd
P=Path(__file__).parent;N=Path('F:/ZackBot2_data/BASKET-NATIVE-IDENTITY-20261112/native_lots.json')
raw=json.loads(N.read_text());start=pd.Timestamp('2024-11-17 12:00');end=pd.Timestamp('2026-09-28');split=pd.Timestamp('2025-10-01');half=start+(end-start)/2
names=['BEAR','EMA','HOT','H5'];caps=dict(BEAR=2,EMA=2,HOT=2,H5=1);books={'OLD3':names[:3],'H5':['H5'],'COMBINED':names};prices={}
for s in ['ADAUSDT','AVAXUSDT','BNBUSDT','BTCUSDT','DOGEUSDT','ETHUSDT','LINKUSDT','SOLUSDT','XRPUSDT']:
 d=pd.read_csv(Path('F:/ZackBot2_data/C1H-VENUE-RECHECK-20261112/inputs/cohort9_1h')/(s+'.csv'));prices[s]=dict(zip(pd.to_datetime(d.t),d.c))
hours=pd.date_range(start,end,freq='h');results=[]
def simulate(lots,book,cb,path):
 cand=collections.defaultdict(list)
 for l in lots:
  l=dict(l);l['tin']=pd.Timestamp(l['t_in']);l['tout']=pd.Timestamp(l['t_out']);l['exit_at']=collections.defaultdict(list)
  for x in l['exits']:l['exit_at'][pd.Timestamp(x['t'])].append(x)
  cand[l['tin']].append(l)
 cash=4000.;active={};rec={};refused=collections.Counter();phantom=0.;day=None;daystart=cash;daycash=0.;halt=False;seq=0;peakcash=cash;cdd=0.;peakmark=cash;mdd=0.;first10=None;riskstat=[];grossstat=[];utilstat=[];fee=(cb-2)/1e4
 def cashflow(amount,l,funding=False):
  nonlocal cash,daycash,halt,peakcash,cdd
  cash+=amount;daycash+=amount;rec[l['id']]['net']+=amount
  if funding:rec[l['id']]['funding']+=amount
  halt=halt or daycash<=-.03*daystart
  peakcash=max(peakcash,cash);cdd=max(cdd,1-cash/peakcash)
 for tm in hours:
  cd=tm.tz_localize('UTC').tz_convert('Africa/Cairo').date()
  if cd!=day:day=cd;daystart=cash;daycash=0.;halt=False
  if tm>start:
   for item in sorted(list(active.values()),key=lambda x:x['rank']):
    l=item['lot'];q=item['q'];cashflow(-q*prices[l['sym']][tm-pd.Timedelta(hours=1)]*.0001/8,l,True)
    for x in l['exit_at'].get(tm,[]):
     xp=x['ref']*(1-l['side']*.0002);cashflow(l['side']*(xp-l['e_fill'])*x['qty']-xp*x['qty']*fee,l);item['q']-=x['qty']
    if l['closed'] and l['tout']==tm:del active[l['id']]
   up=sum(v['lot']['side']*(prices[v['lot']['sym']][tm-pd.Timedelta(hours=1)]-v['lot']['e_fill'])*max(v['q'],0) for v in active.values())
   marked=cash+up;peakmark=max(peakmark,marked);dd=1-marked/peakmark;mdd=max(mdd,dd)
   if first10 is None and dd>=.1:first10=tm.isoformat()
   rr=sum(v['lot']['risk_usd'] for v in active.values());gg=sum(max(v['q'],0)*prices[v['lot']['sym']][tm-pd.Timedelta(hours=1)] for v in active.values());riskstat.append(rr);grossstat.append(gg);utilstat.append(rr/(.025*cash) if cash>0 else 0)
  for l in sorted(cand.get(tm,[]),key=lambda x:(names.index(x['sleeve']),x['sym'],x['id'])):
   sl=l['sleeve'];reason=''
   if cash<=0:reason='nonpositive_equity'
   elif halt:reason='cairo_daily_loss_halt'
   elif sum(v['lot']['sleeve']==sl for v in active.values())>=caps[sl]:reason='sleeve_position_cap'
   elif any(v['lot']['sleeve']==sl and v['lot']['sym']==l['sym'] for v in active.values()):reason='own_coin_slot'
   elif sum(v['lot']['risk_usd'] for v in active.values())+l['risk_usd']>.025*cash+1e-9:reason='aggregate_entry_risk_cap'
   rec[l['id']]={'id':l['id'],'status':'refused' if reason else 'admitted','net':0.,'funding':0.,'t':l['tin'],'closed':l['closed'],'sleeve':sl,'sym':l['sym']}
   if reason:refused[reason]+=1;phantom+=(min(l['tout'],end)-tm).total_seconds()/3600;continue
   seq+=1;active[l['id']]={'lot':l,'q':l['qty'],'rank':seq};cashflow(-l['qty']*l['e_fill']*fee,l)
 adm=[r for r in rec.values() if r['status']=='admitted'];up=sum(v['lot']['side']*(prices[v['lot']['sym']][end-pd.Timedelta(hours=1)]-v['lot']['e_fill'])*max(v['q'],0) for v in active.values());net=cash+up-4000
 row={'book':book,'cost':cb,'path':path,'seed':0,'terminal_marked_equity':cash+up,'net_usd':net,'net_pct':net/40,'closed_cash':cash,'open_mark':up,'closes':sum(r['closed'] for r in adm),'admitted':len(adm),'entries_per_week':len(adm)/((end-start).total_seconds()/604800),'marked_dd_pct':mdd*100,'closed_dd_pct':cdd*100,'refused':sum(refused.values()),'phantom_hours':phantom,'funding_usd':sum(r['funding'] for r in adm),'early_usd':sum(r['net'] for r in adm if r['t']<split),'later_usd':sum(r['net'] for r in adm if r['t']>=split),'half1_usd':sum(r['net'] for r in adm if r['t']<half),'half2_usd':sum(r['net'] for r in adm if r['t']>=half),'avg_open_risk':float(np.mean(riskstat)),'peak_open_risk':max(riskstat),'avg_gross_notional':float(np.mean(grossstat)),'peak_gross_notional':max(grossstat),'avg_risk_utilization':float(np.mean(utilstat)),'blocked':dict(refused)}
 if cb==15 and book=='COMBINED':pd.DataFrame([{**r,'t':str(r['t'])} for r in rec.values()]).to_csv(P/f'own_records_{path}.csv',index=False)
 return row
for cb in [7,15,23]:
 for path in ['path','worst']:
  for book,sl in books.items():
   lots=[x for s in sl for x in raw[f'{s}|{cb}|{path}']];results.append(simulate(lots,book,cb,path))
pd.DataFrame(results).to_csv(P/'own_seed0_cells.csv',index=False)
ref=pd.read_csv(P/'cells.csv');ref=ref[ref.seed==0];errors=[];maxerr=0
for r in results:
 z=ref[(ref.book==r['book'])&(ref.cost==r['cost'])&(ref.path==r['path'])].iloc[0]
 for k,v in r.items():
  if isinstance(v,(float,int)):
   e=abs(float(v)-float(z[k]));maxerr=max(maxerr,e)
   if e>1e-7:errors.append({'book':r['book'],'cost':r['cost'],'path':r['path'],'metric':k,'own':v,'ref':float(z[k]),'error':e})
proof={'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'native_lots_sha256':hashlib.sha256(N.read_bytes()).hexdigest(),'matched_rows':len(results),'max_abs_metric_error':maxerr,'mismatches':errors,'author_replay_imported':False}
(P/'own_seed0_comparison.json').write_text(json.dumps(proof,indent=2));print(json.dumps(proof),flush=True);print(pd.DataFrame(results).query('cost==15')[['book','path','net_usd','net_pct','refused','phantom_hours']].to_string(index=False),flush=True)
