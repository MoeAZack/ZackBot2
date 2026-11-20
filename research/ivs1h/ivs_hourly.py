"""P5 IVS-1H stage A: audit official BTCBVOLUSDT daily BVOLIndex files and build the deterministic hour-level table
(ruling 82f70688). Data preparation only: no x/z, no signals, no prices, no returns.
Per hour t (UTC open): value = last positive finite index_value with calc_time in [t, t+1h) (source string kept byte-exact);
valid iff >= 3000 unique valid observations, last calc_time >= t+1h-60s (and < t+1h by construction), and NO invalid row,
duplicate timestamp, out-of-order timestamp or wrong-day row touching that hour. Missing days stay gaps (no fill).
Usage: python -I ivs_hourly.py <expected_manifest.json> <fetch_manifest.json> <raw cache dir> <out dir>"""
import csv, hashlib, io, json, math, os, sys, zipfile
import datetime as D

HDR = ['calc_time', 'symbol', 'base_asset', 'quote_asset', 'index_value']
SYM, BASE, QUOTE = 'BTCBVOLUSDT', 'BTCBVOL', 'USDT'   # base_asset observed as BTCBVOL (ruling text said BTC): disclosed, constant check
MIN_OBS, FRESH_US = 3000, 60_000_000
HOUR_US = 3_600_000_000


def to_us(s):
    """Native calc_time -> integer microseconds. 13 digits = ms, 16 digits = us; anything else is invalid (None)."""
    if not s.isdigit(): return None, None
    if len(s) == 13: return int(s) * 1000, 'ms'
    if len(s) == 16: return int(s), 'us'
    return None, None


def pos_finite(s):
    try: v = float(s)
    except ValueError: return False
    return math.isfinite(v) and v > 0


def hour_stats(rows, h0):
    """rows: list of (t_us, value_str, row_ok) in FILE order for one hour [h0, h0+1h). Returns the hour record (no file fields)."""
    reasons = []
    if any(not ok for _, _, ok in rows): reasons.append('invalid_row')
    ts = [t for t, _, _ in rows]
    if len(set(ts)) != len(ts): reasons.append('duplicate_time')
    if any(b < a for a, b in zip(ts, ts[1:])): reasons.append('unordered_time')
    good = [(t, v) for t, v, ok in rows if ok]
    n = len({t for t, _ in good})
    last_t, last_v = (good[-1] if good else (None, ''))
    if n < MIN_OBS: reasons.append('count_lt_3000')
    if last_t is None or last_t < h0 + HOUR_US - FRESH_US: reasons.append('stale_last')
    vals = [v for _, v in good]
    run = best = 0; prev = None
    for v in vals:
        run = run + 1 if v == prev else 1; best = max(best, run); prev = v
    return dict(value=last_v if not reasons else '', last_calc_time_us='' if last_t is None else last_t, unique_valid_count=n,
                rows=len(rows), n_distinct_values=len(set(vals)), max_const_run=best, valid=int(not reasons), reason='|'.join(reasons) or 'ok')


def audit_day(raw, date):
    """Parse one day file (bytes). Returns per-hour row lists keyed by hour-start-us and file-level audit dict."""
    zz = zipfile.ZipFile(io.BytesIO(raw)); names = zz.namelist()
    exp = f'{SYM}-BVOLIndex-{date}.csv'
    if names != [exp]: raise SystemExit(f'SOURCE BLOCKER member list {date} {names}')
    text = zz.read(exp).decode('utf-8')
    r = csv.reader(io.StringIO(text)); hdr = next(r)
    if hdr != HDR: raise SystemExit(f'SOURCE BLOCKER header {date} {hdr}')
    d0 = int(D.datetime.fromisoformat(date).replace(tzinfo=D.timezone.utc).timestamp()) * 1_000_000
    hours = {}; units = set(); bad = dict(time=0, symbol=0, value=0, width=0, outside_day=0); n = 0; first = last = None
    for row in r:
        n += 1
        if len(row) != 5: bad['width'] += 1; raise SystemExit(f'SOURCE BLOCKER row width {date} line {n + 1}')
        t, u = to_us(row[0])
        if t is None: raise SystemExit(f'SOURCE BLOCKER calc_time {date} {row[0]!r}')
        units.add(u); first = t if first is None else first; last = t
        ok = True
        if (row[1], row[2], row[3]) != (SYM, BASE, QUOTE): bad['symbol'] += 1; ok = False
        if not pos_finite(row[4]): bad['value'] += 1; ok = False
        if not (d0 <= t < d0 + 24 * HOUR_US): bad['outside_day'] += 1
        hours.setdefault(t - t % HOUR_US, []).append((t, row[4], ok))
    return hours, dict(rows=n, units=sorted(units), first_us=first, last_us=last, bad=bad,
                       hours_present=len(hours), hours_outside_day=sum(1 for h in hours if not (d0 <= h < d0 + 24 * HOUR_US)))


