"""REX gap inventory (CORE-10 bounded remedy B/C, prep tooling): every hole in the attested REX store that would make a
range_vwap_rev.v1 row INCOMPLETE for the classes `incomplete.gap` (missing 4h kline -> `missing_bar`) and
`incomplete.funding_mark` (missing 1h mark -> `funding_mark_gap`), per symbol and development split.

AVAILABILITY ONLY. The tool reads timestamps and nothing else:
  * the access window is the REX one (`pit_run.rex_window` + `rex_segments`: explicit START/END inside the frozen
    development window; calibration and the holdout are refused before anything is opened);
  * the manifest is the prereg-pinned attested one (`pit_run.rex_sources`) and the tier symbols are the frozen static
    list (rank prefix), exactly as `pit_run.rex_load` selects them;
  * a file is opened only when its manifest metadata cannot prove it hole-free (row count != the aligned span), or it
    is a fundingRate file (funding instants are irregular); the bytes are re-checked against the published `.ok`
    checksum AND the manifest sha256, and every CSV line is projected to its FIRST field (the open / calc time) at once
    by `time_column` - the only reader in this module. No price, volume, funding-rate, interval or outcome field is
    parsed, stored or returned (enforced by tests that poison every non-time column);
  * times outside the access window are dropped as they are read (nothing on / after the sealed cutoff is kept).

Hole rules (the exact REX-01 rules, `range_vwap_rev`):
  * 4h klines: `read` / `bar_at` / `bps_at` raise `incomplete.gap` for any missing open; a hole is a missing 4h open
    inside a split segment [lo, hi) strictly between the symbol's first and last archived open (before the first bar
    the symbol is simply not eligible; a missing tail is `incomplete.delisted`, reported only as `series_end`);
  * 1h marks: `mark_at(T)` reads exactly the 1h mark bar OPENING at T - 1h (the bar ending at the nominal funding
    instant T); no nearest / earlier mark is accepted. T is the funding row time rounded to the hour (`t_nom`; a row
    more than 60 s off the hour is `incomplete.funding`, a different class, counted under `off_grid_funding` and not
    listed).
    Inside segment [lo, hi] the clip keeps marks with open >= lo and open + 1h <= hi, so the events checked are
    lo + 1h <= T <= hi after the segment's first decision.
Each hole is a maximal run of missing opens, end-exclusive, cut at split boundaries, labelled with its split.

Usage (repo root; reads the store's time columns only, writes one JSON report):
  py -3.14 tools/research/rex_gap_inventory.py --store DIR --tier CORE-10 \
      --window 2021-01-04T00:00:00Z/2026-09-28T00:00:00Z --out REPORT.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402
import splits as S                                                                          # noqa: E402

FORMAT = 'zb-rex-gap-inventory/1'
HOUR, TF = RV.HOUR, RV.TF
KLINES = ('klines', '4h')
MARKS = ('markPriceKlines', '1h')
FUNDING = ('fundingRate', None)
REASON = {KLINES: 'incomplete.gap', MARKS: 'incomplete.funding_mark'}


class InventoryError(ValueError):
    pass


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


# ------------------------------------------------------------------ the only data reader: first CSV field
def time_column(data: bytes) -> tuple:
    """Project every CSV line to its first field (open_time / calc_time) as int, skipping a header. Nothing after the
    first comma is ever split, decoded or parsed."""
    out = []
    for ln in data.split(b'\n'):
        head = ln.split(b',', 1)[0].strip()
        if not head or head in (b'open_time', b'calc_time'):
            continue
        if not head.isdigit():
            raise InventoryError(f'non-numeric time field {head[:16]!r}')
        out.append(int(head))
    return tuple(out)


def file_times(store: str, f: dict, lo: int, hi: int) -> tuple:
    """The window-bounded times of one manifest file (sha256 re-checked against the sidecar and the manifest)."""
    p = f['path']
    _, published, data = M.read_archive(M.contained(store, p), p)
    if published != f['sha256']:
        raise InventoryError(f'{p}: bytes no longer match the manifest sha256')
    t = time_column(data)
    del data                                                 # the full CSV never outlives the projection
    if len(t) != f['rows'] or (t and (t[0] != f['first_open_ms'] or t[-1] != f['last_open_ms'])):
        raise InventoryError(f'{p}: time column disagrees with the manifest rows / coverage')
    return tuple(x for x in t if lo <= x <= hi)


def proven_full(f: dict, iv: int) -> bool:
    """Manifest metadata alone proves the file has no interior hole (rows strictly increasing + aligned, checked at
    manifest build): rows == the aligned span count."""
    return bool(f['rows']) and f['rows'] == (f['last_open_ms'] - f['first_open_ms']) // iv + 1


def index(manifest: dict, store: str, symbols, lo: int, hi: int, *, reader=file_times) -> dict:
    """{(series, symbol, interval): {'times': sorted window times, 'first': first archived open, 'last': ..., 'paths':
    [(path, month_start, month_end)]}} for the REX series of the tier symbols. Only times are produced."""
    M.validate(manifest)
    if manifest['loader_version'] != M.ARCHIVE_LOADER:
        raise InventoryError(f'the REX store is the {M.ARCHIVE_LOADER} archive (got {manifest["loader_version"]})')
    want = set(symbols)
    files: dict = {}
    for f in manifest['files']:
        if f['symbol'] in want:
            key = (f['series'], f['symbol'], f['interval'])
            if (key[0], key[2]) in (KLINES, MARKS, FUNDING):
                files.setdefault(key, []).append(f)
    out = {}
    for key, fs in sorted(files.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or '')):
        fs.sort(key=lambda f: (f['first_open_ms'] is None, f['first_open_ms'] or 0))
        P._refuse_overlap(key, fs)
        iv = M.INTERVALS[key[2]] if key[2] else None
        times, firsts, lasts, paths = [], [], [], []
        for f in fs:
            meta = M.archive_meta(f['path'])
            paths.append((f['path'], meta['month_start_ms'], meta['month_end_ms']))
            if not f['rows']:
                continue
            firsts.append(f['first_open_ms'])
            lasts.append(f['last_open_ms'])
            if f['last_open_ms'] < lo or f['first_open_ms'] > hi:
                continue
            if iv and proven_full(f, iv):
                a = max(f['first_open_ms'], lo)
                a += -(a - f['first_open_ms']) % iv              # stay on the file's (aligned) open grid
                times.extend(range(a, min(f['last_open_ms'], hi) + 1, iv))
            else:
                times.extend(reader(store, f, lo, hi))
        out[key] = {'times': sorted(set(times)), 'first': min(firsts) if firsts else None,
                    'last': max(lasts) if lasts else None, 'paths': paths}
    return out


# ------------------------------------------------------------------ holes (pure: times in, intervals out)
def _runs(missing, step):
    """Maximal runs of consecutive missing opens -> [(start, end_exclusive, count)]."""
    out = []
    for t in missing:
        if out and out[-1][1] == t:
            out[-1] = (out[-1][0], t + step, out[-1][2] + 1)
        else:
            out.append((t, t + step, 1))
    return out


def _store_paths(entry, a, b):
    return sorted(p for p, ms, me in (entry or {}).get('paths', ()) if ms < b and a < me)


def _hole(sym, key, seg, a, b, n, entry, extra=None):
    series, interval = key
    reason = REASON[key]
    h = {'symbol': sym, 'series': series, 'interval': interval, 'start_ms': a, 'end_ms': b, 'start_utc': S.utc(a),
         'end_utc': S.utc(b), 'missing': n, 'reason': reason, 'class': RV.GAP_CLASS[reason], 'split': seg['key'],
         'split_name': seg['name'], 'store_files': _store_paths(entry, a, b)}
    if extra:
        h.update(extra)
    return h


def bar_holes(sym, entry, segments):
    out, ends = [], []
    if not entry or entry['first'] is None:
        return out, ends
    have = set(entry['times'])
    for seg in segments:
        lo, hi = seg['lo'], seg['hi']
        a = max(lo, entry['first'])
        a += -(a - lo) % TF                                  # first 4h open on the segment grid
        miss = [o for o in range(a, hi, TF) if o not in have]
        tail = [o for o in miss if o > entry['last']]
        if tail:
            ends.append({'symbol': sym, 'series': KLINES[0], 'split': seg['key'], 'from_ms': tail[0],
                         'from_utc': S.utc(tail[0]), 'reason': 'incomplete.delisted'})
        for s0, e0, n in _runs([o for o in miss if o <= entry['last']], TF):
            out.append(_hole(sym, KLINES, seg, s0, e0, n, entry))
    return out, ends


def funding_instants(times):
    """Nominal funding instants T (row time rounded to the hour, within the 60 s tolerance) and the off-grid count."""
    out, off = set(), 0
    for t in times:
        h = (t + HOUR // 2) // HOUR * HOUR
        if abs(t - h) > RV.FUNDING_TOL:
            off += 1
        else:
            out.add(h)
    return sorted(out), off


def mark_holes(sym, mentry, fentry, segments):
    out, off_total = [], 0
    if not fentry:
        return out, 0
    have = set((mentry or {}).get('times', ()))
    for seg in segments:
        lo, hi = seg['lo'], seg['hi']
        ts = [t for t in fentry['times'] if lo <= t <= hi + PR.REX_FUNDING_TAIL_MS]
        inst, off = funding_instants(ts)
        off_total += off
        need = [T for T in inst if T > seg['first'] and T - HOUR >= lo and T <= hi]
        miss = sorted({T - HOUR: T for T in need if T - HOUR not in have}.items())
        by_open = dict(miss)
        for s0, e0, n in _runs([o for o, _ in miss], HOUR):
            evs = [by_open[o] for o in range(s0, e0, HOUR)]
            out.append(_hole(sym, MARKS, seg, s0, e0, n, mentry,
                             {'funding_events_ms': evs, 'funding_events_utc': [S.utc(x) for x in evs]}))
    return out, off_total


def inventory(idx: dict, symbols, segments) -> dict:
    holes, ends, off = [], [], {}
    for sym in symbols:
        kb = idx.get((KLINES[0], sym, KLINES[1]))
        h, e = bar_holes(sym, kb, segments)
        holes += h
        ends += e
        mh, n = mark_holes(sym, idx.get((MARKS[0], sym, MARKS[1])), idx.get((FUNDING[0], sym, None)), segments)
        holes += mh
        if n:
            off[sym] = n
    holes.sort(key=lambda h: (h['symbol'], h['series'], h['start_ms']))
    counts = {}
    for h in holes:
        k = f'{h["reason"]}|{h["split"]}'
        counts[k] = counts.get(k, 0) + h['missing']
    return {'holes': holes, 'series_end': ends, 'off_grid_funding': off, 'missing_by_reason_split': counts}


def build(manifest: dict, store: str, *, symbols, window, plan, tier, reader=file_times) -> dict:
    """The deterministic report. The window guard runs before any file is opened."""
    lo, hi = PR.rex_window(window, plan)
    segments = PR.rex_segments(plan, lo, hi)
    idx = index(manifest, store, symbols, lo, hi + PR.REX_FUNDING_TAIL_MS, reader=reader)
    body = inventory(idx, list(symbols), segments)
    doc = {'format': FORMAT, 'candidate_id': RV.CANDIDATE, 'tier': tier, 'symbols': list(symbols),
           'manifest_digest': manifest['digest'], 'window': {'start': S.utc(lo), 'end': S.utc(hi)},
           'segments': [{k: s[k] for k in ('key', 'name', 'lo', 'hi', 'first')} for s in segments],
           'rules': {'klines': '4h open missing in [seg lo, seg hi) between first and last archived open',
                     'mark': 'mark_at(T): the 1h mark bar opening at T - 1h, T = funding time rounded to the hour '
                             '(+-60 s), seg first < T <= seg hi'},
           'reads': 'time column only (first CSV field); no price / volume / rate / outcome', **body}
    doc['digest'] = hashlib.sha256(canon(doc)).hexdigest()
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--store', required=True)
    ap.add_argument('--tier', choices=PR.RUN_TIERS, default='CORE-10')
    ap.add_argument('--window', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    repo = M.REPO
    plan = PR.rex_plan(repo)
    PR.rex_window(a.window, plan)                            # refuse before the manifest / store is touched
    src = PR.rex_sources(repo)
    symbols = list(src['static']['core_10' if a.tier == 'CORE-10' else 'core_20'])
    doc = build(src['manifest'], a.store, symbols=symbols, window=a.window, plan=plan, tier=a.tier)
    with open(a.out, 'w', newline='\n') as f:
        json.dump(doc, f, sort_keys=True, indent=1)
        f.write('\n')
    print(f'{len(doc["holes"])} hole(s) {doc["missing_by_reason_split"]} -> {a.out} (digest {doc["digest"][:16]})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
