"""Read-only gap checker for the local candle files (DATA-01 pre-stage; docs/newcore/research/DATA01_RES01_DRAFT.md 5.1).

Scans every `<SYMBOL>_<interval>.csv` under the data roots (default: the repo's data/, data1h/ and data_long/) and
reports, per file, in the draft's gap classes:

  G1  missing interval      a step between consecutive keys larger than the interval (each missing open listed)
  G2  duplicate key         the same open time twice (identical or conflicting values)
  G3  OHLC / value invariant high >= max(open, close), low <= min(open, close), high >= low, prices > 0, volume >= 0,
                            every field present and numeric
  G4  zero-volume / flat    volume == 0, or high == low (kept, flagged: a strategy may not enter on such a bar)
  K1  misaligned open       open time not on the interval grid (epoch-aligned, as Binance klines are)
  K2  non-monotonic time    a key earlier than the one before it in file order
  S1  stale tail            the file ends earlier than the newest file of the same interval (lag in bars)
  X1  cross-source mismatch the same symbol / interval / open time in two roots with different values
  X2  cross-timeframe       a coarse bar differs from the aggregate of the finer bars covering its span (a candle
                            stored while still open, or a bad resample)
  P1  provenance            the file disagrees with DATA_MANIFEST.json (sha256 / rows / first / last), or is missing
                            from it, or a manifest entry has no file

G5 (venue maintenance) and F1 (funding cadence) need venue data this checker does not have; they are not checked.

Severity: G1 G2 G3 K1 K2 X1 X2 P1 are errors, G4 and S1 are warnings. Exit 0 = no error (warnings allowed), 1 = at least
one error (or any finding with --strict), 2 = usage / unreadable input. The output is deterministic: no run clock, sorted
keys, files in sorted order. Data files are opened read-only and never written.

Usage (from the repo root):
  python tools/research/gap_check.py [--root DIR ...] [--manifest FILE | --no-manifest] [--json OUT] [--md OUT] [--strict]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

FORMAT = 'zb-data-gaps/1'
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ROOTS = ('data', 'data1h', 'data_long')
CAIRO = ZoneInfo('Africa/Cairo')
MIN = 60_000
INTERVALS = {'1m': MIN, '3m': 3 * MIN, '5m': 5 * MIN, '15m': 15 * MIN, '30m': 30 * MIN, '1h': 60 * MIN,
             '2h': 120 * MIN, '4h': 240 * MIN, '6h': 360 * MIN, '8h': 480 * MIN, '12h': 720 * MIN, '1d': 1440 * MIN}
NAME_RE = re.compile(r'^([A-Z0-9]+)_(' + '|'.join(INTERVALS) + r')\.csv$')
ERRORS = ('G1', 'G2', 'G3', 'K1', 'K2', 'X1', 'X2', 'P1')
WARNINGS = ('G4', 'S1')
DISPOSITION = {
    'G1': 'no fill; signals whose lookback crosses it are not evaluated; positions across it are gap_exposed',
    'G2': 'rejected partition unless both values agree (identical duplicates: drop one)',
    'G3': 'rejected partition',
    'G4': 'kept, flagged; no entry on a flagged bar',
    'K1': 'rejected partition',
    'K2': 'rejected partition (re-sort only after the source is re-fetched)',
    'S1': 'the window end is cut to the last fresh row',
    'X1': 'report; the sources must be reconciled before either is used in a manifest',
    'X2': 'the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars',
    'P1': 'rejected (provenance unknown)',
}
COLS_LEGACY = ('t', 'o', 'h', 'l', 'c', 'v')
COLS_LONG = ('open_time', 'open', 'high', 'low', 'close', 'volume')
EXAMPLES = 5                      # examples kept per (file, class) for the bulk classes
LIST_MAX = 1000                   # missing opens listed one by one up to this many per run


# ------------------------------------------------------------------------------------------------------------ time
def utc_str(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def cairo_str(ms):
    """Africa/Cairo local time through the IANA zone (DST-aware: +02:00 winter, +03:00 summer)."""
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(CAIRO).strftime('%Y-%m-%d %H:%M:%S %z')


def stamp(ms):
    return {'ts_ms': ms, 'utc': utc_str(ms), 'cairo': cairo_str(ms)}


def parse_ts(s):
    s = s.strip()
    if s.isdigit():
        return int(s)
    return int(datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()) * 1000


# ------------------------------------------------------------------------------------------------------------ files
def discover(roots):
    """[(root label, path, symbol, interval)] for every candle csv under the roots, sorted."""
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                m = NAME_RE.match(fn)
                if m:
                    out.append((root, os.path.join(dirpath, fn), m.group(1), m.group(2)))
    return sorted(out, key=lambda x: (rel(x[1]), x[1]))


def rel(path):
    p = os.path.relpath(os.path.abspath(path), ROOT)
    return (os.path.abspath(path) if p.startswith('..') else p).replace('\\', '/')


def read_rows(path):
    """(sha256, [(line no, ts_ms or None, {o,h,l,c,v: str}, raw ts)], header problem or None). Read-only."""
    with open(path, 'rb') as f:
        blob = f.read()
    sha = hashlib.sha256(blob).hexdigest()
    text = blob.decode('utf-8')
    reader = csv.reader(text.splitlines())
    header = next(reader, None)
    if header is None:
        return sha, [], 'empty file'
    header = [h.strip() for h in header]
    if tuple(header[:6]) == COLS_LEGACY:
        idx = dict(zip('tohlcv', range(6)))
    elif tuple(header[:6]) == COLS_LONG:
        idx = dict(zip('tohlcv', range(6)))
    else:
        return sha, [], f'unknown header {header[:6]}'
    rows = []
    for n, rec in enumerate(reader, start=2):
        if not rec:
            continue
        vals = {k: (rec[i].strip() if i < len(rec) else '') for k, i in idx.items()}
        try:
            ts = parse_ts(vals['t'])
        except ValueError:
            ts = None
        rows.append((n, ts, vals))
    return sha, rows, None


# ------------------------------------------------------------------------------------------------------------ checks
def finding(cls, f, frm, to, count, detail, extra=None):
    rec = {'class': cls, 'severity': 'error' if cls in ERRORS else 'warning', 'dataset': f['dataset'],
           'file': f['file'], 'symbol': f['symbol'], 'interval': f['interval'],
           'from': stamp(frm) if frm is not None else None, 'to': stamp(to) if to is not None else None,
           'count': count, 'detail': detail, 'disposition': DISPOSITION[cls]}
    if extra:
        rec.update(extra)
    return rec


def num(s):
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def check_file(root, path, symbol, interval):
    step = INTERVALS[interval]
    f = {'dataset': 'klines_last', 'file': rel(path), 'root': rel(root), 'symbol': symbol, 'interval': interval}
    sha, rows, problem = read_rows(path)
    out = []
    if problem:
        out.append(finding('G3', f, None, None, 1, problem))
        return f | {'sha256': sha, 'rows': 0, 'first': None, 'last': None, 'values': {}}, out
    bad_ts = [r for r in rows if r[1] is None]
    for n, _, vals in bad_ts[:EXAMPLES]:
        out.append(finding('G3', f, None, None, 1, f'line {n}: unparseable open time {vals["t"]!r}'))
    if len(bad_ts) > EXAMPLES:
        out.append(finding('G3', f, None, None, len(bad_ts) - EXAMPLES, 'more unparseable open times'))
    rows = [r for r in rows if r[1] is not None]

    # K2 non-monotonic (file order), K1 misaligned
    k2 = [(rows[i - 1], rows[i]) for i in range(1, len(rows)) if rows[i][1] < rows[i - 1][1]]
    for a, b in k2[:EXAMPLES]:
        out.append(finding('K2', f, b[1], a[1], 1, f'line {b[0]} goes back from {utc_str(a[1])} to {utc_str(b[1])}'))
    if len(k2) > EXAMPLES:
        out.append(finding('K2', f, None, None, len(k2) - EXAMPLES, 'more backward steps'))
    k1 = [r for r in rows if r[1] % step]
    for n, ts, _ in k1[:EXAMPLES]:
        out.append(finding('K1', f, ts, ts, 1, f'line {n}: open not on the {interval} grid (offset {ts % step} ms)'))
    if len(k1) > EXAMPLES:
        out.append(finding('K1', f, None, None, len(k1) - EXAMPLES, 'more misaligned opens'))

    # G2 duplicates
    seen, values = {}, {}
    for n, ts, vals in rows:
        key = (vals['o'], vals['h'], vals['l'], vals['c'], vals['v'])
        if ts in seen:
            same = seen[ts][1] == key
            out.append(finding('G2', f, ts, ts, 1, f'lines {seen[ts][0]} and {n}: '
                               + ('identical duplicate' if same else 'CONFLICTING values')))
            continue
        seen[ts] = (n, key)
        values[ts] = key

    # G3 / G4 per bar
    g3, g4z, g4f = [], [], []
    for n, ts, vals in rows:
        o, h, lo, c, v = (num(vals[k]) for k in 'ohlcv')
        if None in (o, h, lo, c, v):
            g3.append((ts, f'line {n}: missing / non-numeric field'))
            continue
        why = []
        if min(o, h, lo, c) <= 0:
            why.append('price <= 0')
        if h < max(o, c):
            why.append('high < max(open, close)')
        if lo > min(o, c):
            why.append('low > min(open, close)')
        if h < lo:
            why.append('high < low')
        if v < 0:
            why.append('volume < 0')
        if why:
            g3.append((ts, f'line {n}: ' + ', '.join(why)))
            continue
        if v == 0:
            g4z.append(ts)
        elif h == lo:
            g4f.append(ts)
    for ts, why in g3[:EXAMPLES]:
        out.append(finding('G3', f, ts, ts, 1, why))
    if len(g3) > EXAMPLES:
        out.append(finding('G3', f, None, None, len(g3) - EXAMPLES, 'more invariant violations'))
    for label, lst in (('zero volume', g4z), ('flat bar (high == low)', g4f)):
        if lst:
            out.append(finding('G4', f, lst[0], lst[-1], len(lst), label,
                               {'examples': [stamp(t) for t in lst[:EXAMPLES]]}))

    # G1 missing intervals (between first and last key, on the grid)
    keys = sorted(values)
    grid = [t for t in keys if t % step == 0]          # a misaligned key is K1 (likely a mis-stamped bar), not a hole
    for a, b in zip(grid, grid[1:]):
        if b - a > step:
            first, last = a + step, b - step
            count = (last - first) // step + 1
            extra = {'missing': [stamp(t) for t in range(first, last + 1, step)]} if count <= LIST_MAX else {}
            out.append(finding('G1', f, first, last, count,
                               f'{count} missing {interval} bar(s) between {utc_str(a)} and {utc_str(b)}', extra))
    meta = f | {'sha256': sha, 'rows': len(rows), 'first': stamp(keys[0]) if keys else None,
                'last': stamp(keys[-1]) if keys else None, 'values': values}
    return meta, out


def check_stale(files):
    out = []
    newest = {}
    for m in files:
        if m['last']:
            newest[m['interval']] = max(newest.get(m['interval'], 0), m['last']['ts_ms'])
    for m in files:
        if not m['last']:
            continue
        top = newest[m['interval']]
        lag = (top - m['last']['ts_ms']) // INTERVALS[m['interval']]
        if lag > 0:
            out.append(finding('S1', m, m['last']['ts_ms'], top, lag,
                               f'ends {lag} {m["interval"]} bar(s) before the newest {m["interval"]} file '
                               f'({utc_str(top)})'))
    return out


def check_cross(files):
    """X1: the same symbol / interval in two roots must agree on every shared open time."""
    out = []
    groups = {}
    for m in files:
        groups.setdefault((m['symbol'], m['interval']), []).append(m)
    for (sym, iv), ms in sorted(groups.items()):
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                a, b = ms[i], ms[j]
                shared = sorted(set(a['values']) & set(b['values']))
                diff = [t for t in shared if not _same_bar(a['values'][t], b['values'][t])]
                if diff:
                    out.append(finding('X1', a, diff[0], diff[-1], len(diff),
                                       f'{len(diff)} of {len(shared)} shared bars differ from {b["file"]}',
                                       {'other_file': b['file'], 'shared': len(shared),
                                        'examples': [dict(stamp(t), a=list(a['values'][t]), b=list(b['values'][t]))
                                                     for t in diff[:EXAMPLES]]}))
    return out


def check_timeframes(files):
    """X2: a coarse bar must equal the aggregate of the finer bars of the same symbol that cover its whole span (open =
    first open, high = max, low = min, close = last close, volume = sum). It catches a candle stored while still open
    (a partial bar) and mis-built resamples. Coarse bars whose span the finer file does not fully cover are skipped."""
    out = []
    by_sym = {}
    for m in files:
        by_sym.setdefault(m['symbol'], []).append(m)
    for sym in sorted(by_sym):
        for coarse in by_sym[sym]:
            cstep = INTERVALS[coarse['interval']]
            for fine in by_sym[sym]:
                fstep = INTERVALS[fine['interval']]
                if fstep >= cstep or cstep % fstep:
                    continue
                k = cstep // fstep
                checked, diff = 0, []
                for t in sorted(coarse['values']):
                    parts = [fine['values'].get(t + i * fstep) for i in range(k)]
                    if None in parts:
                        continue
                    checked += 1
                    agg = _aggregate(parts)
                    if agg is None or not _same_bar(coarse['values'][t], agg):
                        diff.append((t, agg))
                if diff:
                    out.append(finding('X2', coarse, diff[0][0], diff[-1][0], len(diff),
                                       f'{len(diff)} of {checked} bars differ from the aggregate of {fine["file"]}',
                                       {'other_file': fine['file'], 'checked': checked,
                                        'examples': [dict(stamp(t), bar=list(coarse['values'][t]),
                                                          aggregate=list(a) if a else None)
                                                     for t, a in diff[:EXAMPLES]]}))
    return out


def _aggregate(parts):
    vals = [[num(x) for x in p] for p in parts]
    if any(None in v for v in vals):
        return None
    return (str(vals[0][0]), str(max(v[1] for v in vals)), str(min(v[2] for v in vals)), str(vals[-1][3]),
            str(sum((v[4] for v in vals), Decimal(0))))


def _same_bar(x, y):
    return all(num(p) == num(q) for p, q in zip(x, y))


def load_manifest(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f).get('files', {})


def check_manifest(files, manifest, manifest_path):
    out = []
    by_rel = {m['file']: m for m in files}
    mdir = os.path.dirname(os.path.abspath(manifest_path))
    for key in sorted(manifest):
        ent = manifest[key]
        frel = rel(os.path.join(mdir, key))
        m = by_rel.get(frel)
        if m is None:
            if NAME_RE.match(os.path.basename(key)):
                fake = {'dataset': 'klines_last', 'file': frel, 'symbol': None, 'interval': None}
                out.append(finding('P1', fake, None, None, 1, 'listed in the manifest, file not found'))
            continue
        why = []
        if ent.get('sha256') != m['sha256']:
            why.append('sha256 differs')
        if 'rows' in ent and ent['rows'] != m['rows']:
            why.append(f'rows {ent["rows"]} != {m["rows"]}')
        for k in ('first', 'last'):
            if k in ent and m[k] and ent[k] != m[k]['utc'][:-4]:
                why.append(f'{k} {ent[k]} != {m[k]["utc"][:-4]}')
        if why:
            out.append(finding('P1', m, None, None, 1, '; '.join(why)))
        m['manifest'] = 'listed'
    listed = {rel(os.path.join(mdir, k)) for k in manifest}
    for m in files:
        if m['file'] not in listed:
            out.append(finding('P1', m, None, None, 1, 'not listed in the manifest'))
    return out


# ------------------------------------------------------------------------------------------------------------ report
def run(roots, manifest_path=None):
    files, findings = [], []
    for root, path, sym, iv in discover(roots):
        meta, fs = check_file(root, path, sym, iv)
        files.append(meta)
        findings += fs
    findings += check_stale(files)
    findings += check_cross(files)
    findings += check_timeframes(files)
    if manifest_path:
        findings += check_manifest(files, load_manifest(manifest_path), manifest_path)
    order = {c: i for i, c in enumerate(ERRORS + WARNINGS)}
    findings.sort(key=lambda r: (r['file'], order[r['class']], (r['from'] or {}).get('ts_ms', -1), r['detail']))
    counts = {}
    for r in findings:
        counts[r['class']] = counts.get(r['class'], 0) + 1
    report = {
        'format': FORMAT,
        'roots': [rel(r) for r in roots if os.path.isdir(r)],
        'manifest': rel(manifest_path) if manifest_path else None,
        'not_checked': {'G5': 'venue maintenance windows (needs venue_sessions)',
                        'F1': 'funding cadence (no funding data in these roots)'},
        'files': [{k: m[k] for k in ('file', 'root', 'dataset', 'symbol', 'interval', 'rows', 'first', 'last',
                                     'sha256')} for m in files],
        'summary': {'files': len(files), 'rows': sum(m['rows'] for m in files),
                    'errors': sum(1 for r in findings if r['severity'] == 'error'),
                    'warnings': sum(1 for r in findings if r['severity'] == 'warning'),
                    'by_class': dict(sorted(counts.items())),
                    'missing_bars': sum(r['count'] for r in findings if r['class'] == 'G1')},
        'findings': findings,
    }
    return report


def to_json(report):
    return json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + '\n'


def to_markdown(report):
    s = report['summary']
    lines = ['# Local candle data: gap report (`' + report['format'] + '`)', '',
             f"Roots: {', '.join('`' + r + '`' for r in report['roots'])}. Manifest: "
             f"{'`' + report['manifest'] + '`' if report['manifest'] else 'not checked'}. "
             f"{s['files']} files, {s['rows']:,} rows. **{s['errors']} errors, {s['warnings']} warnings**, "
             f"{s['missing_bars']} missing bars.", '',
             'Times are UTC and Africa/Cairo (IANA zone, DST-aware). Not checked: '
             + '; '.join(f'{k} {v}' for k, v in report['not_checked'].items()) + '.', '',
             '| class | findings |', '|---|---|']
    lines += [f'| {c} | {n} |' for c, n in s['by_class'].items()] or ['| - | 0 |']
    lines += ['', '## Files', '', '| file | rows | first (UTC) | last (UTC) | findings |', '|---|---|---|---|---|']
    per = {}
    for r in report['findings']:
        per.setdefault(r['file'], []).append(r['class'])
    for m in report['files']:
        cls = per.get(m['file'], [])
        summ = ', '.join(f'{c} x{cls.count(c)}' for c in sorted(set(cls))) or 'clean'
        lines.append(f"| `{m['file']}` | {m['rows']:,} | {m['first']['utc'][:-4] if m['first'] else '-'} | "
                     f"{m['last']['utc'][:-4] if m['last'] else '-'} | {summ} |")
    lines += ['', '## Findings', '']
    if not report['findings']:
        lines.append('None.')
    for r in report['findings']:
        when = ''
        if r['from']:
            when = f" {r['from']['utc']} ({r['from']['cairo']} Cairo)"
            if r['to'] and r['to']['ts_ms'] != r['from']['ts_ms']:
                when += f" -> {r['to']['utc']} ({r['to']['cairo']} Cairo)"
        lines.append(f"- **{r['class']}** ({r['severity']}) `{r['file']}`:{when} - {r['detail']} (count {r['count']}). "
                     f"_{r['disposition']}_")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Read-only gap checker for local candle files (zb-data-gaps/1).')
    ap.add_argument('--root', action='append', help='data root (repeatable); default: data, data1h, data_long')
    ap.add_argument('--manifest', help='DATA_MANIFEST.json to cross-check (default: the repo one if present)')
    ap.add_argument('--no-manifest', action='store_true')
    ap.add_argument('--json', help='write the JSON report here')
    ap.add_argument('--md', help='write the Markdown report here')
    ap.add_argument('--strict', action='store_true', help='warnings fail too')
    a = ap.parse_args(argv)
    roots = [os.path.abspath(r) for r in (a.root or [os.path.join(ROOT, d) for d in DEFAULT_ROOTS])]
    manifest = None
    if not a.no_manifest:
        manifest = a.manifest or os.path.join(ROOT, 'DATA_MANIFEST.json')
        if not os.path.isfile(manifest):
            if a.manifest:
                print(f'manifest not found: {manifest}', file=sys.stderr)
                return 2
            manifest = None
    try:
        report = run(roots, manifest)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as ex:
        print(f'unreadable input: {ex}', file=sys.stderr)
        return 2
    if a.json:
        with open(a.json, 'w', encoding='utf-8', newline='\n') as f:
            f.write(to_json(report))
    if a.md:
        with open(a.md, 'w', encoding='utf-8', newline='\n') as f:
            f.write(to_markdown(report))
    s = report['summary']
    print(f"{s['files']} files, {s['rows']} rows: {s['errors']} errors, {s['warnings']} warnings, "
          f"{s['missing_bars']} missing bars; by class {s['by_class']}")
    if s['errors'] or (a.strict and s['warnings']):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
