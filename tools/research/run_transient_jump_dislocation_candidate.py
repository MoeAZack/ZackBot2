"""Run frozen TJD-1H with archived 1m path features and actual funding."""
import argparse
import contextlib
import hashlib
import json
import os
import statistics
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path: sys.path.insert(0, p)

import manifest as M  # noqa: E402
import run_rank_breadth_reversal_candidate as A  # noqa: E402
import run_volume_pressure_inventory_candidate as V  # noqa: E402

KEY = 'transient_jump_dislocation_1h'
FORMAT = 'zb-transient-jump-dislocation-1h-envelope/1'
B, TF_MS = V.B, V.TF_MS
ACTUAL_FUNDING_SCOPE = V.one_hour_actual_scope


class TransientJumpError(RuntimeError): pass


def minute_features(man, store, symbol, lo, hi):
    files = [f for f in man['files'] if f.get('series') == 'klines' and f.get('interval') == '1m'
             and f.get('symbol') == symbol and f.get('first_open_ms') is not None
             and f['last_open_ms'] >= lo and f['first_open_ms'] < hi]
    sub = dict(man); sub['files'] = files
    rows, used = A.archive_rows(sub, store, symbol, 'klines', lo, hi - 1)
    groups = {}
    for r in rows:
        t = int(r[0]);
        if not (lo <= t < hi): continue
        h = (t // TF_MS) * TF_MS
        op, cl, qv = float(r[1]), float(r[4]), float(r[7])
        groups.setdefault(h, []).append((cl / op - 1.0, qv, t))
    out = {}
    for h, xs in groups.items():
        xs.sort(key=lambda x: x[2])
        if len(xs) != 60: continue
        jump, jqv, _ = max(xs, key=lambda x: abs(x[0]))
        median_qv = statistics.median(x[1] for x in xs)
        # Retention is magnitude of the completed 1h contract move relative to the largest 1m jump.
        # The contract move itself is filled later from the causal 1h book in build_context.
        out[h] = {'jump': jump, 'jump_qv': jqv, 'median_qv': median_qv}
    return out, used


@contextlib.contextmanager
def minute_context_scope(events, marks, features):
    old_build = B.ST.build_context

    def build_context(dfs, btc_key='BTCUSDT'):
        ctx = old_build(dfs, btc_key)
        for sym, d in dfs.items():
            feat = features.get(sym, {})
            opens = pd.to_datetime(d.t, utc=True).map(lambda x: int(x.timestamp() * 1000)).to_numpy()
            jumps = np.array([float(feat.get(int(t), {}).get('jump', np.nan)) for t in opens])
            jqv = np.array([float(feat.get(int(t), {}).get('jump_qv', np.nan)) for t in opens])
            mqv = np.array([float(feat.get(int(t), {}).get('median_qv', np.nan)) for t in opens])
            hour_ret = d.c.to_numpy(float) / d.o.to_numpy(float) - 1.0
            ctx[sym]['minute_jump'] = jumps
            ctx[sym]['jump_retention'] = np.divide(
                np.abs(hour_ret), np.abs(jumps), out=np.full(len(jumps), np.nan), where=np.abs(jumps) > 0)
            ctx[sym]['jump_participation'] = np.divide(
                jqv, mqv, out=np.full(len(jumps), np.nan), where=mqv > 0)
        return ctx

    with ACTUAL_FUNDING_SCOPE(events, marks):
        B.ST.build_context = build_context
        try: yield
        finally: B.ST.build_context = old_build


def run(*, cache, regime_cache, funding_manifest, funding_store, window, tier, side, out):
    if os.path.exists(out): raise TransientJumpError(f'{out} exists; packets are never overwritten')
    base = out + '.audited-base'
    if any(os.path.exists(p) for p in (base, out + '.tmp')):
        raise TransientJumpError('stale staging exists; remove explicitly before rerun')
    lo, hi, _ = B.VC.check_window(window, None)
    man = A.load_manifest(funding_manifest)
    syms = sorted(set(B.VC.pick_symbols(B.VC.frozen_universe(), tier, None)) | {B.BTC})
    features, inventory = {}, {}
    for sym in syms:
        features[sym], used = minute_features(man, funding_store, sym, lo, hi)
        inventory[sym] = {'complete_feature_hours': len(features[sym]),
                          'files': [x['path'] for x in used], 'coverage_present': bool(features[sym])}
    old_key, old_format, old_scope = V.KEY, V.FORMAT, V.one_hour_actual_scope
    V.KEY, V.FORMAT = KEY, FORMAT
    V.one_hour_actual_scope = lambda events, marks: minute_context_scope(events, marks, features)
    try:
        V.run(cache=cache, regime_cache=regime_cache, funding_manifest=funding_manifest,
              funding_store=funding_store, window=window, tier=tier, side=side, out=base)
        with open(base, 'rb') as f: pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise TransientJumpError('audited-base packet hash mismatch')
        env = pkt.pop('volume_pressure_inventory_1h')
        env.update(format=FORMAT, hypothesis='transient high-participation 1m jump inside completed 1h candle',
                   minute_inventory=inventory)
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
    except (B.RunnerError, M.ManifestError, A.RankBreadthError, V.VolumePressureError, TransientJumpError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt[KEY]
    print(f"{KEY} {a.side}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, R {agg['r_sum']}, "
          f"PF {agg['pf']}, breadth {env['breadth']['breadth_ok']}, regime {env['regime_check']['ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__': sys.exit(main())
