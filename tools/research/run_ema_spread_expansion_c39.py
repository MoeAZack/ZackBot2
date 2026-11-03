"""Run candidate 39 through the audited 1h actual-funding path."""
import argparse
import contextlib
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path: sys.path.insert(0, p)

import manifest as M  # noqa: E402
import run_volume_pressure_inventory_candidate as V  # noqa: E402

KEY = 'ema_spread_expansion_long_1h'
FORMAT = 'zb-ema-spread-expansion-c39-envelope/1'
B, R = V.B, V.R


class EmaSpreadC39Error(RuntimeError): pass


def development_gates(pkt):
    agg, env = pkt['stats']['aggregate'], pkt[KEY]
    return {
        'pre_cost_gross_positive': float(agg.get('gross') or 0) > 0,
        'mean_net_r_positive': float(agg.get('r_mean') or 0) > 0,
        'pf_gt_1_05': agg.get('pf') is not None and float(agg['pf']) > 1.05,
        'sample_ge_100': int(agg.get('trades') or 0) >= 100,
        'symbols_ge_8': int(env['breadth']['symbols_with_trades']) >= 8,
        'max_dd_le_15r': agg.get('max_dd_r') is not None and float(agg['max_dd_r']) <= 15.0,
        'each_regime_ge_20': bool(env['regime_check']['ok']),
    }


def run(*, cache, regime_cache, funding_manifest, funding_store, window, tier, out):
    if os.path.exists(out): raise EmaSpreadC39Error(f'{out} exists; packets are never overwritten')
    base = out + '.audited-base'
    if any(os.path.exists(p) for p in (base, out + '.tmp')):
        raise EmaSpreadC39Error('stale staging exists; remove explicitly before rerun')
    old_key, old_format = V.KEY, V.FORMAT
    V.KEY, V.FORMAT = KEY, FORMAT
    try:
        V.run(cache=cache, regime_cache=regime_cache, funding_manifest=funding_manifest,
              funding_store=funding_store, window=window, tier=tier, side='long', out=base)
        with open(base, 'rb') as f: pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise EmaSpreadC39Error('audited-base packet hash mismatch')
        env = pkt.pop('volume_pressure_inventory_1h')
        env.update(format=FORMAT, hypothesis='1h ATR-normalized EMA spread expansion after convergence')
        pkt[KEY] = env
        env['development_gates'] = development_gates(pkt)
        env['all_development_gates_pass'] = all(env['development_gates'].values())
        pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); tmp = out + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode()); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, out); return pkt
    finally:
        V.KEY, V.FORMAT = old_key, old_format
        for p in (base, out + '.tmp'):
            with contextlib.suppress(FileNotFoundError): os.remove(p)


def main(argv=None):
    ap = argparse.ArgumentParser()
    for name in ('cache', 'regime_cache', 'funding_manifest', 'funding_store', 'window', 'out'):
        ap.add_argument('--' + name.replace('_', '-'), required=True)
    ap.add_argument('--tier', required=True, choices=('CORE-10', 'CORE-20'))
    a = ap.parse_args(argv)
    try: pkt = run(**vars(a))
    except (B.RunnerError, M.ManifestError, R.RankBreadthError, V.VolumePressureError, EmaSpreadC39Error) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt[KEY]
    failed = sorted(k for k, v in env['development_gates'].items() if not v)
    print(f"{KEY}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, R {agg['r_sum']}, "
          f"PF {agg['pf']}, DD {agg['max_dd_r']}, dev_pass {env['all_development_gates_pass']}, "
          f"failed {failed}, packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__': sys.exit(main())
