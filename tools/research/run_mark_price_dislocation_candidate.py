"""Run frozen MPD-1H with causal Binance mark-price context and actual funding."""
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

KEY = 'mark_price_dislocation_1h'
FORMAT = 'zb-mark-price-dislocation-1h-envelope/1'
B, TF_MS = V.B, V.TF_MS
ACTUAL_FUNDING_SCOPE = V.one_hour_actual_scope


class MarkDislocationError(RuntimeError): pass


@contextlib.contextmanager
def mark_basis_scope(events, marks):
    """Add mark basis at each completed candle while preserving actual funding."""
    old_build = B.ST.build_context

    def build_context(dfs, btc_key='BTCUSDT'):
        ctx = old_build(dfs, btc_key)
        for sym, d in dfs.items():
            pts = marks.get(sym, [])
            # mark point timestamps are close times; book timestamps are opens.
            ms = pd.Series({np.datetime64(int(t - TF_MS), 'ms'): float(px) for t, px in pts})
            opens = pd.to_datetime(d.t, utc=True).values.astype('datetime64[ms]')
            mark = ms.reindex(opens).to_numpy(float)
            ctx[sym]['mark_basis'] = (d.c.to_numpy(float) - mark) / mark
        return ctx

    with ACTUAL_FUNDING_SCOPE(events, marks):
        B.ST.build_context = build_context
        try: yield
        finally: B.ST.build_context = old_build


def run(*, cache, regime_cache, funding_manifest, funding_store, window, tier, side, out):
    if os.path.exists(out): raise MarkDislocationError(f'{out} exists; packets are never overwritten')
    base = out + '.audited-base'
    if any(os.path.exists(p) for p in (base, out + '.tmp')):
        raise MarkDislocationError('stale staging exists; remove explicitly before rerun')
    old_key, old_format, old_scope = V.KEY, V.FORMAT, V.one_hour_actual_scope
    V.KEY, V.FORMAT, V.one_hour_actual_scope = KEY, FORMAT, mark_basis_scope
    try:
        V.run(cache=cache, regime_cache=regime_cache, funding_manifest=funding_manifest,
              funding_store=funding_store, window=window, tier=tier, side=side, out=base)
        with open(base, 'rb') as f: pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise MarkDislocationError('audited-base packet hash mismatch')
        env = pkt.pop('volume_pressure_inventory_1h')
        env.update(format=FORMAT, hypothesis='contract-close deviation from independently archived mark close')
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
    except (B.RunnerError, M.ManifestError, A.RankBreadthError, V.VolumePressureError, MarkDislocationError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt[KEY]
    print(f"{KEY} {a.side}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, R {agg['r_sum']}, "
          f"PF {agg['pf']}, breadth {env['breadth']['breadth_ok']}, regime {env['regime_check']['ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__': sys.exit(main())
