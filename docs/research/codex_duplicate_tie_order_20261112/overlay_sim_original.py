import csv,json,argparse,math
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from collections import defaultdict

def stamp(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if not d.tzinfo: raise ValueError('UTC offset required')
 return d.astimezone(timezone.utc)

def simulate(rows,order,policy,risk_mode,initial=4000,tie='exit-first',multiple=1.5,funding_bps=0):
 if funding_bps<0 or not math.isfinite(funding_bps): raise ValueError('funding stress must be finite and nonnegative')
 if initial<=0 or len(order)!=len(set(order)): raise ValueError('invalid initial equity or order')
 idx={s:i for i,s in enumerate(order)}; events=[]; seen=set()
 for row in rows:
  r=dict(row); u=r['uniqueid']; s=r['sleeve']; r['side']=int(r['side']); r['net_R_at_cost']=float(r['net_R_at_cost']); a=stamp(r['entry_utc']); b=stamp(r['exit_utc'])
  if u in seen or s not in idx or r['side'] not in (-1,1) or a>=b: raise ValueError('invalid candidate '+u)
  seen.add(u)
  r['risk_fraction']=float(r.get('risk_fraction') or .004); r['max_positions']=int(r.get('max_positions') or 2)
  if not math.isfinite(r['risk_fraction']) or r['risk_fraction']<=0 or r['max_positions']<=0: raise ValueError('invalid sleeve limits: '+u)
  if funding_bps and (not r.get('initial_notional_per_risk') or float(r['initial_notional_per_risk'])<=0 or not math.isfinite(float(r['initial_notional_per_risk']))): raise ValueError('positive initial_notional_per_risk required for funding stress: '+u)
  for k,t in [('entry',a),('exit',b)]:
   priority=(0 if k=='exit' else 1) if tie=='exit-first' else (0 if k=='entry' else 1)
   events.append((t,priority,idx[s],r['symbol'],u,k,r))
 events.sort(key=lambda e:e[:5]); eq=peak=initial; maxdd=0; first=None; active={}; records={}; curve=[]; day=None; start=initial; daily=0; halt=False; annual=defaultdict(float); sleeve=defaultdict(float); weekly=defaultdict(lambda:{'closes':0,'pnl':0.})
 for t,_,_,_,u,k,r in events:
  local=t.astimezone(ZoneInfo('Africa/Cairo'))
  if local.date()!=day: day=local.date(); start=eq; daily=0; halt=False
  if k=='exit':
   if u not in active: continue
   lot=active.pop(u); settlements=math.ceil(stamp(r['exit_utc']).timestamp()/28800)-math.ceil(stamp(r['entry_utc']).timestamp()/28800); funding=lot['risk']*float(r.get('initial_notional_per_risk') or 0)*funding_bps/10000*settlements; pnl=lot['risk']*r['net_R_at_cost']-funding; eq+=pnl; daily+=pnl
   if start>0 and daily<=-.03*start: halt=True
   peak=max(peak,eq); dd=(peak-eq)/peak; maxdd=max(maxdd,dd)
   if first is None and dd>=.10: first=t.isoformat()
   annual[str(local.year)]+=pnl; sleeve[r['sleeve']]+=pnl; w=local.date().isocalendar(); wk=f'{w.year}-W{w.week:02}'; weekly[wk]['closes']+=1; weekly[wk]['pnl']+=pnl; records[u].update(net_realized=pnl,exit_equity=eq,funding_debit=funding,funding_settlements=settlements,net_R_after_funding=pnl/lot['risk']); curve.append({'time_utc':t.isoformat(),'equity':eq,'closed_drawdown':dd,'uniqueid':u}); continue
  normal=(initial if risk_mode=='fixed' else eq)*r['risk_fraction']; risk=normal; same=[v for v in active.values() if v['symbol']==r['symbol'] and v['side']==r['side']]; other=[v for v in same if v['sleeve']!=r['sleeve']]; reason=''
  if eq<=0 or normal<=0: reason='nonpositive_equity'
  elif halt: reason='cairo_daily_loss_halt'
  elif sum(v['sleeve']==r['sleeve'] for v in active.values()) >= r['max_positions']: reason='sleeve_position_cap'
  elif any(v['sleeve']==r['sleeve'] and v['symbol']==r['symbol'] for v in active.values()): reason='own_coin_slot'
  elif policy=='A' and other: reason='other_sleeve_same_coin_side'
  elif policy=='B':
   risk=min(normal,max(0,multiple*normal-sum(v['risk'] for v in same)))
   if risk<.25*normal-1e-12: reason='duplicate_budget_below_25pct'
  if not reason and sum(v['risk'] for v in active.values())+risk>.025*eq+1e-12: reason='aggregate_entry_risk_cap'
  records[u]={'uniqueid':u,'sleeve':r['sleeve'],'symbol':r['symbol'],'side':r['side'],'entry_utc':r['entry_utc'],'exit_utc':r['exit_utc'],'status':'refused' if reason else 'admitted','reason':reason,'normal_risk':normal,'entry_risk':0 if reason else risk,'joined_other':bool(other),'net_R_at_cost':r['net_R_at_cost'],'net_realized':0}
  if not reason: active[u]={'risk':risk,'sleeve':r['sleeve'],'symbol':r['symbol'],'side':r['side']}
 joins=[r for r in records.values() if r['status']=='admitted' and r['joined_other']]; nonjoins=[r for r in records.values() if r['status']=='admitted' and not r['joined_other']]; yearly={}; base=initial
 for y,p in sorted(annual.items()): yearly[y]={'start_equity':base,'pnl':p,'return':p/base if base>0 else None}; base+=p
 return {'summary':{'initial_equity':initial,'terminal_equity':eq,'max_closed_equity_drawdown':maxdd,'first_10pct_closed_drawdown_utc':first,'drawdown_10pct_enforced':False,'drawdown_basis':'closed equity only; unrealized and intrabar unavailable','admitted':sum(r['status']=='admitted' for r in records.values()),'refused':sum(r['status']=='refused' for r in records.values()),'joined_other_count':len(joins),'joined_other_average_R':sum(r['net_R_after_funding'] for r in joins)/len(joins) if joins else None,'nonjoined_other_count':len(nonjoins),'nonjoined_other_average_R':sum(r['net_R_after_funding'] for r in nonjoins)/len(nonjoins) if nonjoins else None,'yearly':yearly,'sleeve_contribution':dict(sleeve),'weekly_closes':dict(weekly),'policy':policy,'risk_mode':risk_mode,'tie':tie,'budget_multiple':multiple,'funding_stress_bps_per8h':funding_bps,'funding_timing':'charged at close as conservative proxy; not at each settlement'},'records':list(records.values()),'curve':curve}

def main():
 p=argparse.ArgumentParser(); p.add_argument('input'); p.add_argument('--sleeve-order',required=True); p.add_argument('--policy',choices=['baseline','A','B'],required=True); p.add_argument('--risk-mode',choices=['equity','fixed'],required=True); p.add_argument('--tie',choices=['exit-first','entry-first'],default='exit-first'); p.add_argument('--budget-multiple',type=float,choices=[1.5,2],default=1.5); p.add_argument('--funding-stress-bps-per8h',type=float,default=0); p.add_argument('--initial',type=float,default=4000); p.add_argument('--output',required=True); a=p.parse_args()
 with open(a.input,newline='',encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
 result=simulate(rows,a.sleeve_order.split(','),a.policy,a.risk_mode,a.initial,a.tie,a.budget_multiple,a.funding_stress_bps_per8h)
 with open(a.output,'w',encoding='utf-8') as f: json.dump(result,f,indent=2,allow_nan=False)
if __name__=='__main__': main()


