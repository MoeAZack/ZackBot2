"""EDGE05 prep: metadata-only data-coverage check for `funding_carry_xsec.v1` (DRAFT prereg, no evaluation).

Reads ONLY manifest metadata (`zb-data-manifest/1` file entries: series, symbol, interval, path month, rows,
first/last timestamps). It never opens an archive zip, never reads a price, rate or volume, and never computes a
return, P&L or R. The manifest is parsed as a stream, one file entry at a time, so memory stays flat however large
the manifest is (the committed archive manifest is ~7 MB gzip).

Sealed holdout: nothing on or after SEALED_CUTOFF (2026-09-28T00:00:00Z) is ever reported.
  * `--end` later than the cutoff is refused before any read;
  * an entry whose first timestamp is at or after `--end` is skipped and only counted (`sealed_skipped`);
  * an entry that straddles `--end` is listed as `straddle` (its month is present, its rows are NOT counted and
    its timestamps are not reported);
  * the output is re-scanned before it is printed and the tool refuses if any reported ms is >= the end.

Fail closed: a universe symbol with no fundingRate entry in the window, or with a missing month between its first
and last covered month, makes the status INCOMPLETE (exit code 3). Coverage is month-granular here; the
settlement-level continuity check (`costs.check_funding_sequence`) belongs to the frozen run, not to this prep.

  python tools/research/edge05_coverage.py [--manifest PATH] [--universe PATH] [--start Z] [--end Z]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from datetime import datetime, timezone

FORMAT = 'zb-edge05-coverage/1'
SEALED_CUTOFF = '2026-09-28T00:00:00Z'
DEFAULT_START = '2020-02-03T00:00:00Z'                     # EDGE04/R4 calibration start
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST_PATH = 'research_evidence/manifests/binance-um-archive-v1.json.gz'
UNIVERSE_PATH = 'research_evidence/universe/static-top40-minhist365-contract-20261011.json'
# (series, interval) pairs the frozen run needs: settled funding, 4h execution bars, 1d ATR bars, 1h funding mark.
REQUIRED = (('fundingRate', None), ('klines', '4h'), ('klines', '1d'), ('markPriceKlines', '1h'))
MONTH_RE = re.compile(r'-(\d{4})-(\d{2})\.zip$')
_STR_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
MS_KEYS = ('first_open_ms', 'last_open_ms')


class CoverageError(Exception):
    """Refuse: holdout boundary, malformed input. Nothing is printed."""


def parse_utc(s: str) -> int:
    if not isinstance(s, str) or not s.endswith('Z'):
        raise CoverageError(f'{s!r}: times are UTC and end in Z')
    return int(datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc).timestamp() * 1000)


def month_key(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return f'{d.year:04d}-{d.month:02d}'


def months_between(a: str, b: str) -> list[str]:
    """Inclusive list of YYYY-MM from a to b."""
    y, m = int(a[:4]), int(a[5:])
    out = []
    while f'{y:04d}-{m:02d}' <= b:
        out.append(f'{y:04d}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _open(path: str):
    return gzip.open(path, 'rt', encoding='utf-8') if path.endswith('.gz') else open(path, encoding='utf-8')


def iter_file_entries(path: str):
    """Yield each object of the top-level `files` array, one at a time, from a pretty-printed manifest.

    Depth is tracked per line with string literals removed; an entry is buffered only while it is open, so the
    whole manifest is never held in memory. A single-line (minified) manifest is refused rather than loaded."""
    depth = 0
    in_files = False
    buf: list[str] = []
    with _open(path) as f:
        for line in f:
            if len(line) > 1_000_000:
                raise CoverageError('manifest is not line-oriented (minified); refusing to load it whole')
            bare = _STR_RE.sub('""', line)
            if depth == 1 and not in_files and '"files"' in line and '[' in bare:
                in_files = True
                depth += bare.count('{') + bare.count('[') - bare.count('}') - bare.count(']')
                continue
            before = depth
            depth += bare.count('{') + bare.count('[') - bare.count('}') - bare.count(']')
            if in_files:
                if before == 2 and bare.lstrip().startswith('{'):
                    buf = [line]                                   # an entry opens (possibly on one line)
                elif before >= 3:
                    buf.append(line)
                if buf and depth == 2:
                    yield json.loads(''.join(buf).strip().rstrip(','))
                    buf = []
                if depth == 1:
                    in_files = False
            if depth < 0:
                raise CoverageError('manifest braces do not balance')


def load_universe(path: str) -> list[str]:
    with open(path, encoding='utf-8') as f:
        u = json.load(f)                                           # small (40 symbols)
    syms = [r['symbol'] if isinstance(r, dict) else r for r in u['symbols']]
    if len(set(syms)) != len(syms) or not syms:
        raise CoverageError('universe symbols must be non-empty and unique')
    return syms


def coverage(manifest_path: str, symbols: list[str], start: str = DEFAULT_START, end: str = SEALED_CUTOFF) -> dict:
    lo, hi, cut = parse_utc(start), parse_utc(end), parse_utc(SEALED_CUTOFF)
    if hi > cut:
        raise CoverageError(f'end {end} is after the sealed holdout cutoff {SEALED_CUTOFF}; refused before any read')
    if lo >= hi:
        raise CoverageError('start must be before end')
    want = set(symbols)
    # per (series, interval) -> symbol -> {month: rows or None for straddle}
    seen: dict = {req: {s: {} for s in symbols} for req in REQUIRED}
    sealed_skipped = 0
    for e in iter_file_entries(manifest_path):
        req = (e.get('series'), e.get('interval'))
        if req not in seen or e.get('symbol') not in want:
            continue
        first, last = e.get('first_open_ms'), e.get('last_open_ms')
        if type(first) is not int or type(last) is not int or first > last:
            raise CoverageError(f'{e.get("path")}: missing or bad first/last timestamps')
        if first >= hi:
            sealed_skipped += 1                                    # holdout (or beyond the window): never reported
            continue
        if last < lo:
            continue
        m = MONTH_RE.search(e.get('path', ''))
        if not m:
            raise CoverageError(f'{e.get("path")}: not a monthly archive path')
        month = f'{m.group(1)}-{m.group(2)}'
        straddle = last >= hi
        rows = e.get('rows')
        if not straddle and type(rows) is not int:
            raise CoverageError(f'{e.get("path")}: rows missing')
        seen[req][e['symbol']][month] = None if straddle else rows
    out_series = {}
    status = 'COMPLETE'
    for req in REQUIRED:
        name = req[0] if req[1] is None else f'{req[0]}:{req[1]}'
        per = {}
        missing_symbols = []
        for s in symbols:
            ms = seen[req][s]
            if not ms:
                missing_symbols.append(s)
                continue
            first_m, last_m = min(ms), max(ms)
            gaps = [x for x in months_between(first_m, last_m) if x not in ms]
            per[s] = {'first_month': first_m, 'last_month': last_m, 'months_present': len(ms),
                      'months_missing': gaps, 'rows_full_months': sum(v for v in ms.values() if v is not None),
                      'straddle_months_unread': sorted(k for k, v in ms.items() if v is None)}
        by_month: dict = {}
        for s in symbols:
            for mo in seen[req][s]:
                by_month[mo] = by_month.get(mo, 0) + 1
        gap_syms = sorted(s for s, v in per.items() if v['months_missing'])
        if missing_symbols or gap_syms:
            status = 'INCOMPLETE'
        out_series[name] = {'symbols_with_data': len(per), 'missing_symbols': missing_symbols,
                            'symbols_with_month_gaps': gap_syms, 'symbols_per_month': dict(sorted(by_month.items())),
                            'per_symbol': per}
    fr = out_series['fundingRate']['symbols_per_month']
    doc = {'format': FORMAT, 'status': status, 'window': {'start': start, 'end_exclusive': end},
           'sealed_cutoff': SEALED_CUTOFF, 'universe_size': len(symbols), 'sealed_skipped_entries': sealed_skipped,
           'funding_symbols_per_month_min': min(fr.values()) if fr else 0,
           'funding_symbols_per_month_max': max(fr.values()) if fr else 0,
           'first_month_with_15_funding_symbols': next((k for k, v in fr.items() if v >= 15), None),
           'series': out_series,
           'note': 'metadata only: no archive opened, no price/rate read, no return computed'}
    _assert_no_holdout(doc, hi)
    return doc


def _assert_no_holdout(doc, hi_ms: int) -> None:
    """Fail closed if anything reported reaches the window end (months are compared by their first instant)."""
    hi_month = month_key(hi_ms)
    for name, s in doc['series'].items():
        for mo in s['symbols_per_month']:
            if mo > hi_month:
                raise CoverageError(f'{name}: reported month {mo} is past the window end')
        for v in s['per_symbol'].values():
            for k in MS_KEYS:
                if k in v:
                    raise CoverageError('raw timestamps must not be reported')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--manifest', default=os.path.join(REPO, MANIFEST_PATH))
    ap.add_argument('--universe', default=os.path.join(REPO, UNIVERSE_PATH))
    ap.add_argument('--start', default=DEFAULT_START)
    ap.add_argument('--end', default=SEALED_CUTOFF)
    ap.add_argument('--summary', action='store_true', help='omit per-symbol detail')
    a = ap.parse_args(argv)
    try:
        if parse_utc(a.end) > parse_utc(SEALED_CUTOFF):            # before any read, including the universe
            raise CoverageError(f'end {a.end} is after the sealed holdout cutoff {SEALED_CUTOFF}')
        doc = coverage(a.manifest, load_universe(a.universe), a.start, a.end)
    except CoverageError as ex:
        print(f'REFUSED: {ex}', file=sys.stderr)
        return 2
    if a.summary:
        for s in doc['series'].values():
            s.pop('per_symbol')
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0 if doc['status'] == 'COMPLETE' else 3


if __name__ == '__main__':
    sys.exit(main())
