"""Prospectively frozen C24/C41 chronological validation evaluator."""
import argparse, hashlib, json, math, os, sys
from collections import defaultdict
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(HERE))
for p in (ROOT,HERE):
    if p not in sys.path: sys.path.insert(0,p)
import run_rotation_long_h2_candidate as C24
import run_volatility_acceleration_c41 as C41

WINDOWS=(('2025-04-01T00:00:00Z','2025-09-30T00:00:00Z'),('2025-10-01T00:00:00Z','2026-03-31T00:00:00Z'),('2026-04-01T00:00:00Z','2026-09-28T00:00:00Z'))
DEV={'rotation_long_h2':dict(canon='474ba2eddf2e046bc4b8652fcd17b5182d396c20cb0e3537a721a8acb328ee66',file='39cbaa56a058473659f9001d857998f63a9c735f11030a28e0984cc609f3175f',mean=.17728,pf=1.463514,dd=18.599937),
     'volatility_acceleration_long_1h':dict(canon='a530ab55e1ab12607bec6ae85323d699139e0d2490f251ec6b8984de2615f69b',file='9ec03d496e58381b515d3a8c451d211549a5a7230e4c39cdecfd211062a0ff6c',mean=.033853,pf=1.052772,dd=43.682092)}
FAMILY=44; ALPHA=.05/FAMILY
class EvalError(RuntimeError):pass
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def load(path,key,canon=None,file=None):
 if file and sha(path)!=file:raise EvalError(f'{path}: file hash mismatch')
 with open(path,'rb') as f:d=json.loads(f.read())
 c=d.pop('packet_sha256',None); B=C24.B if key=='rotation_long_h2' else C41.B
 if c!=hashlib.sha256(B._canon(d)).hexdigest() or (canon and c!=canon):raise EvalError(f'{path}: canonical hash mismatch')
 d['packet_sha256']=c
 if key not in d or d.get('tree')!='clean':raise EvalError(f'{path}: wrong key or dirty tree')
 return d
def maxdd(rs):
 x=peak=dd=0.
 for r in rs:x+=r;peak=max(peak,x);dd=max(dd,peak-x)
 return dd
def metrics(trades):
 rs=[float(t['R']) for t in trades]; pn=[float(t['pnl']) for t in trades]; sy=defaultdict(float); rg=defaultdict(list); mo=defaultdict(float)
 for t,r in zip(trades,rs):sy[t['symbol']]+=r;rg[t['regime']].append(r);mo[t['t_out'][:7]]+=r
 wins=sum(x for x in pn if x>0);loss=-sum(x for x in pn if x<0);n=len(rs);k=sum(x>0 for x in mo.values());m=len(mo)
 p=sum(math.comb(m,i) for i in range(k,m+1))/(2**m) if m else 1.
 den=sum(abs(x) for x in sy.values());conc=max((abs(x)/den for x in sy.values()),default=1.)
 return dict(trades=n,symbols=len(sy),r_sum=sum(rs),r_mean=sum(rs)/n if n else None,pf=wins/loss if loss else None,max_dd_r=maxdd(rs),regimes={q:dict(trades=len(v),mean_r=sum(v)/len(v)) for q,v in sorted(rg.items())},months=m,positive_months=k,monthly_sign_p=p,max_symbol_abs_contribution=conc)
