"""Run frozen FDMR-1H with audited point-in-time funding and mark archives.

Research only.  Funding features are injected into the in-memory candle frames;
no runtime registry, PAPER setting, credential, or network endpoint is touched.
"""
import argparse
import bisect
import contextlib
import gzip
import hashlib
import json
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import manifest as M  # noqa: E402
import run_breadth_candidate as B  # noqa: E402

KEY = 'funding_dislocation_mr_1h'
TF, TF_MS = '1h', 3_600_000
FORMAT = 'zb-funding-dislocation-mr-1h-envelope/1'
LOOKBACK_EVENTS, MIN_EVENTS = 90, 60
FRESH_MAX_MS = 61 * 60_000


class FundingDislocationError(RuntimeError):
    pass


def _load_manifest(path):
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        man = json.load(f)
    M.validate(man)
    if man.get('loader_version') != M.ARCHIVE_LOADER or man.get('source_class') != 'archive-verified':
        raise FundingDislocationError('funding manifest is not checksum-verified Binance archive data')
    return man


def _archive_rows(man, store, symbol, series, lo, hi):
    """Read and checksum-verify only archive files that can overlap [lo, hi]."""
    files = [f for f in man['files'] if f.get('series') == series and f.get('symbol') == symbol
             and f.get('first_open_ms') is not None and f['last_open_ms'] >= lo and f['first_open_ms'] <= hi]
    out = []
    for f in sorted(files, key=lambda x: x['first_open_ms']):
        path = M.contained(store, f['path'])
        _, published, data = M.read_archive(path, f['path'])
        if published != f['sha256']:
            raise FundingDislocationError(f'{f["path"]}: archive checksum differs from manifest')
        rows = M.parse_archive_csv(f['path'], data, M.archive_meta(f['path']))
        if len(rows) != f['rows']:
            raise FundingDislocationError(f'{f["path"]}: row count differs from manifest')
        out.extend(rows)
    return out, files


def funding_events(man, store, symbol, lo, hi):
    rows, files = _archive_rows(man, store, symbol, 'fundingRate', lo, hi)
    events = [(int(r[0]), float(r[2])) for r in rows if lo <= int(r[0]) <= hi]
    if any(b[0] <= a[0] for a, b in zip(events, events[1:])):
        raise FundingDislocationError(f'{symbol}: funding events are not strictly increasing')
    return events, files


def mark_points(man, store, symbol, lo, hi):
    rows, files = _archive_rows(man, store, symbol, 'markPriceKlines', lo - TF_MS, hi)
    # A mark candle is usable only after it closes.
    points = [(int(r[0]) + TF_MS, float(r[4])) for r in rows if lo - TF_MS <= int(r[0]) <= hi]
    if any(b[0] <= a[0] for a, b in zip(points, points[1:])):
        raise FundingDislocationError(f'{symbol}: mark candles are not strictly increasing')
    return points, files


def event_z(events):
    out = []
    rates = []
    for t, rate in events:
        hist = rates[-LOOKBACK_EVENTS:]
        z = float('nan')
        if len(hist) >= MIN_EVENTS:
            sd = float(np.std(hist, ddof=1))
            if math.isfinite(sd) and sd > 0:
                z = (rate - float(np.mean(hist))) / sd
        out.append((t, rate, z))
        rates.append(rate)
    return out


def attach_features(df, events):
    """Last event must predate candle open; fresh on the first full post-event candle only."""
    out = df.copy()
    et = [x[0] for x in events]
    z = np.full(len(out), np.nan)
    rate = np.full(len(out), np.nan)
    fresh = np.zeros(len(out), bool)
    for i, t in enumerate(out.t.astype(np.int64)):
        j = bisect.bisect_left(et, int(t)) - 1  # strict: event already known before this open
        if j >= 0:
            age = int(t) - et[j]
            rate[i], z[i] = events[j][1], events[j][2]
            fresh[i] = 0 < age <= FRESH_MAX_MS
    out['funding_rate'], out['funding_z'], out['funding_fresh'] = rate, z, fresh
    return out


def actual_funding(symbol, side, qty, entry_ms, exit_ms, events, marks):
    mt, mp = [x[0] for x in marks], [x[1] for x in marks]
    total = 0.0
    for t, rate, _ in events:
        if entry_ms <= t < exit_ms:
            j = bisect.bisect_right(mt, t) - 1
            if j < 0:
                raise FundingDislocationError(f'{symbol}: no causal mark-price close for funding event {t}')
            total += int(side) * float(rate) * float(qty) * mp[j]
    return total


