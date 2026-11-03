"""Run frozen WLP-1H through the audited actual-funding 1h path."""
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
import run_rank_breadth_reversal_candidate as A  # noqa: E402
import run_volume_pressure_inventory_candidate as V  # noqa: E402

KEY = 'weekend_liquidity_premium_1h'
FORMAT = 'zb-weekend-liquidity-premium-1h-envelope/1'
B = V.B


class WeekendPremiumError(RuntimeError): pass


def run(*, cache, regime_cache, funding_manifest, funding_store, window, tier, side, out):
    if os.path.exists(out): raise WeekendPremiumError(f'{out} exists; packets are never overwritten')
    base = out + '.audited-base'
    if any(os.path.exists(p) for p in (base, out + '.tmp')):
        raise WeekendPremiumError('stale staging exists; remove explicitly before rerun')
    old_key, old_format = V.KEY, V.FORMAT
    V.KEY, V.FORMAT = KEY, FORMAT
    try:
        V.run(cache=cache, regime_cache=regime_cache, funding_manifest=funding_manifest,
              funding_store=funding_store, window=window, tier=tier, side=side, out=base)
        with open(base, 'rb') as f: pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise WeekendPremiumError('audited-base packet hash mismatch')
        env = pkt.pop('volume_pressure_inventory_1h')
        env.update(format=FORMAT, hypothesis='fixed UTC weekend-boundary liquidity risk premium')
        pkt[KEY] = env
        pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); tmp = out + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode()); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, out)
        return pkt
    finally:
        V.KEY, V.FORMAT = old_key, old_format
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
    except (B.RunnerError, M.ManifestError, A.RankBreadthError, V.VolumePressureError, WeekendPremiumError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt[KEY]
    print(f"{KEY} {a.side}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, R {agg['r_sum']}, "
          f"PF {agg['pf']}, breadth {env['breadth']['breadth_ok']}, regime {env['regime_check']['ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__': sys.exit(main())
