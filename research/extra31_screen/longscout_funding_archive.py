"""Pin public historical funding features for the frozen 166-symbol input pool.

This reads Binance Data Vision only. It never connects to an exchange account.
The archive's funding fields are candidate inputs. The simulator retains
its frozen cost model. No economic worker is started by this collector.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.request import urlopen
import zipfile
from zoneinfo import ZoneInfo

from longscout_register_version import RESTART_DEADLINE

ARCHIVE = 'https://data.binance.vision/data/futures/um/monthly/fundingRate'
CACHE = Path('C:/Dev/ZackBot_research_cache')
PRICE = CACHE / 'longscout_codex_v91_20261120' / '_data'
OUT = CACHE / 'longscout_codex_funding_20261125'
HOUR_MS = 3_600_000
COLUMNS = ('calc_time', 'funding_interval_hours', 'last_funding_rate')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def price_bounds(path):
    with path.open('rb') as stream:
        assert stream.readline().startswith(b't,')
        first = int(stream.readline().split(b',', 1)[0])
        stream.seek(-min(4096, path.stat().st_size), 2)
        last = int(stream.read().splitlines()[-1].split(b',', 1)[0])
    return first, last + HOUR_MS


def months(start_ms, end_ms):
    current = datetime.fromtimestamp(start_ms / 1000, timezone.utc).replace(day=1, hour=0, minute=0)
    end = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    while (current.year, current.month) <= (end.year, end.month):
        yield current.strftime('%Y-%m')
        current = current.replace(year=current.year + (current.month == 12), month=current.month % 12 + 1)


def read_url(url):
    if datetime.now(ZoneInfo('Africa/Cairo')) >= RESTART_DEADLINE:
        raise RuntimeError('Cairo cutoff reached')
    with urlopen(url, timeout=10) as response:
        return response.read()


def archive_rows(symbol, month):
    stem = f'{symbol}-fundingRate-{month}'
    url = f'{ARCHIVE}/{symbol}/{stem}.zip'
    data = read_url(url)
    expected, name = read_url(f'{url}.CHECKSUM').decode('ascii').split()
    actual = digest(data)
    if expected != actual or name.lstrip('*') != f'{stem}.zip':
        raise ValueError(f'{symbol} {month}: archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist() != [f'{stem}.csv']:
            raise ValueError(f'{symbol} {month}: unexpected archive members')
        text = archive.read(f'{stem}.csv').decode('utf-8-sig')
    table = csv.DictReader(io.StringIO(text))
    if tuple(table.fieldnames or ()) != COLUMNS:
        raise ValueError(f'{symbol} {month}: unknown columns')
    rows = []
    for row in table:
        stamp, interval, rate = int(row[COLUMNS[0]]), int(row[COLUMNS[1]]), Decimal(row[COLUMNS[2]])
        if not (1 <= interval <= 24 and rate.is_finite()):
            raise ValueError(f'{symbol} {month}: invalid funding row')
        rows.append((stamp, interval, rate))
    if not rows or any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
        raise ValueError(f'{symbol} {month}: funding rows absent or unordered')
    return actual, rows


def validate_coverage(symbol, records, begin, end, allow_gaps):
    if not records or records[0][0] - begin > records[0][1] * HOUR_MS + 60_000:
        raise ValueError(f'{symbol}: missing initial funding coverage')
    if end - records[-1][0] > records[-1][1] * HOUR_MS + 60_000:
        raise ValueError(f'{symbol}: missing terminal funding coverage')
    gaps = []
    for a, b in zip(records, records[1:]):
        if b[0] - a[0] > max(a[1], b[1]) * HOUR_MS + 60_000:
            if allow_gaps:
                gaps.append(dict(from_ms=a[0], to_ms=b[0], interval_h_before=a[1], interval_h_after=b[1]))
                continue
            zone = ZoneInfo('Africa/Cairo')
            first = datetime.fromtimestamp(a[0] / 1000, zone).isoformat(timespec='milliseconds')
            last = datetime.fromtimestamp(b[0] / 1000, zone).isoformat(timespec='milliseconds')
            raise ValueError(f'{symbol}: funding gap {first} to {last} Cairo (intervals {a[1]}h/{b[1]}h)')
    return gaps


def atomic(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


def collect(path, out, allow_gaps):
    symbol = path.name.removesuffix('_1h.csv')
    if not re.fullmatch('[A-Z0-9]+', symbol):
        raise ValueError('unsafe symbol filename')
    begin, end = price_bounds(path)
    report_path, target = out / f'{symbol}.json', out / f'{symbol}.csv'
    if report_path.is_file() and target.is_file():
        report = json.loads(report_path.read_bytes())
        if report['begin_ms'] == begin and report['end_ms'] == end and report['csv_sha256'] == digest(target.read_bytes()):
            return symbol, report
    by_stamp, checksums = {}, {}
    for month in months(begin, end):
        checksums[month], rows = archive_rows(symbol, month)
        for stamp, interval, rate in rows:
            if begin <= stamp <= end:
                old = by_stamp.setdefault(stamp, (interval, rate))
                if old != (interval, rate):
                    raise ValueError(f'{symbol} {month}: conflicting archive overlap')
    records = [(ts, *by_stamp[ts]) for ts in sorted(by_stamp)]
    gaps = validate_coverage(symbol, records, begin, end, allow_gaps)
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(COLUMNS)
    writer.writerows(records)
    data = output.getvalue().encode('ascii')
    report = dict(begin_ms=begin, end_ms=end, rows=len(records), csv_sha256=digest(data), archives=checksums, gaps=gaps)
    atomic(target, data)
    atomic(report_path, json.dumps(report, sort_keys=True).encode('utf-8'))
    return symbol, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', action='store_true', help='Check BTC/ICP/TLM coverage before the full acquisition.')
    parser.add_argument('--record-gaps', action='store_true',
                        help='Retain all symbols and record each internal gap for event-time entry masking.')
    args = parser.parse_args()
    files = sorted(PRICE.glob('*_1h.csv'))
    assert len(files) == 166
    out = OUT / 'masked' if args.record_gaps else OUT
    if args.pilot:
        out /= 'pilot'
        files = [path for path in files if path.name in ('BTCUSDT_1h.csv', 'ICPUSDT_1h.csv', 'TLMUSDT_1h.csv')]
    out.mkdir(exist_ok=True, parents=True)
    reports, failures = {}, {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(collect, path, out, args.record_gaps): path for path in files}
        for job in as_completed(jobs):
            path = jobs[job]
            try:
                symbol, report = job.result()
                reports[symbol] = report
                print(f'{symbol}: verified {report["rows"]} funding events, {len(report.get("gaps", []))} gaps', flush=True)
            except (ValueError, OSError, RuntimeError, ArithmeticError, zipfile.BadZipFile) as exc:
                failures[path.name] = str(exc)
                print(f'{path.name}: {exc}', flush=True)
    manifest = dict(status='INCOMPLETE' if failures else ('PILOT' if args.pilot else 'COMPLETE'),
                    source=ARCHIVE, expected_symbols=len(files), successes=len(reports), failures=failures,
                    gap_policy='MASK_OUTSIDE_CADENCE' if args.record_gaps else 'STRICT_CONTINUITY',
                    symbols=reports)
    data = (json.dumps(manifest, sort_keys=True, indent=1) + '\n').encode('utf-8')
    atomic(out / 'manifest.json', data)
    print(f'{manifest["status"]}: {len(reports)}/{len(files)} verified, manifest {digest(data)}')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
