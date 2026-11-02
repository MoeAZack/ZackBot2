"""Run the frozen RAS-1H candidate through the shared cost-aware breadth path.

This wrapper adds the already-supported generic 1h cache interval only for this
call, validates the staged packet, records the >=100 trades / >=8 symbols breadth
gate, and publishes atomically.  It changes no runtime registry settings.
"""
import argparse
import contextlib
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_breadth_candidate as B  # noqa: E402

KEY = 'regime_adaptive_switch_1h'
TF, TF_MS = '1h', 3_600_000
FORMAT = 'zb-regime-adaptive-1h-envelope/1'


class RegimeAdaptiveError(RuntimeError):
    pass


@contextlib.contextmanager
def one_hour_scope():
    old = dict(B.TFS)
    B.TFS[TF] = TF_MS
    try:
        yield
    finally:
        B.TFS.clear(); B.TFS.update(old)


def breadth(pkt):
    a = pkt['stats']['aggregate']
    syms = sorted(s for s, x in pkt['stats']['per_symbol'].items() if int(x.get('trades') or 0) > 0)
    ok = int(a.get('trades') or 0) >= 100 and len(syms) >= 8
    return {'closed_trades': int(a.get('trades') or 0), 'symbols_with_trades': len(syms),
            'symbols': syms, 'breadth_ok': ok,
            'rule': '>= 100 closed trades across >= 8 symbols with trades'}


def run(*, cache, regime_cache, window, tier, side, out):
    if os.path.exists(out):
        raise RegimeAdaptiveError(f'{out} exists; packets are never overwritten')
    staging = out + '.staging'
    if os.path.exists(staging) or os.path.exists(staging + '.tmp'):
        raise RegimeAdaptiveError('stale staging exists; remove explicitly before rerun')
    st = B.ST.STRATEGIES.get(KEY) or {}
    if st.get('tf') != TF or not st.get('research_only'):
        raise RegimeAdaptiveError('candidate is not the frozen research-only 1h key')
    try:
        with one_hour_scope():
            B.run(cache=cache, window=window, out=staging, key=KEY, side=side,
                  mgmt_mode='defaults', tier=tier, regime_cache=regime_cache)
        with open(staging, 'rb') as f:
            pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise RegimeAdaptiveError('staged packet hash mismatch')
        if pkt.get('strategy', {}).get('key') != KEY or pkt.get('window', {}).get('tf') != TF:
            raise RegimeAdaptiveError('staged packet identity mismatch')
        pkt['regime_adaptive_1h'] = {'format': FORMAT, 'breadth': breadth(pkt),
                            'timeframe_scope': '1h added to shared runner TFS for this call only'}
        pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
        tmp = out + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode())
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, out)
    finally:
        for p in (staging, staging + '.tmp', out + '.tmp'):
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)
    return pkt


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', required=True)
    ap.add_argument('--regime-cache', required=True)
    ap.add_argument('--window', required=True)
    ap.add_argument('--tier', required=True, choices=('CORE-10', 'CORE-20'))
    ap.add_argument('--side', required=True, choices=('long', 'short'))
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    try:
        pkt = run(**vars(a))
    except (B.RunnerError, RegimeAdaptiveError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, br = pkt['stats']['aggregate'], pkt['regime_adaptive_1h']['breadth']
    print(f"{KEY} {a.side}: {agg['trades']} trades on {br['symbols_with_trades']} symbols, "
          f"R sum {agg['r_sum']}, PF {agg['pf']}, breadth_ok {br['breadth_ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
