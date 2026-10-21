"""FUND-GAP-01 FG-1: read-only fundingRate continuity inventory for the residual CORE-10 funding-rate gaps (#13).

The REX packet `rex-core10-gapfill-splitcut-v1` is INCOMPLETE for four evaluation rows (walk_forward[0], long,
range_vwap_rev.v1.rg+0.5 and B2 x base / fees_slip_x2), all `incomplete.funding` / `funding_rate_gap`. That reason is
raised only by `range_vwap_rev.t_nom` / `funding_events`. This tool applies those exact rules to the STORED fundingRate
series - no trade, no position, no PnL, no evaluation - and lists every defect with its trigger:

  * `time_tol`      a row more than FUNDING_TOL (60 s) from its whole hour;
  * `interval_type` interval_hours not an int in FUNDING_GRIDS (1/2/4/8);
  * `grid`          the nominal hour is off its own interval grid from 00:00 UTC;
  * `duplicate`     two rows with the same nominal instant;
  * `spacing`       consecutive nominal instants not exactly one interval (of either row) apart;
  * `data_start`    the symbol's first stored funding row lies after the scan start (no phase anchor possible
                    before it - a listing / data-start edge, not a mid-series hole);
  * `next_event`    the series ends inside the scan window (the event after the last row is due at or before hi);
  * `clip_anchor`   in the evaluator's `rex_clip` view (rows with time_ms >= window lo) the first kept row does not
                    anchor the first interval.
Every defect has `in_window`; a `data_start` after the window end is `in_window: false` (not listed, non-causal).

G2 (Codex 6316137350): per symbol, `evaluator_equivalence` compares the scanner's rows with the rows the evaluator
path actually delivers to the window (`rex_series` -> `rex_clip(lo, hi)`), by timestamp and TYPED interval_hours:
counts, digests and the first mismatch only. No Access is opened and nothing is recorded in a ledger.

Inputs are the committed attested manifest (`pit_run.MANIFEST_PATH`) and its archive store; every file read is
re-proved against the manifest sha256 and its `.ok` sidecar. Only fundingRate files overlapping the scan span are read.
A scan end after the sealed holdout boundary is refused before any read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import manifest as M                                                                        # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402

FORMAT = 'zb-fundgap-scan/1'
HOUR = RV.HOUR
SEALED_END = '2026-09-28T00:00:00Z'
MANIFEST_PATH = 'research_evidence/manifests/binance-um-archive-v1.json.gz'
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ScanError(Exception):
    """Refuse: holdout, digest or input failure. Nothing is written."""


def parse_utc(s: str) -> int:
    if not s.endswith('Z'):
        raise ScanError(f'{s}: times are UTC and end in Z')
    return int(datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc).timestamp() * 1000)


def utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


class Row:
    __slots__ = ('time_ms', 'interval_hours', 'file')

    def __init__(self, time_ms, interval_hours, file=None):
        self.time_ms, self.interval_hours, self.file = time_ms, interval_hours, file


def _classify(r: Row) -> str | None:
    """None when `RV.t_nom` accepts the row, else the trigger it fails on (same predicate, split by cause)."""
    try:
        RV.t_nom(r)
        return None
    except RV.Incomplete:
        pass
    h = (r.time_ms + HOUR // 2) // HOUR * HOUR
    if abs(r.time_ms - h) > RV.FUNDING_TOL:
        return 'time_tol'
    if type(r.interval_hours) is not int or r.interval_hours not in RV.FUNDING_GRIDS:
        return 'interval_type'
    return 'grid'


def scan_rows(rows: list, lo: int, hi: int, first_ms: int | None, clip_lo: int | None = None) -> list[dict]:
    """Defects of one symbol's rows over [lo, hi] (rows already limited to the scan span, any order). `first_ms` is
    the symbol's first stored funding row time from the manifest (None = no funding file at all). Every defect
    carries `in_window`: False only for a `data_start` after `hi` (the symbol was not listed in the window, so it
    cannot cause a trade row there). `clip_lo` (the window start) adds the evaluator-view check `clip_anchor`:
    `pit_run.rex_clip` keeps only rows with time_ms >= clip_lo, so the first kept row must anchor the first
    interval (its nominal instant at most one interval after clip_lo)."""
    out = []
    def add(kind, t, in_window=True, **kw):
        out.append(dict(kind=kind, t_ms=t, utc=utc(t), in_window=in_window, **kw))
    if first_ms is None:
        add('data_start', lo, in_window=False, detail='no fundingRate file for this symbol')
        return out
    if first_ms > lo:
        add('data_start', first_ms, in_window=first_ms <= hi,
            detail=f'first stored funding row {utc(first_ms)} is after the scan start'
                   + ('' if first_ms <= hi else ' and after the window end: not listed in the window, non-causal'))
    if clip_lo is not None and first_ms <= clip_lo:
        kept = sorted((r for r in rows if r.time_ms >= clip_lo and _classify(r) is None), key=lambda r: r.time_ms)
        if not kept or RV.t_nom(kept[0]) > clip_lo + kept[0].interval_hours * HOUR:
            add('clip_anchor', kept[0].time_ms if kept else clip_lo,
                detail='the rex_clip view (time_ms >= window lo) starts more than one interval after lo')
    good = []
    for r in sorted(rows, key=lambda r: r.time_ms):
        k = _classify(r)
        if k is None:
            good.append((RV.t_nom(r), r))
        else:
            add(k, r.time_ms, interval_hours=r.interval_hours, file=r.file)
    for (ta, ra), (tb, rb) in zip(good, good[1:]):
        if tb == ta:
            add('duplicate', tb, files=sorted({ra.file, rb.file} - {None}))
        elif (tb - ta) not in {ra.interval_hours * HOUR, rb.interval_hours * HOUR}:
            step = ra.interval_hours * HOUR
            add('spacing', ta, next_t_ms=tb, next_utc=utc(tb), gap_h=(tb - ta) / HOUR,
                expected_h=sorted({ra.interval_hours, rb.interval_hours}),
                missing_at_prev_interval=[utc(t) for t in range(ta + step, tb, step)][:16],
                files=sorted({ra.file, rb.file} - {None}))
    if good:
        nxt = good[-1][0] + good[-1][1].interval_hours * HOUR
        if nxt <= hi:
            add('next_event', nxt, detail=f'series ends at {utc(good[-1][0])}; next event due inside the window',
                file=good[-1][1].file)
    return out


def evaluator_funding(ds, symbol: str, lo: int, hi: int, acc_lo: int | None = None, acc_hi: int | None = None):
    """G2: the funding rows the REX evaluator delivers to a split [lo, hi]. `pit_run.rex_series` reads them as
    `View(w.hi).funding(s, w.lo)` of an access window [acc_lo, acc_hi + tail] (`View._rows`: per manifest file,
    newest first and prepended, `Dataset.slice` time >= acc_lo and available_ms <= acc_hi + tail), and `run_rex`
    clips each split with `pit_run.rex_clip(lo, hi)`. A Window is opened only through a ledger-recorded Access, so the
    `View._rows` loop is mirrored here over the same `Dataset.files` / `Dataset.slice` (proved identical to the real
    Access path by the tests) and `rex_clip` is the real function. The access window defaults to the split itself
    (`rex_clip` gives the same rows for any access window covering it). A file reaching the sealed holdout refuses."""
    import pit_run as PR                                                                    # noqa: E402
    acc_lo = lo if acc_lo is None else acc_lo
    t = (hi if acc_hi is None else acc_hi) + PR.REX_FUNDING_TAIL_MS
    out = []
    for f in reversed(ds.files('fundingRate', symbol, None)):
        if f['first_open_ms'] is None or f['first_open_ms'] > t or f['last_open_ms'] < acc_lo:
            continue
        if f['last_open_ms'] >= parse_utc(SEALED_END):
            raise ScanError(f'{symbol}: an evaluator-path file reaches the sealed holdout; refusing')
        out = ds.slice(f, acc_lo, t) + out
    return PR.rex_clip(RV.Series({}, {}, tuple(out), {}), lo, hi).funding


def row_key(time_ms, interval_hours) -> list:
    """The compared identity of a funding row: timestamp and the TYPED interval (an 8.0 is not an 8)."""
    return [time_ms, interval_hours, type(interval_hours).__name__]


def _digest(keys: list) -> str:
    return hashlib.sha256(json.dumps(keys, separators=(',', ':')).encode()).hexdigest()


def equivalence(rows: list, delivered, lo: int, hi: int) -> dict:
    """Scanner rows (restricted to the rex_clip view: time_ms >= lo, available = time_ms <= hi + tail) vs the
    evaluator-delivered rows, both by time: counts, digests and only the first mismatch (no rate, no result)."""
    a = [row_key(r.time_ms, r.interval_hours)
         for r in sorted(rows, key=lambda r: r.time_ms) if lo <= r.time_ms <= hi + RV.FUNDING_TOL]
    b = [row_key(r.time_ms, r.interval_hours) for r in sorted(delivered, key=lambda r: r.time_ms)]
    first = None
    for i in range(max(len(a), len(b))):
        x, y = (a[i] if i < len(a) else None), (b[i] if i < len(b) else None)
        if x != y:
            first = {'index': i, 'scanner': x, 'evaluator': y}
            break
    return {'scanner_rows': len(a), 'evaluator_rows': len(b), 'scanner_digest': _digest(a),
            'evaluator_digest': _digest(b), 'equal': first is None, 'first_mismatch': first}


def evaluator_dataset(man: dict, store: str, symbols: list[str]):
    """The evaluator's StaticDataset (the rank prefix covering `symbols`) over the same attested manifest; metadata
    and the committed universe only, no Access and no ledger record."""
    import pit_run as PR                                                                    # noqa: E402
    src = PR.rex_sources(REPO)
    if src['manifest']['digest'] != man['digest']:
        raise ScanError('the evaluator sources pin a different manifest digest')
    ranked = [x['symbol'] for x in src['static']['symbols']]
    if any(s not in ranked for s in symbols):
        raise ScanError(f'{sorted(set(symbols) - set(ranked))} not in the static universe')
    return PR.StaticDataset(man, store, src['static'], ranked[:max(ranked.index(s) for s in symbols) + 1])


def _git(*args) -> str:
    return subprocess.run(['git', '-C', REPO, *args], capture_output=True, text=True, check=True).stdout.strip()


def _read_funding(store: str, f: dict) -> list[Row]:
    p = f['path']
    _, published, data = M.read_archive(M.contained(store, p), p)
    if published != f['sha256']:
        raise ScanError(f'{p}: bytes no longer match the manifest sha256')
    raw = M.parse_archive_csv(p, data, M.archive_meta(p))
    if len(raw) != f['rows']:
        raise ScanError(f'{p}: {len(raw)} rows != manifest {f["rows"]}')
    return [Row(int(r[0]), int(r[1]), p) for r in raw]


def scan(store: str, man: dict, symbols: list[str], lo: int, hi: int, ds=None) -> dict:
    if hi > parse_utc(SEALED_END):
        raise ScanError(f'scan end {utc(hi)} is after the sealed holdout boundary {SEALED_END}; refusing')
    span_lo = lo - C.MAX_FUNDING_INTERVAL_H * HOUR - RV.FUNDING_TOL
    span_hi = hi + RV.FUNDING_TOL
    files, per = [], {}
    for s in symbols:
        fs = sorted((f for f in man['files'] if f.get('series') == 'fundingRate' and f['symbol'] == s),
                    key=lambda f: f['first_open_ms'] or 0)
        first = min((f['first_open_ms'] for f in fs if f['first_open_ms'] is not None), default=None)
        use = [f for f in fs if f['first_open_ms'] is not None
               and f['first_open_ms'] <= span_hi and f['last_open_ms'] >= span_lo]
        if any(f['last_open_ms'] >= parse_utc(SEALED_END) for f in use):
            raise ScanError(f'{s}: a selected file reaches the sealed holdout; refusing')
        rows = [r for f in use for r in _read_funding(store, f) if span_lo <= r.time_ms <= span_hi]
        files += [{'path': f['path'], 'sha256': f['sha256'], 'rows': f['rows']} for f in use]
        kept = [r for r in rows if r.time_ms >= lo]
        per[s] = {'first_row_utc': None if first is None else utc(first), 'rows_scanned': len(rows),
                  'first_clipped_row_utc': utc(min(r.time_ms for r in kept)) if kept else None,
                  'defects': scan_rows(rows, span_lo, hi, first, clip_lo=lo)}
        if ds is not None:
            per[s]['evaluator_equivalence'] = equivalence(rows, evaluator_funding(ds, s, lo, hi), lo, hi)
    eq = {} if ds is None else {
        'evaluator_path': 'pit_run.rex_series (View.funding over Dataset.slice) -> pit_run.rex_clip(lo, hi)',
        'evaluator_mismatch_symbols': sorted(s for s, v in per.items() if not v['evaluator_equivalence']['equal'])}
    return {**eq,'format': FORMAT, 'purpose': 'FUND-GAP-01 FG-1 fundingRate continuity inventory (no evaluation)',
            'manifest_digest': man['digest'], 'window': {'lo': utc(lo), 'hi': utc(hi)},
            'scan_span': {'lo': utc(span_lo), 'hi': utc(span_hi)},
            'rules': {'funding_tol_ms': RV.FUNDING_TOL, 'grids_h': list(RV.FUNDING_GRIDS),
                      'anchor_lookback_h': C.MAX_FUNDING_INTERVAL_H},
            'symbols': per, 'defect_count': sum(len(v['defects']) for v in per.values()),
            'in_window_defect_count': sum(d['in_window'] for v in per.values() for d in v['defects']),
            'files_read': files}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--store', required=True, help='the binance_um archive store of the attested manifest')
    ap.add_argument('--symbols', required=True, help='comma-separated, e.g. BTCUSDT,ETHUSDT')
    ap.add_argument('--window', required=True, help='LO/HI in UTC, e.g. 2021-12-20T00:00:00Z/2022-12-19T00:00:00Z')
    ap.add_argument('--out', default=None, help='write the report JSON here (default: stdout)')
    a = ap.parse_args(argv)
    try:
        lo_s, sep, hi_s = a.window.partition('/')
        if not sep:
            raise ScanError('--window is LO/HI')
        lo, hi = parse_utc(lo_s), parse_utc(hi_s)
        if lo >= hi:
            raise ScanError('--window LO must be before HI')
        if hi > parse_utc(SEALED_END):
            raise ScanError(f'scan end {hi_s} is after the sealed holdout boundary {SEALED_END}; refusing')
        man = M.load(os.path.join(REPO, MANIFEST_PATH))
        symbols = [s for s in a.symbols.split(',') if s]
        doc = scan(a.store, man, symbols, lo, hi, ds=evaluator_dataset(man, a.store, symbols))
        doc['provenance'] = {'commit': _git('rev-parse', 'HEAD'), 'clean': _git('status', '--porcelain') == ''}
    except (ScanError, M.ManifestError, OSError, ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    text = json.dumps(doc, sort_keys=True, indent=1) + '\n'
    doc_sha = hashlib.sha256(text.encode()).hexdigest()
    if a.out:
        with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        print(f'{a.out} sha256 {doc_sha} defects {doc["defect_count"]} in-window {doc["in_window_defect_count"]}')
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
