"""Run frozen RBR-4H through the shared breadth engine with actual funding."""
import argparse
import bisect
import contextlib
import gzip
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import manifest as M  # noqa: E402
import run_breadth_candidate as B  # noqa: E402

KEY = 'rank_breadth_reversal_4h'
FORMAT = 'zb-rank-breadth-reversal-4h-envelope/1'
BAR_MS = 4 * 3_600_000
HOUR_MS = 3_600_000


class RankBreadthError(RuntimeError):
    pass


def load_manifest(path):
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        man = json.load(f)
    M.validate(man)
    if man.get('loader_version') != M.ARCHIVE_LOADER or man.get('source_class') != 'archive-verified':
        raise RankBreadthError('funding manifest is not checksum-verified Binance archive data')
    return man


def archive_rows(man, store, symbol, series, lo, hi):
    files = [f for f in man['files'] if f.get('series') == series and f.get('symbol') == symbol
             and f.get('first_open_ms') is not None and f['last_open_ms'] >= lo and f['first_open_ms'] <= hi]
    rows = []
    for f in sorted(files, key=lambda x: x['first_open_ms']):
        path = M.contained(store, f['path'])
        _, published, raw = M.read_archive(path, f['path'])
        if published != f['sha256']:
            raise RankBreadthError(f'{f["path"]}: archive checksum differs from manifest')
        got = M.parse_archive_csv(f['path'], raw, M.archive_meta(f['path']))
        if len(got) != f['rows']:
            raise RankBreadthError(f'{f["path"]}: row count differs from manifest')
        rows.extend(got)
    return rows, files


def funding_events(man, store, symbol, lo, hi):
    rows, files = archive_rows(man, store, symbol, 'fundingRate', lo, hi)
    events = [(int(r[0]), float(r[2])) for r in rows if lo <= int(r[0]) < hi]
    if any(b[0] <= a[0] for a, b in zip(events, events[1:])):
        raise RankBreadthError(f'{symbol}: funding events not strictly increasing')
    return events, files


def mark_points(man, store, symbol, lo, hi):
    rows, files = archive_rows(man, store, symbol, 'markPriceKlines', lo - HOUR_MS, hi)
    points = [(int(r[0]) + HOUR_MS, float(r[4])) for r in rows if lo - HOUR_MS <= int(r[0]) < hi]
    if any(b[0] <= a[0] for a, b in zip(points, points[1:])):
        raise RankBreadthError(f'{symbol}: mark points not strictly increasing')
    return points, files


def funding_cost(symbol, side, qty, entry, exit_, events, marks):
    mt, mp = [x[0] for x in marks], [x[1] for x in marks]
    total = 0.0
    for t, rate in events:
        if entry <= t < exit_:
            j = bisect.bisect_right(mt, t) - 1
            if j < 0:
                raise RankBreadthError(f'{symbol}: no causal mark for funding event {t}')
            total += int(side) * float(rate) * float(qty) * mp[j]
    return total


@contextlib.contextmanager
def actual_funding_scope(events, marks):
    old_enrich, old_fund = B.enrich, B.BT.FUND_PER_BAR

    def enrich(tr, pos, book, reg):
        got = old_enrich(tr, pos, book, reg)
        if not len(got):
            return got
        bt = B._ms(book.t)
        for ix, row in got.iterrows():
            raw = tr.loc[ix]
            p = pos[(raw.sym, int(raw.i_in))]
            entry, exit_ = int(bt[int(raw.i_in)]), int(bt[int(raw.i_out)]) + BAR_MS
            fund = funding_cost(raw.sym, int(raw.side), p['qty'], entry, exit_,
                                events.get(raw.sym, []), marks.get(raw.sym, []))
            pnl = float(got.at[ix, 'gross']) - float(got.at[ix, 'fees']) - fund
            got.at[ix, 'funding'], got.at[ix, 'pnl'], got.at[ix, 'R'] = fund, pnl, pnl / float(p['risk'])
        return got

    B.enrich, B.BT.FUND_PER_BAR = enrich, 0.0
    try:
        yield
    finally:
        B.enrich, B.BT.FUND_PER_BAR = old_enrich, old_fund