def main(exp_path, fetch_path, raw_dir, out):
    E = json.load(open(exp_path, encoding='utf-8')); F = json.load(open(fetch_path, encoding='utf-8'))
    assert sorted(F['files']) == sorted(E['files']) and F['missing_dates'] == E['missing_dates']
    c0 = D.date.fromisoformat(E['calendar_first']); c1 = D.date.fromisoformat(E['calendar_last'])
    cal = [(c0 + D.timedelta(k)).isoformat() for k in range((c1 - c0).days + 1)]
    assert len(cal) == E['calendar_dates'] and sorted(set(cal) - set(E['files'])) == E['missing_dates']
    os.makedirs(out, exist_ok=True); table = []; files = {}; spill = {}
    for date in cal:
        d0 = int(D.datetime.fromisoformat(date).replace(tzinfo=D.timezone.utc).timestamp()) * 1_000_000
        if date not in E['files']:
            for k in range(24): table.append(dict(hour_open_us=d0 + k * HOUR_US, value='', last_calc_time_us='', unique_valid_count=0, rows=0,
                                                  n_distinct_values=0, max_const_run=0, valid=0, reason='missing_day', source_file='', source_sha256=''))
            continue
        f = F['files'][date]; raw = open(os.path.join(raw_dir, f['file']), 'rb').read()
        h = hashlib.sha256(raw).hexdigest()
        if h != f['sha256'] or len(raw) != E['files'][date]['zip_listed_bytes']: raise SystemExit(f'SOURCE BLOCKER hash/size {date}')
        hours, a = audit_day(raw, date); files[date] = dict(a, file=f['file'], sha256=h)
        for hs, rows in hours.items():
            if not (d0 <= hs < d0 + 24 * HOUR_US): spill.setdefault(hs, []).append(date)
        for k in range(24):
            hs = d0 + k * HOUR_US
            rec = hour_stats(hours.get(hs, []), hs)
            table.append(dict(hour_open_us=hs, **rec, source_file=f['file'], source_sha256=h))
    for rec in table:   # a row from another day's file landing in this hour makes it ambiguous -> invalid, never merged
        if rec['hour_open_us'] in spill:
            rec['valid'] = 0; rec['value'] = ''; rec['reason'] = ('' if rec['reason'] == 'ok' else rec['reason'] + '|') + 'cross_file_row'
    cols = ['hour_open_utc', 'hour_open_us', 'value', 'last_calc_time_us', 'unique_valid_count', 'rows', 'n_distinct_values', 'max_const_run',
            'valid', 'reason', 'source_file', 'source_sha256']
    with open(os.path.join(out, 'btcbvol_hourly.csv'), 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, cols, lineterminator='\n'); w.writeheader()
        for rec in table:
            w.writerow(dict(rec, hour_open_utc=D.datetime.fromtimestamp(rec['hour_open_us'] / 1e6, D.timezone.utc).strftime('%Y-%m-%d %H:%M')))
    from collections import Counter
    rc = Counter(x for rec in table for x in rec['reason'].split('|'))
    aud = dict(ruling='82f70688', symbol=SYM, base_asset_observed=BASE, calendar_dates=len(cal), available_files=len(files),
               missing_dates=E['missing_dates'], hours_total=len(table), hours_valid=sum(r['valid'] for r in table), reason_counts=dict(sorted(rc.items())),
               time_units=dict(Counter(u for a in files.values() for u in a['units'])), rows_total=sum(a['rows'] for a in files.values()),
               bad_rows_total={k: sum(a['bad'][k] for a in files.values()) for k in ('symbol', 'value', 'width', 'outside_day')},
               cross_file_hours=sorted(spill), files=files,
               note='Data preparation only. No x/z/signal/price/return computed. calc_time is index calculation time, not publication/receipt time.')
    json.dump(aud, open(os.path.join(out, 'audit.json'), 'w', encoding='utf-8'), indent=1, sort_keys=True)
    print(json.dumps({k: v for k, v in aud.items() if k not in ('files',)}, default=str)[:3000])


if __name__ == '__main__':
    main(*sys.argv[1:5])