def evaluate(key,dev,folds):
 commits={x['commit'] for x in folds}
 if len(commits)!=1:raise EvalError(f'{key}: fold commits differ')
 for i,(d,w) in enumerate(zip(folds,WINDOWS),1):
  if (d['window']['start'],d['window']['end'])!=w or d['window'].get('sealed_cutoff')!='2026-09-28T00:00:00Z':raise EvalError(f'{key}: fold {i} window mismatch')
 fm=[metrics(d['trades']) for d in folds]; allm=metrics([t for d in folds for t in d['trades']])
 above=allm['regimes'].get('btc_above_ema200',{});below=allm['regimes'].get('btc_below_ema200',{})
 retention=allm['r_mean']/dev['mean']; dd_ratio=allm['max_dd_r']/dev['dd']; cap=min(1.,15/dev['dd'],15/allm['max_dd_r']) if allm['max_dd_r'] else min(1.,15/dev['dd'])
 gates={'aggregate_net_r_positive':allm['r_sum']>0,'aggregate_mean_r_positive':allm['r_mean']>0,'aggregate_pf_gt_1_05':allm['pf'] is not None and allm['pf']>1.05,'two_of_three_positive_folds':sum(x['r_sum']>0 and x['pf'] is not None and x['pf']>1 for x in fm)>=2,'sample_ge_200':allm['trades']>=200,'symbols_ge_8':allm['symbols']>=8,'regime_counts_ge_20':above.get('trades',0)>=20 and below.get('trades',0)>=20,'regime_means_ge_minus_0_05':above.get('mean_r',-99)>=-.05 and below.get('mean_r',-99)>=-.05,'symbol_concentration_le_35pct':allm['max_symbol_abs_contribution']<=.35,'mean_r_retention_ge_50pct':retention>=.5,'familywise_monthly_significance':allm['monthly_sign_p']<=ALPHA,'dd_not_extreme':allm['max_dd_r']<=60}
 return {'key':key,'fold_commit':next(iter(commits)),'folds':fm,'aggregate':allm,'degradation':{'mean_r_retention':retention,'pf_delta':allm['pf']-dev['pf'],'dd_ratio':dd_ratio},'risk_multiplier_cap':cap,'multiplicity':{'family':FAMILY,'alpha':ALPHA},'gates':gates,'isolated_paper_eligibility':'PASS' if all(gates.values()) else 'BLOCK'}
def main():
 ap=argparse.ArgumentParser()
 for x in ('rot_dev','rot_wf1','rot_wf2','rot_wf3','vol_dev','vol_wf1','vol_wf2','vol_wf3','out'):ap.add_argument('--'+x.replace('_','-'),required=True)
 a=ap.parse_args(); rd=load(a.rot_dev,'rotation_long_h2',DEV['rotation_long_h2']['canon'],DEV['rotation_long_h2']['file']);vd=load(a.vol_dev,'volatility_acceleration_long_1h',DEV['volatility_acceleration_long_1h']['canon'],DEV['volatility_acceleration_long_1h']['file'])
 rf=[load(x,'rotation_long_h2') for x in (a.rot_wf1,a.rot_wf2,a.rot_wf3)];vf=[load(x,'volatility_acceleration_long_1h') for x in (a.vol_wf1,a.vol_wf2,a.vol_wf3)]
 out={'format':'zb-c24-c41-extended-validation/1','sealed_holdout_opened':False,'results':[evaluate('rotation_long_h2',DEV['rotation_long_h2'],rf),evaluate('volatility_acceleration_long_1h',DEV['volatility_acceleration_long_1h'],vf)],'input_hashes':{p:sha(p) for p in (a.rot_dev,a.rot_wf1,a.rot_wf2,a.rot_wf3,a.vol_dev,a.vol_wf1,a.vol_wf2,a.vol_wf3)}}
 raw=json.dumps(out,sort_keys=True,indent=1,allow_nan=False).encode();out['report_sha256']=hashlib.sha256(raw).hexdigest();raw=json.dumps(out,sort_keys=True,indent=1,allow_nan=False).encode();os.makedirs(os.path.dirname(os.path.abspath(a.out)),exist_ok=True)
 if os.path.exists(a.out):raise EvalError('output exists')
 with open(a.out,'wb') as f:f.write(raw)
 print(*[(x['key'],x['isolated_paper_eligibility'],x['aggregate']['r_sum'],x['aggregate']['pf'],x['aggregate']['max_dd_r']) for x in out['results']],out['report_sha256'])
if __name__=='__main__':main()
