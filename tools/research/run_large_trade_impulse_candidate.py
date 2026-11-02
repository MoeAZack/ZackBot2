"""Run frozen LTI-1H using archived quote volume/trade counts and actual funding."""
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
import run_rank_breadth_reversal_candidate as A  # noqa: E402
import run_volume_pressure_inventory_candidate as V  # noqa: E402

KEY = 'large_trade_impulse_1h'
FORMAT = 'zb-large-trade-impulse-1h-envelope/1'
B, TF_MS = V.B, V.TF_MS
ACTUAL_FUNDING_SCOPE = V.one_hour_actual_scope


class LargeTradeError(RuntimeError): pass


def trade_size_features(man, store, symbol, lo, hi):
    files = [f for f in man['files'] if f.get('series') == 'klines' and f.get('interval') == '1m'
             and f.get('symbol') == symbol and f.get('first_open_ms') is not None
             and f['last_open_ms'] >= lo and f['first_open_ms'] < hi]
    sub = dict(man); sub['files'] = files
    rows, used = A.archive_rows(sub, store, symbol, 'klines', lo, hi - 1)
    groups = {}
    for r in rows:
        t = int(r[0])
        if lo <= t < hi:
            groups.setdefault((t // TF_MS) * TF_MS, []).append((float(r[7]), int(r[8])))
    out = {}
    for h, xs in groups.items():
        if len(xs) != 60: continue
        qv, trades = sum(x[0] for x in xs), sum(x[1] for x in xs)
        if trades > 0: out[h] = qv / trades
    return out, used


@contextlib.contextmanager
def trade_size_context_scope(events, marks, features):
    old_build = B.ST.build_context

    def build_context(dfs, btc_key='BTCUSDT'):
        ctx = old_build(dfs, btc_key)
        for sym, d in dfs.items():
            feat = features.get(sym, {})
            opens = pd.to_datetime(d.t, utc=True).map(lambda x: int(x.timestamp() * 1000)).to_numpy()
            ctx[sym]['avg_trade_notional'] = np.array([float(feat.get(int(t), np.nan)) for t in opens])
        return ctx

    with ACTUAL_FUNDING_SCOPE(events, marks):
        B.ST.build_context = build_context
        try: yield
        finally: B.ST.build_context = old_build


def run(*, cache, regime_cache, funding_manifest, funding_store, window, tier, side, out):
    if os.path.exists(out): raise LargeTradeError(f'{out} exists; packets are never overwritten')
    base = out + '.audited-base'
    if any(os.path.exists(p) for p in (base, out + '.tmp')):
        raise LargeTradeError('stale staging exists; remove explicitly before rerun')
    lo, hi, _ = B.VC.check_window(window, None); man = A.load_manifest(funding_manifest)
    syms = sorted(set(B.VC.pick_symbols(B.VC.frozen_universe(), tier, None)) | {B.BTC})
    features, inventory = {}, {}
    for sym in syms:
        features[sym], used = trade_size_features(man, funding_store, sym, lo, hi)
        inventory[sym] = {'complete_feature_hours': len(features[sym]),
                          'files': [x['path'] for x in used], 'coverage_present': bool(features[sym])}
    old_key, old_format, old_scope = V.KEY, V.FORMAT, V.one_hour_actual_scope
    V.KEY, V.FORMAT = KEY, FORMAT
    V.one_hour_actual_scope = lambda events, marks: trade_size_context_scope(events, marks, features)
    try:
        V.run(cache=cache, regime_cache=regime_cache, funding_manifest=funding_manifest,
              funding_store=funding_store, window=window, tier=tier, side=side, out=base)
        with open(base, 'rb') as f: pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise LargeTradeError('audited-base packet hash mismatch')
        env = pkt.pop('volume_pressure_inventory_1h')
        env.update(format=FORMAT, hypothesis='large average trade-notional directional impulse',
                   minute_inventory=inventory)
        pkt[KEY] = env; pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); tmp = out + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode()); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, out); return pkt
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
    except (B.RunnerError, M.ManifestError, A.RankBreadthError, V.VolumePressureError, LargeTradeError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt[KEY]
    print(f"{KEY} {a.side}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, R {agg['r_sum']}, "
          f"PF {agg['pf']}, breadth {env['breadth']['breadth_ok']}, regime {env['regime_check']['ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__': sys.exit(main())