def breadth(pkt):
    agg = pkt['stats']['aggregate']
    syms = sorted(s for s, x in pkt['stats']['per_symbol'].items() if int(x.get('trades') or 0) > 0)
    return {'closed_trades': int(agg.get('trades') or 0), 'symbols_with_trades': len(syms), 'symbols': syms,
            'breadth_ok': int(agg.get('trades') or 0) >= 60 and len(syms) >= 8,
            'rule': '>= 60 closed trades across >= 8 symbols'}


def regime_check(pkt):
    rows = {k: int((v or {}).get('trades') or 0) for k, v in pkt['stats']['regime'].items()}
    return {'trades': rows, 'ok': all(n >= 15 for n in rows.values()),
            'rule': '>= 15 development closes in each reported BTC 4h EMA200 regime'}


def run(*, cache, funding_manifest, funding_store, window, tier, side, out):
    if os.path.exists(out):
        raise RankBreadthError(f'{out} exists; packets are never overwritten')
    staging = out + '.staging'
    if any(os.path.exists(p) for p in (staging, staging + '.tmp', out + '.tmp')):
        raise RankBreadthError('stale staging exists; remove explicitly before rerun')
    st = B.ST.STRATEGIES.get(KEY) or {}
    if st.get('tf') != '4h' or not st.get('research_only'):
        raise RankBreadthError('candidate is not the frozen research-only 4h key')
    lo, hi, _ = B.VC.check_window(window, None)
    static = B.VC.frozen_universe()
    syms = sorted(set(B.VC.pick_symbols(static, tier, None)) | {B.BTC})
    man = load_manifest(funding_manifest)
    events, marks, inventory = {}, {}, {}
    for sym in syms:
        ev, ef = funding_events(man, funding_store, sym, lo, hi)
        mk, mf = mark_points(man, funding_store, sym, lo, hi)
        events[sym], marks[sym] = ev, mk
        inventory[sym] = {'funding_events': len(ev), 'mark_points': len(mk),
                          'funding_files': [x['path'] for x in ef], 'mark_files': [x['path'] for x in mf],
                          'coverage_present': bool(ev and mk)}
    try:
        with actual_funding_scope(events, marks):
            B.run(cache=cache, window=window, out=staging, key=KEY, side=side,
                  mgmt_mode='defaults', tier=tier)
        with open(staging, 'rb') as f:
            pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise RankBreadthError('staged packet hash mismatch')
        pkt['costs'].update(funding='actual signed settled rate x causal 1h mark-price close', fund_per_bar=None)
        pkt['rank_breadth_reversal_4h'] = {
            'format': FORMAT, 'breadth': breadth(pkt), 'regime_check': regime_check(pkt),
            'manifest_digest': man['digest'], 'inventory': inventory,
            'equity_note': 'trade P&L/R and gates use actual funding; shared bar-equity path omits it'}
        pkt['stats']['actual_trade_equity_end'] = round(float(pkt['account']['start']) +
                                                        sum(float(t['pnl']) for t in pkt['trades']), 6)
        pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        tmp = out + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode())
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, out)
    finally:
        for p in (staging, staging + '.tmp', out + '.tmp'):
            with contextlib.suppress(FileNotFoundError): os.remove(p)
    return pkt


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', required=True)
    ap.add_argument('--funding-manifest', required=True)
    ap.add_argument('--funding-store', required=True)
    ap.add_argument('--window', required=True)
    ap.add_argument('--tier', required=True, choices=('CORE-10', 'CORE-20'))
    ap.add_argument('--side', required=True, choices=('long', 'short'))
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    try:
        pkt = run(**vars(a))
    except (B.RunnerError, M.ManifestError, RankBreadthError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, env = pkt['stats']['aggregate'], pkt['rank_breadth_reversal_4h']
    print(f"{KEY} {a.side}: {agg['trades']} trades/{env['breadth']['symbols_with_trades']} symbols, "
          f"R {agg['r_sum']}, PF {agg['pf']}, breadth {env['breadth']['breadth_ok']}, "
          f"regime {env['regime_check']['ok']}, packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
