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
  * `next_event`    the series ends inside the scan window (the event after the last row is due at or before hi).

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


def scan_rows(rows: list, lo: int, hi: int, first_ms: int | None) -> list[dict]:
    """Defects of one symbol's rows over [lo, hi] (rows already limited to the scan span, any order). `first_ms` is
    the symbol's first stored funding row time from the manifest (None = no funding file at all)."""
    out = []
    def add(kind, t, **kw):
        out.append(dict(kind=kind, t_ms=t, utc=utc(t), **kw))
    if first_ms is None:
        add('data_start', lo, detail='no fundingRate file for this symbol')
        return out
    if first_ms > lo:
        add('data_start', first_ms, detail=f'first stored funding row {utc(first_ms)} is after the scan start')
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


def scan(store: str, man: dict, symbols: list[str], lo: int, hi: int) -> dict:
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
        per[s] = {'first_row_utc': None if first is None else utc(first), 'rows_scanned': len(rows),
                  'defects': scan_rows(rows, span_lo, hi, first)}
    return {'format': FORMAT, 'purpose': 'FUND-GAP-01 FG-1 fundingRate continuity inventory (no evaluation)',
            'manifest_digest': man['digest'], 'window': {'lo': utc(lo), 'hi': utc(hi)},
            'scan_span': {'lo': utc(span_lo), 'hi': utc(span_hi)},
            'rules': {'funding_tol_ms': RV.FUNDING_TOL, 'grids_h': list(RV.FUNDING_GRIDS),
                      'anchor_lookback_h': C.MAX_FUNDING_INTERVAL_H},
            'symbols': per, 'defect_count': sum(len(v['defects']) for v in per.values()),
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
        doc = scan(a.store, man, [s for s in a.symbols.split(',') if s], lo, hi)
        doc['provenance'] = {'commit': _git('rev-parse', 'HEAD'), 'clean': _git('status', '--porcelain') == ''}
    except (ScanError, M.ManifestError, OSError, ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    text = json.dumps(doc, sort_keys=True, indent=1) + '\n'
    doc_sha = hashlib.sha256(text.encode()).hexdigest()
    if a.out:
        with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        print(f'{a.out} sha256 {doc_sha} defects {doc["defect_count"]}')
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
