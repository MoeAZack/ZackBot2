import argparse,contextlib,hashlib,json,os,sys
H=os.path.dirname(os.path.abspath(__file__));R=os.path.dirname(os.path.dirname(H));sys.path[:0]=[R,H];import run_volume_pressure_inventory_candidate as V
KEY='long_legged_doji_short_1h';FORMAT='zb-long-legged-doji-short-c223/1';B=V.B
def run(**a):
 out=a.pop('out');tier=a.pop('tier');base=out+'.base';ok,of=V.KEY,V.FORMAT;V.KEY,V.FORMAT=KEY,FORMAT
 try:
  V.run(**a,tier=tier,side='short',out=base);p=json.load(open(base));p.pop('packet_sha256');e=p.pop('volume_pressure_inventory_1h');p[KEY]=e;x=p['stats']['aggregate'];g={'pre_cost_gross_positive':x['gross'] is not None and x['gross']>0,'mean_net_r_positive':x['r_mean'] is not None and x['r_mean']>0,'pf_gt_1_05':x['pf'] is not None and x['pf']>1.05,'sample_ge_100':x['trades']>=100,'symbols_ge_8':e['breadth']['symbols_with_trades']>=8,'each_regime_ge_20':e['regime_check']['ok'],'max_dd_le_15r':x['max_dd_r'] is not None and x['max_dd_r']<=15};e.update(format=FORMAT,hypothesis='A two-sided long-legged doji below the long trend identifies resolved indecision and downside continuation',stage_a_gates=g,stage_a_pass=all(g.values()),multiplicity={'numbered_candidate_family':223,'future_familywise_alpha':0.05/223},isolated_paper_eligibility='BLOCK');p['packet_sha256']=hashlib.sha256(B._canon(p)).hexdigest();os.makedirs(os.path.dirname(out),exist_ok=True);json.dump(p,open(out,'w'),sort_keys=True,indent=1);return p
 finally:
  V.KEY,V.FORMAT=ok,of
  with contextlib.suppress(FileNotFoundError):os.remove(base)
if __name__=='__main__':
 ap=argparse.ArgumentParser();[ap.add_argument('--'+n.replace('_','-'),required=True) for n in ('cache','regime_cache','funding_manifest','funding_store','window','out','tier')];q=run(**vars(ap.parse_args()));x=q['stats']['aggregate'];print(x,q[KEY]['stage_a_gates'],q['packet_sha256'])
