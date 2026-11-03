"""Run C100 through the audited CORE-20 1h actual-funding Stage-A path."""
import argparse,contextlib,hashlib,json,os,sys
HERE=os.path.dirname(os.path.abspath(__file__));ROOT=os.path.dirname(os.path.dirname(HERE))
for p in (ROOT,HERE):
    if p not in sys.path:sys.path.insert(0,p)
import run_volume_pressure_inventory_candidate as V
KEY='demarker_recovery_long_1h';FORMAT='zb-demarker-c100-envelope/1';B,R=V.B,V.R
class C100Error(RuntimeError):pass
def gates(pkt):
 a,e=pkt['stats']['aggregate'],pkt[KEY];return {'pre_cost_gross_positive':float(a.get('gross') or 0)>0,'mean_net_r_positive':float(a.get('r_mean') or 0)>0,'pf_gt_1_05':a.get('pf') is not None and float(a['pf'])>1.05,'sample_ge_100':int(a.get('trades') or 0)>=100,'symbols_ge_8':int(e['breadth']['symbols_with_trades'])>=8,'each_regime_ge_20':bool(e['regime_check']['ok']),'max_dd_le_15r':float(a.get('max_dd_r') or float('inf'))<=15.0}
def run(*,cache,regime_cache,funding_manifest,funding_store,window,tier,out):
 if os.path.exists(out):raise C100Error('output exists')
 base=out+'.audited-base';ok,of=V.KEY,V.FORMAT;V.KEY,V.FORMAT=KEY,FORMAT
 try:
  V.run(cache=cache,regime_cache=regime_cache,funding_manifest=funding_manifest,funding_store=funding_store,window=window,tier=tier,side='long',out=base)
  with open(base,'rb') as f:pkt=json.loads(f.read())
  c=pkt.pop('packet_sha256')
  if c!=hashlib.sha256(B._canon(pkt)).hexdigest():raise C100Error('base hash mismatch')
  e=pkt.pop('volume_pressure_inventory_1h');e.update(format=FORMAT,hypothesis='A classic DeMarker recovery above 0.30 identifies a causal shift from downside to upside incremental-extrema pressure that persists after realistic costs');pkt[KEY]=e;e['stage_a_gates']=gates(pkt);e['stage_a_pass']=all(e['stage_a_gates'].values());e['multiplicity']={'numbered_candidate_family':100,'future_familywise_alpha':0.05/100};e['isolated_paper_eligibility']='BLOCK';pkt['packet_sha256']=hashlib.sha256(B._canon(pkt)).hexdigest();os.makedirs(os.path.dirname(out),exist_ok=True)
  with open(out+'.tmp','wb') as f:f.write(json.dumps(pkt,sort_keys=True,indent=1,allow_nan=False).encode());f.flush();os.fsync(f.fileno())
  os.replace(out+'.tmp',out);return pkt
 finally:
  V.KEY,V.FORMAT=ok,of
  for p in (base,out+'.tmp'):
   with contextlib.suppress(FileNotFoundError):os.remove(p)
def main():
 ap=argparse.ArgumentParser()
 for n in ('cache','regime_cache','funding_manifest','funding_store','window','out'):ap.add_argument('--'+n.replace('_','-'),required=True)
 ap.add_argument('--tier',required=True);a=ap.parse_args();p=run(**vars(a));x,e=p['stats']['aggregate'],p[KEY];print(f"{KEY}: {x['trades']} trades/{e['breadth']['symbols_with_trades']} symbols, R {x['r_sum']}, PF {x['pf']}, DD {x['max_dd_r']}, stage_a_pass {e['stage_a_pass']}, gates {e['stage_a_gates']}, packet {p['packet_sha256']} -> {a.out}")
if __name__=='__main__':main()
