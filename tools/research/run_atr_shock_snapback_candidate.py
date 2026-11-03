"""Run frozen ASS-4H with the prior candidate ledger's actual-funding overlay."""
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

B = R.B
KEY = 'atr_shock_snapback_4h'
FORMAT = 'zb-atr-shock-snapback-4h-envelope/1'


class ShockSnapbackError(RuntimeError): pass


def breadth(pkt):
    agg = pkt['stats']['aggregate']
    syms = sorted(s for s, x in pkt['stats']['per_symbol'].items() if int(x.get('trades') or 0) > 0)
    return {'closed_trades': int(agg.get('trades') or 0), 'symbols_with_trades': len(syms), 'symbols': syms,
            'breadth_ok': int(agg.get('trades') or 0) >= 80 and len(syms) >= 8,
            'rule': '>= 80 closed trades across >= 8 symbols'}


def regime_check(pkt):
    rows = {k: int((v or {}).get('trades') or 0) for k, v in pkt['stats']['regime'].items()}
    return {'trades': rows, 'ok': all(n >= 15 for n in rows.values()),
            'rule': '>= 15 development closes in each reported BTC 4h EMA200 regime'}


def run(*, cache, funding_manifest, funding_store, window, tier, side, out):
    if os.path.exists(out): raise ShockSnapbackError(f'{out} exists; packets are never overwritten')
    staging = out + '.staging'
    if any(os.path.exists(p) for p in (staging, staging + '.tmp', out + '.tmp')):
        raise ShockSnapbackError('stale staging exists; remove explicitly before rerun')
    st = B.ST.STRATEGIES.get(KEY) or {}
    if st.get('tf') != '4h' or not st.get('research_only'):
        raise ShockSnapbackError('candidate is not the frozen research-only 4h key')
    lo, hi, _ = B.VC.check_window(window, None)
    static = B.VC.frozen_universe(); syms = sorted(set(B.VC.pick_symbols(static, tier, None)) | {B.BTC})
    man = R.load_manifest(funding_manifest)
    events, marks, inventory = {}, {}, {}
    for sym in syms:
        ev, ef = R.funding_events(man, funding_store, sym, lo, hi)
        mk, mf = R.mark_points(man, funding_store, sym, lo, hi)
        events[sym], marks[sym] = ev, mk
        inventory[sym] = {'funding_events': len(ev), 'mark_points': len(mk),
                          'funding_files': [x['path'] for x in ef], 'mark_files': [x['path'] for x in mf],
                          'coverage_present': bool(ev and mk)}
    try:
        with R.actual_funding_scope(events, marks):
            B.run(cache=cache, window=window, out=staging, key=KEY, side=side, mgmt_mode='defaults', tier=tier)
        with open(staging, 'rb') as f: pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest(): raise ShockSnapbackError('staged packet hash mismatch')
        pkt['costs'].update(funding='actual signed settled rate x causal 1h mark-price close', fund_per_bar=None)
        pkt['atr_shock_snapback_4h'] = {'format': FORMAT, 'breadth': breadth(pkt),
            'regime_check': regime_check(pkt), 'manifest_digest': man['digest'], 'inventory': inventory,
            'equity_note': 'trade P&L/R and gates use actual funding; shared bar-equity path omits it'}
        pkt['stats']['actual_trade_equity_end'] = round(float(pkt['account']['start']) +
                                                        sum(float(t['pnl']) for t in pkt['trades']), 6)
        pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); tmp = out + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode()); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, out)
    finally:
        for p in (staging, staging + '.tmp', out + '.tmp'):
            with contextlib.suppress(FileNotFoundError): os.remove(p)
    return pkt


def main(argv=None):
    ap = argparse.ArgumentParser()
    for name in ('cache', 'funding_manifest', 'funding_store', 'window', 'out'):
        ap.add_argument('--' + name.replace('_', '-'), required=True)
    ap.add_argument('--tier', required=True, choices=('CORE-10', 'CORE-20'))
    ap.add_argument('--side', required=True, choices=('long', 'short'))
    a = ap.parse_args(argv)
    try: pkt = run(**vars(a))
    except (B.RunnerError, M.ManifestError, R.RankBreadthError, ShockSnapbackError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt['atr_shock_snapback_4h']
    print(f"{KEY} {a.side}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, R {agg['r_sum']}, "
          f"PF {agg['pf']}, breadth {env['breadth']['breadth_ok']}, regime {env['regime_check']['ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__': sys.exit(main())
