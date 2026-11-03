"""Run candidate 41 through the audited 1h actual-funding path."""
import argparse, contextlib, hashlib, json, os, sys
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(HERE))
for p in (ROOT,HERE):
    if p not in sys.path: sys.path.insert(0,p)
import manifest as M  # noqa: E402
import run_volume_pressure_inventory_candidate as V  # noqa: E402
KEY='volatility_acceleration_long_1h'; FORMAT='zb-volatility-acceleration-c41-envelope/1'; B,R=V.B,V.R
class VolatilityAccelerationC41Error(RuntimeError): pass
def development_gates(pkt):
    a,e=pkt['stats']['aggregate'],pkt[KEY]
    return {'pre_cost_gross_positive':float(a.get('gross') or 0)>0,'mean_net_r_positive':float(a.get('r_mean') or 0)>0,
      'pf_gt_1_05':a.get('pf') is not None and float(a['pf'])>1.05,'sample_ge_100':int(a.get('trades') or 0)>=100,
      'symbols_ge_8':int(e['breadth']['symbols_with_trades'])>=8,'max_dd_le_15r':a.get('max_dd_r') is not None and float(a['max_dd_r'])<=15,
      'each_regime_ge_20':bool(e['regime_check']['ok'])}
def run(*,cache,regime_cache,funding_manifest,funding_store,window,tier,out):
    if os.path.exists(out): raise VolatilityAccelerationC41Error(f'{out} exists; packets are never overwritten')
    base=out+'.audited-base'; old_key,old_format=V.KEY,V.FORMAT; V.KEY,V.FORMAT=KEY,FORMAT
    try:
      V.run(cache=cache,regime_cache=regime_cache,funding_manifest=funding_manifest,funding_store=funding_store,window=window,tier=tier,side='long',out=base)
      with open(base,'rb') as f: pkt=json.loads(f.read())
      claimed=pkt.pop('packet_sha256',None)
      if claimed!=hashlib.sha256(B._canon(pkt)).hexdigest(): raise VolatilityAccelerationC41Error('audited-base packet hash mismatch')
      e=pkt.pop('volume_pressure_inventory_1h'); e.update(format=FORMAT,hypothesis='1h bullish continuation during true-range acceleration'); pkt[KEY]=e
      e['development_gates']=development_gates(pkt); e['all_development_gates_pass']=all(e['development_gates'].values())
      pkt['packet_sha256']=hashlib.sha256(B._canon(pkt)).hexdigest(); os.makedirs(os.path.dirname(os.path.abspath(out)),exist_ok=True); tmp=out+'.tmp'
      with open(tmp,'wb') as f: f.write(json.dumps(pkt,sort_keys=True,indent=1,allow_nan=False).encode()); f.flush(); os.fsync(f.fileno())
      os.replace(tmp,out); return pkt
    finally:
      V.KEY,V.FORMAT=old_key,old_format
      for p in (base,out+'.tmp'):
        with contextlib.suppress(FileNotFoundError): os.remove(p)
def main(argv=None):
    ap=argparse.ArgumentParser()
    for n in ('cache','regime_cache','funding_manifest','funding_store','window','out'): ap.add_argument('--'+n.replace('_','-'),required=True)
    ap.add_argument('--tier',required=True,choices=('CORE-10','CORE-20')); a=ap.parse_args(argv)
    try: pkt=run(**vars(a))
    except (B.RunnerError,M.ManifestError,R.RankBreadthError,V.VolumePressureError,VolatilityAccelerationC41Error) as e: print(f'REFUSED: {e}',file=sys.stderr); return 2
    x,e=pkt['stats']['aggregate'],pkt[KEY]; failed=sorted(k for k,v in e['development_gates'].items() if not v)
    print(f"{KEY}: {x['trades']} trades/{e['breadth']['symbols_with_trades']} symbols, R {x['r_sum']}, PF {x['pf']}, DD {x['max_dd_r']}, dev_pass {e['all_development_gates_pass']}, failed {failed}, packet {pkt['packet_sha256'][:12]} -> {a.out}"); return 0
if __name__=='__main__': sys.exit(main())
