"""Collect checksum-verified BTC Metrics at 23:55 UTC on complete days."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import partial
import io
import json
from urllib.error import HTTPError
import zipfile

from longscout_funding_archive import atomic, CACHE, digest, PRICE, price_bounds, read_url

URL = 'https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT'
OUT = CACHE / 'longscout_codex_btc_oi_20261125'
POSITION_OUT = CACHE / 'longscout_codex_btc_position_20261125'
POSITION_FIELDS = ('count_toptrader_long_short_ratio', 'sum_toptrader_long_short_ratio')


def select_metrics(raw, name, day, fields):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != [f'{name}.csv']:
            raise ValueError('unexpected archive members')
        text = archive.read(f'{name}.csv').decode('utf-8-sig')
    target = f'{day} 23:55:00'
    values = {field: set() for field in fields}
    rows = csv.DictReader(io.StringIO(text))
    for row in rows:
        if row['create_time'] != target:
            continue
        if row['symbol'] != 'BTCUSDT':
            raise ValueError('unexpected instrument')
        for field, found in values.items():
            if row[field]:
                value = Decimal(row[field])
                if value.is_finite() and value > 0:
                    found.add(value)
    return {field: str(found.pop()) if len(found) == 1 else None for field, found in values.items()}


def collect(day, out, fields):
    receipt = out / f'{day}.json'
    if receipt.is_file():
        return json.loads(receipt.read_bytes())
    name = f'BTCUSDT-metrics-{day}'
    url = f'{URL}/{name}.zip'
    try:
        data = read_url(url)
    except HTTPError as exc:
        if exc.code != 404:
            return dict(date=day, error=f'HTTP {exc.code}')
        row = dict(date=day, archive_missing=True)
    except (OSError, RuntimeError) as exc:
        return dict(date=day, error=str(exc))
    else:
        try:
            expected, entry = read_url(url + '.CHECKSUM').decode('ascii').split()
            archive_hash = digest(data)
            if expected != archive_hash or entry.lstrip('*') != name + '.zip':
                raise ValueError('checksum mismatch')
            row = dict(date=day, checksum=archive_hash, **select_metrics(data, name, day, fields))
        except (OSError, RuntimeError, ValueError, KeyError, ArithmeticError, zipfile.BadZipFile) as exc:
            return dict(date=day, error=str(exc))
    atomic(receipt, json.dumps(row, sort_keys=True).encode('utf-8'))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--positioning', action='store_true')
    args = parser.parse_args()
    out, fields = (POSITION_OUT, POSITION_FIELDS) if args.positioning else (OUT, ('sum_open_interest',))
    start, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    first = datetime.fromtimestamp(start / 1000, timezone.utc).date()
    past_last = datetime.fromtimestamp(end / 1000, timezone.utc).date()
    dates = [(first + timedelta(days=delta)).isoformat() for delta in range((past_last - first).days)]
    out.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        days = []
        for row in pool.map(partial(collect, out=out, fields=fields), dates):
            days.append(row)
            if len(days) % 100 == 0:
                print(f'{len(days)}/{len(dates)} UTC dates checked', flush=True)
    errors = [row for row in days if 'error' in row]
    missing = sum(any(row.get(field) is None for field in fields) for row in days)
    manifest = dict(status='INCOMPLETE' if errors else 'COMPLETE_WITH_EXPLICIT_MASKS', source=URL,
                    sample='23:55 UTC exact observation', days=days)
    manifest['fields' if args.positioning else 'field'] = fields if args.positioning else fields[0]
    data = (json.dumps(manifest, indent=1, sort_keys=True) + '\n').encode('utf-8')
    atomic(out / 'manifest.json', data)
    print(f'{manifest["status"]}: {len(days)-missing}/{len(days)} observed; {len(errors)} errors; manifest {digest(data)}')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
