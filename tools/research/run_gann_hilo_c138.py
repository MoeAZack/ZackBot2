"""C138 audited CORE-20 actual-funding Stage-A."""
import argparse,contextlib,hashlib,json,os,sys
HERE=os.path.dirname(os.path.abspath(__file__));ROOT=os.path.dirname(os.path.dirname(HERE));sys.path[:0]=[ROOT,HERE]
import run_volume_pressure_inventory_candidate as V
KEY='gann_hilo_long_1h';FORMAT='zb-gann-hilo-c138-envelope/1';B=V.B
def gates(p):
 a,e=p['stats']['aggregate'],p[KEY];return {'pre_cost_gross_positive':float(a.get('gross')or 0)>0,'mean_net_r_positive':float(a.get('r_mean')or 0)>0,'pf_gt_1_05':a.get('pf')is not None and float(a['pf'])>1.05,'sample_ge_100':int(a.get('trades')or 0)>=100,'symbols_ge_8':int(e['breadth']['symbols_with_trades'])>=8,'each_regime_ge_20':bool(e['regime_check']['ok']),'max_dd_le_15r':float(a.get('max_dd_r')or 1e9)<=15}
def run(*,cache,regime_cache,funding_manifest,funding_store,window,tier,out):
 if os.path.exists(out):raise RuntimeError('output exists')
 base=out+'.base';ok,of=V.KEY,V.FORMAT;V.KEY,V.FORMAT=KEY,FORMAT
 try:
  V.run(cache=cache,regime_cache=regime_cache,funding_manifest=funding_manifest,funding_store=funding_store,window=window,tier=tier,side='long',out=base);p=json.load(open(base));h=p.pop('packet_sha256');assert h==hashlib.sha256(B._canon(p)).hexdigest();e=p.pop('volume_pressure_inventory_1h');e.update(format=FORMAT,hypothesis='A causal bullish Gann HiLo state switch identifies trend onset that survives realistic costs');p[KEY]=e;e['stage_a_gates']=gates(p);e['stage_a_pass']=all(e['stage_a_gates'].values());e['multiplicity']={'numbered_candidate_family':138,'future_familywise_alpha':.05/138};e['isolated_paper_eligibility']='BLOCK';p['packet_sha256']=hashlib.sha256(B._canon(p)).hexdigest();os.makedirs(os.path.dirname(out),exist_ok=True);json.dump(p,open(out+'.tmp','w'),sort_keys=True,indent=1,allow_nan=False);os.replace(out+'.tmp',out);return p
 finally:
  V.KEY,V.FORMAT=ok,of
  for x in (base,out+'.tmp'):
   with contextlib.suppress(FileNotFoundError):os.remove(x)
def main():
 ap=argparse.ArgumentParser();[ap.add_argument('--'+n.replace('_','-'),required=True) for n in ('cache','regime_cache','funding_manifest','funding_store','window','out')];ap.add_argument('--tier',required=True);a=ap.parse_args();p=run(**vars(a));x,e=p['stats']['aggregate'],p[KEY];print(KEY,x['trades'],e['breadth']['symbols_with_trades'],x['r_sum'],x['pf'],x['max_dd_r'],e['stage_a_pass'],p['packet_sha256'])
if __name__=='__main__':main()
