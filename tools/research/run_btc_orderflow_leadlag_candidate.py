"""Run frozen BOL-1H with verified BTC taker flow and actual funding."""
import argparse
import contextlib
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path: sys.path.insert(0, p)

import manifest as M  # noqa: E402
import run_aggressor_flow_absorption_candidate as O  # noqa: E402
import run_rank_breadth_reversal_candidate as A  # noqa: E402
import run_volume_pressure_inventory_candidate as V  # noqa: E402

KEY = 'btc_orderflow_leadlag_1h'
FORMAT = 'zb-btc-orderflow-leadlag-1h-envelope/1'
B = V.B
ACTUAL_FUNDING_SCOPE = V.one_hour_actual_scope


class BTCLeadLagError(RuntimeError): pass


@contextlib.contextmanager
def leadlag_context_scope(events, marks, btc_flow):
    old_build = B.ST.build_context

    def build_context(dfs, btc_key='BTCUSDT'):
        ctx = old_build(dfs, btc_key)
        btc = dfs[btc_key]
        bt = pd.to_datetime(btc.t, utc=True).map(lambda x: int(x.timestamp() * 1000))
        flow = pd.Series([float(btc_flow.get(int(t), np.nan)) for t in bt], index=pd.to_datetime(btc.t, utc=True))
        move = pd.Series((btc.c / btc.o - 1.0).to_numpy(float), index=pd.to_datetime(btc.t, utc=True))
        for sym, d in dfs.items():
            t = pd.to_datetime(d.t, utc=True)
            ctx[sym]['btc_taker_imbalance'] = flow.reindex(t).to_numpy(float)
            ctx[sym]['btc_hour_move'] = move.reindex(t).to_numpy(float)
        return ctx

    with ACTUAL_FUNDING_SCOPE(events, marks):
        B.ST.build_context = build_context
        try: yield
        finally: B.ST.build_context = old_build


def run(*, cache, regime_cache, funding_manifest, funding_store, window, tier, side, out):
    if os.path.exists(out): raise BTCLeadLagError(f'{out} exists; packets are never overwritten')
    base = out + '.audited-base'
    if any(os.path.exists(p) for p in (base, out + '.tmp')):
        raise BTCLeadLagError('stale staging exists; remove explicitly before rerun')
    lo, hi, _ = B.VC.check_window(window, None)
    man = A.load_manifest(funding_manifest)
    btc_flow, used = O.flow_features(man, funding_store, B.BTC, lo, hi)
    if not btc_flow: raise BTCLeadLagError('verified BTC 1m taker-flow coverage is absent')
    old_key, old_format, old_scope = V.KEY, V.FORMAT, V.one_hour_actual_scope
    V.KEY, V.FORMAT = KEY, FORMAT
    V.one_hour_actual_scope = lambda events, marks: leadlag_context_scope(events, marks, btc_flow)
    try:
        V.run(cache=cache, regime_cache=regime_cache, funding_manifest=funding_manifest,
              funding_store=funding_store, window=window, tier=tier, side=side, out=base)
        with open(base, 'rb') as f: pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise BTCLeadLagError('audited-base packet hash mismatch')
        env = pkt.pop('volume_pressure_inventory_1h')
        env.update(format=FORMAT, hypothesis='BTC aggressor-flow impulse followed by locally flat target',
                   btc_flow_inventory={'complete_feature_hours': len(btc_flow),
                                       'files': [x['path'] for x in used]})
        pkt[KEY] = env
        pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); tmp = out + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode()); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, out)
        return pkt
    finally:
        V.KEY, V.FORMAT, V.one_hour_actual_scope = old_key, old_format, old_scope
        for p in (base, out + '.tmp'):
            with contextlib.suppress(FileNotFoundError): os.remove(p)


def main(argv=None):
    ap = argparse.ArgumentParser()
    for name in ('cache', 'regime_cache', 'funding_manifest', 'funding_store', 'window', 'out'):
        ap.add_argument('--' + name.replace('_', '-'), required=True)
    ap.add_argument('--tier', required=True, choices=('CORE-10', 'CORE-20'))
    ap.add_argument('--side', required=True, choices=('long', 'short'))
    a = ap.parse_args(argv)
    try: pkt = run(**vars(a))
    except (B.RunnerError, M.ManifestError, A.RankBreadthError, V.VolumePressureError,
            O.AggressorFlowError, BTCLeadLagError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt[KEY]
    print(f"{KEY} {a.side}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, R {agg['r_sum']}, "
          f"PF {agg['pf']}, breadth {env['breadth']['breadth_ok']}, regime {env['regime_check']['ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__': sys.exit(main())