@contextlib.contextmanager
def one_hour_actual_scope(features, marks):
    """Add 1h and actual funding to the generic runner for this call only."""
    old_tfs, old_read, old_enrich, old_fund = dict(B.TFS), B.read_cache, B.enrich, B.BT.FUND_PER_BAR

    def read_cache(cache, sym, tf):
        df, rec = old_read(cache, sym, tf)
        if df is not None and tf == TF:
            # A late-listed/incomplete candle file is screened out by the shared
            # completeness gate.  It may carry an empty funding feature vector,
            # but can never emit a trade or enter aggregate statistics.
            df = attach_features(df, features.get(sym, []))
        return df, rec

    def enrich(tr, pos, book, reg):
        got = old_enrich(tr, pos, book, reg)
        if not len(got):
            return got
        bt = B._ms(book.t)
        for ix, r in got.iterrows():
            raw = tr.loc[ix]
            p = pos[(raw.sym, int(raw.i_in))]
            entry = int(bt[int(raw.i_in)])
            exit_ = int(bt[int(raw.i_out)]) + TF_MS  # include an event at the exit bar open, exclude next boundary
            fund = actual_funding(raw.sym, int(raw.side), p['qty'], entry, exit_, features[raw.sym], marks[raw.sym])
            pnl = float(got.at[ix, 'gross']) - float(got.at[ix, 'fees']) - fund
            got.at[ix, 'funding'] = fund
            got.at[ix, 'pnl'] = pnl
            got.at[ix, 'R'] = pnl / float(p['risk'])
        return got

    B.TFS[TF] = TF_MS
    B.read_cache, B.enrich, B.BT.FUND_PER_BAR = read_cache, enrich, 0.0
    try:
        yield
    finally:
        B.TFS.clear(); B.TFS.update(old_tfs)
        B.read_cache, B.enrich, B.BT.FUND_PER_BAR = old_read, old_enrich, old_fund


def breadth(pkt):
    a = pkt['stats']['aggregate']
    syms = sorted(s for s, x in pkt['stats']['per_symbol'].items() if int(x.get('trades') or 0) > 0)
    return {'closed_trades': int(a.get('trades') or 0), 'symbols_with_trades': len(syms), 'symbols': syms,
            'breadth_ok': int(a.get('trades') or 0) >= 100 and len(syms) >= 8,
            'rule': '>= 100 closed trades across >= 8 symbols with trades'}


def run(*, cache, regime_cache, funding_manifest, funding_store, window, tier, side, out):
    if os.path.exists(out):
        raise FundingDislocationError(f'{out} exists; packets are never overwritten')
    staging = out + '.staging'
    if any(os.path.exists(p) for p in (staging, staging + '.tmp', out + '.tmp')):
        raise FundingDislocationError('stale staging exists; remove explicitly before rerun')
    st = B.ST.STRATEGIES.get(KEY) or {}
    if st.get('tf') != TF or not st.get('research_only'):
        raise FundingDislocationError('candidate is not the frozen research-only 1h key')
    lo, hi, _ = B.VC.check_window(window, None)
    static = B.VC.frozen_universe()
    syms = sorted(set(B.VC.pick_symbols(static, tier, None)) | {B.BTC})
    man = _load_manifest(funding_manifest)
    history_lo = lo - 45 * 86_400_000
    features, marks, inventory = {}, {}, {}
    for sym in syms:
        ev, ef = funding_events(man, funding_store, sym, history_lo, hi)
        mk, mf = mark_points(man, funding_store, sym, lo, hi)
        features[sym], marks[sym] = event_z(ev), mk
        inventory[sym] = {'funding_files': [f['path'] for f in ef], 'mark_files': [f['path'] for f in mf],
                          'funding_events': len(ev), 'mark_points': len(mk),
                          'coverage_present': bool(ev and mk)}
    try:
        with one_hour_actual_scope(features, marks):
            B.run(cache=cache, window=window, out=staging, key=KEY, side=side,
                  mgmt_mode='defaults', tier=tier, regime_cache=regime_cache)
        with open(staging, 'rb') as f:
            pkt = json.loads(f.read())
        claimed = pkt.pop('packet_sha256', None)
        if claimed != hashlib.sha256(B._canon(pkt)).hexdigest():
            raise FundingDislocationError('staged packet hash mismatch')
        if pkt.get('strategy', {}).get('key') != KEY or pkt.get('window', {}).get('tf') != TF:
            raise FundingDislocationError('staged packet identity mismatch')
        pkt['costs'].update(funding='actual signed settled rate x causal 1h mark-price close', fund_per_bar=None)
        pkt['funding_dislocation_mr_1h'] = {
            'format': FORMAT, 'breadth': breadth(pkt), 'manifest_digest': man['digest'],
            'availability': 'funding calc_time strictly before decision-candle open; mark open+1h <= funding event',
            'inventory': inventory,
            'equity_note': 'trade P&L/R and all trade-derived gates use actual funding; shared bar-equity path omits it',
            'timeframe_scope': '1h added to shared runner TFS for this call only'}
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
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)
    return pkt


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', required=True)
    ap.add_argument('--regime-cache', required=True)
    ap.add_argument('--funding-manifest', required=True)
    ap.add_argument('--funding-store', required=True)
    ap.add_argument('--window', required=True)
    ap.add_argument('--tier', required=True, choices=('CORE-10', 'CORE-20'))
    ap.add_argument('--side', required=True, choices=('long', 'short'))
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    try:
        pkt = run(**vars(a))
    except (B.RunnerError, M.ManifestError, FundingDislocationError) as e:
        print(f'REFUSED: {e}', file=sys.stderr); return 2
    agg, br = pkt['stats']['aggregate'], pkt['funding_dislocation_mr_1h']['breadth']
    print(f"{KEY} {a.side}: {agg['trades']} trades on {br['symbols_with_trades']} symbols, "
          f"R sum {agg['r_sum']}, PF {agg['pf']}, breadth_ok {br['breadth_ok']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
