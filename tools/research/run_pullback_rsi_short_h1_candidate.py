"""Run queued H1 pullback-RSI short through the audited 4h actual-funding path."""
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
import run_rank_breadth_reversal_candidate as R  # noqa: E402
import run_volume_pressure_inventory_candidate as V  # noqa: E402

KEY = 'pullback_rsi_short_h1'
FORMAT = 'zb-pullback-rsi-short-h1-envelope/1'
B = R.B


class PullbackH1Error(RuntimeError): pass


def run(*, cache, funding_manifest, funding_store, window, tier, out):
    if os.path.exists(out): raise PullbackH1Error(f'{out} exists; packets are never overwritten')
    base = out + '.audited-base'
    if any(os.path.exists(p) for p in (base, out + '.tmp')):
        raise PullbackH1Error('stale staging exists; remove explicitly before rerun')
    old_key, old_format = R.KEY, R.FORMAT
    R.KEY, R.FORMAT = KEY, FORMAT
    try:
        R.run(cache=cache, funding_manifest=funding_manifest, funding_store=funding_store,
              window=window, tier=tier, side='short', out=base)
        with open(base, 'rb') as f: pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise PullbackH1Error('audited-base packet hash mismatch')
        env = pkt.pop('rank_breadth_reversal_4h')
        env.update(format=FORMAT, hypothesis='queued H1 existing pullback_rsi short re-evaluation',
                   breadth=V.breadth(pkt), regime_check=V.regime_check(pkt))
        pkt[KEY] = env; pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); tmp = out + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode()); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, out); return pkt
    finally:
        R.KEY, R.FORMAT = old_key, old_format
        for p in (base, out + '.tmp'):
            with contextlib.suppress(FileNotFoundError): os.remove(p)


def main(argv=None):
    ap = argparse.ArgumentParser()
    for name in ('cache', 'funding_manifest', 'funding_store', 'window', 'out'):
        ap.add_argument('--' + name.replace('_', '-'), required=True)
    ap.add_argument('--tier', required=True, choices=('CORE-10', 'CORE-20'))
    a = ap.parse_args(argv)
    try: pkt = run(**vars(a))
    except (B.RunnerError, M.ManifestError, R.RankBreadthError, PullbackH1Error) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt[KEY]
    print(f"{KEY}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, R {agg['r_sum']}, "
          f"PF {agg['pf']}, breadth {env['breadth']['breadth_ok']}, regime {env['regime_check']['ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__': sys.exit(main())
