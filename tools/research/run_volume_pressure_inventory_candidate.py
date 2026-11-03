"""Run frozen VPI-1H with actual funding and the scoped 1h shared runner."""
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
KEY = 'volume_pressure_inventory_1h'
TF, TF_MS = '1h', 3_600_000
FORMAT = 'zb-volume-pressure-inventory-1h-envelope/1'


class VolumePressureError(RuntimeError): pass


@contextlib.contextmanager
def one_hour_actual_scope(events, marks):
    old_tfs, old_enrich, old_fund = dict(B.TFS), B.enrich, B.BT.FUND_PER_BAR

    def enrich(tr, pos, book, reg):
        got = old_enrich(tr, pos, book, reg)
        if not len(got): return got
        bt = B._ms(book.t)
        for ix, row in got.iterrows():
            raw = tr.loc[ix]; p = pos[(raw.sym, int(raw.i_in))]
            entry, exit_ = int(bt[int(raw.i_in)]), int(bt[int(raw.i_out)]) + TF_MS
            fund = R.funding_cost(raw.sym, int(raw.side), p['qty'], entry, exit_,
                                  events.get(raw.sym, []), marks.get(raw.sym, []))
            pnl = float(got.at[ix, 'gross']) - float(got.at[ix, 'fees']) - fund
            got.at[ix, 'funding'], got.at[ix, 'pnl'], got.at[ix, 'R'] = fund, pnl, pnl / float(p['risk'])
        return got

    B.TFS[TF], B.enrich, B.BT.FUND_PER_BAR = TF_MS, enrich, 0.0
    try: yield
    finally:
        B.TFS.clear(); B.TFS.update(old_tfs)
        B.enrich, B.BT.FUND_PER_BAR = old_enrich, old_fund


def breadth(pkt):
    agg = pkt['stats']['aggregate']
    syms = sorted(s for s, x in pkt['stats']['per_symbol'].items() if int(x.get('trades') or 0) > 0)
    return {'closed_trades': int(agg.get('trades') or 0), 'symbols_with_trades': len(syms), 'symbols': syms,
            'breadth_ok': int(agg.get('trades') or 0) >= 100 and len(syms) >= 8,
            'rule': '>= 100 closed trades across >= 8 symbols'}


def regime_check(pkt):
    rows = {k: int((v or {}).get('trades') or 0) for k, v in pkt['stats']['regime'].items()}
    return {'trades': rows, 'ok': all(n >= 20 for n in rows.values()),
            'rule': '>= 20 development closes in each reported BTC 4h EMA200 regime'}


def run(*, cache, regime_cache, funding_manifest, funding_store, window, tier, side, out):
    if os.path.exists(out): raise VolumePressureError(f'{out} exists; packets are never overwritten')
    staging = out + '.staging'
    if any(os.path.exists(p) for p in (staging, staging + '.tmp', out + '.tmp')):
        raise VolumePressureError('stale staging exists; remove explicitly before rerun')
    st = B.ST.STRATEGIES.get(KEY) or {}
    if st.get('tf') != TF or not st.get('research_only'):
        raise VolumePressureError('candidate is not the frozen research-only 1h key')
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
        with one_hour_actual_scope(events, marks):
            B.run(cache=cache, regime_cache=regime_cache, window=window, out=staging,
                  key=KEY, side=side, mgmt_mode='defaults', tier=tier)
        with open(staging, 'rb') as f: pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest(): raise VolumePressureError('staged packet hash mismatch')
        pkt['costs'].update(funding='actual signed settled rate x causal 1h mark-price close', fund_per_bar=None)
        pkt['volume_pressure_inventory_1h'] = {'format': FORMAT, 'breadth': breadth(pkt),
            'regime_check': regime_check(pkt), 'manifest_digest': man['digest'], 'inventory': inventory,
            'timeframe_scope': '1h added to shared runner TFS for this call only',
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
    for name in ('cache', 'regime_cache', 'funding_manifest', 'funding_store', 'window', 'out'):
        ap.add_argument('--' + name.replace('_', '-'), required=True)
    ap.add_argument('--tier', required=True, choices=('CORE-10', 'CORE-20'))
    ap.add_argument('--side', required=True, choices=('long', 'short'))
    a = ap.parse_args(argv)
    try: pkt = run(**vars(a))
    except (B.RunnerError, M.ManifestError, R.RankBreadthError, VolumePressureError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt['volume_pressure_inventory_1h']
    print(f"{KEY} {a.side}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, R {agg['r_sum']}, "
          f"PF {agg['pf']}, breadth {env['breadth']['breadth_ok']}, regime {env['regime_check']['ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__': sys.exit(main())
