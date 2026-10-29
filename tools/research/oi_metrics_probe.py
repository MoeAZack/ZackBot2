"""OI-DATA-01: LIST-ONLY coverage probe of Binance UM daily `metrics` files (open-interest history) for STATIC-40
(Codex authorization, issue comment 6458048381). Network: public S3 bucket LISTING GETs only. No file is downloaded,
no series value is read, nothing is evaluated.

  python tools/research/oi_metrics_probe.py --universe <static-top40 json> --out <summary.json>

Per symbol it lists `data/futures/um/daily/metrics/<SYM>/`, parses the dates out of `<SYM>-metrics-YYYY-MM-DD.zip`
key names (`.CHECKSUM` keys are ignored), clips at CUTOFF (2026-09-28; keys on/after it are only counted, never
reported) and reports first / last date, file count, missing days inside [first, last] with the gap ranges, and the
longest run of consecutive complete calendar months that end before the cutoff. Gate (Codex): PASS when >= 15
symbols have >= 36 such consecutive complete months."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

CUTOFF = dt.date(2026, 9, 28)
LIST_URL = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
DAILY_PREFIX = 'data/futures/um/daily/metrics/'
MONTHLY_PREFIX = 'data/futures/um/monthly/metrics/'
GATE_MONTHS = 36
GATE_SYMBOLS = 15
_NS = '{http://s3.amazonaws.com/doc/2006-03-01/}'


def check_end(end: dt.date, cutoff: dt.date = CUTOFF) -> dt.date:
    """Refuse any reporting end on or after the cutoff."""
    if end >= cutoff:
        raise ValueError(f'end {end} is on/after the cutoff {cutoff}: refused')
    return end


def parse_key(key: str, symbol: str):
    """Date of a daily metrics .zip key for `symbol`, else None (CHECKSUM, other symbols, odd names)."""
    m = re.fullmatch(re.escape(f'{DAILY_PREFIX}{symbol}/{symbol}-metrics-') + r'(\d{4})-(\d{2})-(\d{2})\.zip', key)
    if not m:
        return None
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def parse_listing(xml_bytes: bytes):
    """(keys, common_prefixes, is_truncated, next_marker) of one S3 ListBucketResult page."""
    root = ET.fromstring(xml_bytes)
    keys = [c.findtext(f'{_NS}Key') for c in root.findall(f'{_NS}Contents')]
    prefixes = [c.findtext(f'{_NS}Prefix') for c in root.findall(f'{_NS}CommonPrefixes')]
    trunc = (root.findtext(f'{_NS}IsTruncated') or '').strip().lower() == 'true'
    nxt = root.findtext(f'{_NS}NextMarker') or (keys[-1] if keys else None)
    return keys, prefixes, trunc, nxt


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'zackbot-oi-probe/1 (list-only)'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def list_prefix(prefix: str, fetch=http_get, max_pages: int = 100):
    """All keys and common prefixes under `prefix` (delimiter '/'), following `marker` pagination."""
    keys, prefixes, marker = [], [], ''
    for _ in range(max_pages):
        q = {'prefix': prefix, 'delimiter': '/'}
        if marker:
            q['marker'] = marker
        k, p, trunc, nxt = parse_listing(fetch(LIST_URL + '?' + urllib.parse.urlencode(q)))
        keys += k
        prefixes += p
        if not trunc:
            return keys, prefixes
        if not nxt or nxt == marker:
            raise RuntimeError(f'pagination stalled at {marker!r}')
        marker = nxt
    raise RuntimeError(f'more than {max_pages} pages under {prefix}')


def _month_end(y: int, m: int) -> dt.date:
    return (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))


def month_runs(dates, cutoff: dt.date = CUTOFF):
    """Complete calendar months (every day present) whose last day is before `cutoff`; returns
    (longest_run, trailing_run) as dicts {months, start, end} (start/end 'YYYY-MM'), trailing = run ending at the last
    month that ends before the cutoff (0 when that month is incomplete)."""
    have = set(dates)
    by_month = {}
    for d in have:
        by_month.setdefault((d.year, d.month), 0)
        by_month[(d.year, d.month)] += 1
    complete = sorted(ym for ym, n in by_month.items()
                      if _month_end(*ym) < cutoff and n == _month_end(*ym).day)
    best = {'months': 0, 'start': None, 'end': None}
    cur_start, cur_len, prev = None, 0, None
    runs = []
    for ym in complete:
        if prev and (ym[0] * 12 + ym[1]) == (prev[0] * 12 + prev[1]) + 1:
            cur_len += 1
        else:
            cur_start, cur_len = ym, 1
        prev = ym
        runs.append((cur_start, ym, cur_len))
        if cur_len > best['months']:
            best = {'months': cur_len, 'start': '%04d-%02d' % cur_start, 'end': '%04d-%02d' % ym}
    last_eligible = (cutoff.year, cutoff.month)
    while _month_end(*last_eligible) >= cutoff:
        y, m = last_eligible
        last_eligible = (y - (m == 1), (m - 2) % 12 + 1)
    trailing = {'months': 0, 'start': None, 'end': None}
    if runs and runs[-1][1] == last_eligible:
        s, e, n = runs[-1]
        trailing = {'months': n, 'start': '%04d-%02d' % s, 'end': '%04d-%02d' % e}
    return best, trailing


def gaps(dates):
    """Missing-day ranges inside [min, max] of `dates` as [[first_missing, last_missing, n_days], ...]."""
    ds = sorted(set(dates))
    out = []
    for a, b in zip(ds, ds[1:]):
        if (b - a).days > 1:
            out.append([(a + dt.timedelta(days=1)).isoformat(), (b - dt.timedelta(days=1)).isoformat(),
                        (b - a).days - 1])
    return out


def summarize(symbol: str, keys, cutoff: dt.date = CUTOFF) -> dict:
    all_dates = [d for d in (parse_key(k, symbol) for k in keys) if d is not None]
    dates = sorted({d for d in all_dates if d < cutoff})
    n_after = len({d for d in all_dates if d >= cutoff})
    if not dates:
        return {'symbol': symbol, 'first': None, 'last': None, 'files': 0, 'missing_days': 0, 'gaps': [],
                'longest_complete_month_run': {'months': 0, 'start': None, 'end': None},
                'trailing_complete_month_run': {'months': 0, 'start': None, 'end': None},
                'files_on_or_after_cutoff_count_only': n_after}
    check_end(dates[-1], cutoff)
    g = gaps(dates)
    best, trailing = month_runs(dates, cutoff)
    return {'symbol': symbol, 'first': dates[0].isoformat(), 'last': dates[-1].isoformat(), 'files': len(dates),
            'missing_days': sum(x[2] for x in g), 'gaps': g,
            'longest_complete_month_run': best, 'trailing_complete_month_run': trailing,
            'files_on_or_after_cutoff_count_only': n_after}


def gate(rows, months: int = GATE_MONTHS, need: int = GATE_SYMBOLS) -> dict:
    ok = [r['symbol'] for r in rows if r['longest_complete_month_run']['months'] >= months]
    return {'rule': f'>= {need} symbols with >= {months} consecutive complete months ending before {CUTOFF}',
            'symbols_passing': len(ok), 'passing': ok, 'result': 'PASS' if len(ok) >= need else 'FAIL'}


def probe(symbols, fetch=http_get, end: dt.date = CUTOFF - dt.timedelta(days=1)) -> dict:
    check_end(end)
    cutoff = end + dt.timedelta(days=1)
    rows = []
    for s in symbols:
        keys, _ = list_prefix(f'{DAILY_PREFIX}{s}/', fetch)
        rows.append(summarize(s, keys, cutoff))
    _, monthly = list_prefix(MONTHLY_PREFIX.rsplit('metrics/', 1)[0], fetch)
    mkeys, mpref = list_prefix(MONTHLY_PREFIX, fetch)
    firsts = [r['first'] for r in rows if r['first']]
    lasts = [r['last'] for r in rows if r['last']]
    return {'format': 'zb-oi-metrics-probe/1', 'mode': 'list-only (no download, no values, no evaluation)',
            'listing_endpoint': LIST_URL, 'daily_prefix': DAILY_PREFIX, 'cutoff_exclusive': cutoff.isoformat(),
            'monthly_metrics_prefix_exists': MONTHLY_PREFIX in monthly or bool(mkeys or mpref),
            'symbols': rows, 'no_metrics': [r['symbol'] for r in rows if not r['files']],
            'earliest_first': min(firsts) if firsts else None, 'latest_last': max(lasts) if lasts else None,
            'gate': gate(rows)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--universe', required=True, help='static-top40 contract JSON (reads its `symbols` list)')
    ap.add_argument('--out', required=True, help='summary JSON path (not overwritten)')
    ap.add_argument('--end', default=(CUTOFF - dt.timedelta(days=1)).isoformat(), help='last reportable date')
    a = ap.parse_args(argv)
    end = check_end(dt.date.fromisoformat(a.end))
    if os.path.exists(a.out):
        raise SystemExit(f'{a.out} exists: refusing to overwrite')
    with open(a.universe, encoding='utf-8') as f:
        syms = [s['symbol'] if isinstance(s, dict) else s for s in json.load(f)['symbols']]
    res = probe(syms, end=end)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(res, f, indent=1, sort_keys=True)
    g = res['gate']
    print(f"{g['result']}: {g['symbols_passing']} symbols >= {GATE_MONTHS} months; "
          f"earliest {res['earliest_first']} latest {res['latest_last']}; no metrics {res['no_metrics']}; "
          f"monthly prefix {res['monthly_metrics_prefix_exists']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
