"""Lock daily BTC implied-volatility observations from public Binance BVOL."""
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
from statistics import median
from urllib.error import HTTPError
import zipfile

from longscout_funding_archive import atomic, CACHE, digest, PRICE, price_bounds, read_url

URL = 'https://data.binance.vision/data/option/daily/BVOLIndex/BTCBVOLUSDT'
OUT = CACHE / 'longscout_codex_bvol_20261125'
SYMBOL = 'BTCBVOLUSDT'


def read_bvol(raw, name, day):
    midnight = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=1)
    end = int(midnight.timestamp() * 1000)
    first = end - 300_000
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != [name + '.csv']:
            raise ValueError('unexpected options archive member')
        lines = archive.read(name + '.csv').decode('utf-8-sig').splitlines()
    if lines[0] != 'calc_time,symbol,base_asset,quote_asset,index_value':
        raise ValueError('unexpected options archive header')
    low, high = str(first), str(end)
    found = {}
    for line in lines[1:]:
        if not low <= line[:13] < high:
            continue
        stamp, symbol, _, _, raw_value = next(csv.reader([line]))
        stamp = int(stamp)
        value = Decimal(raw_value)
        if symbol != SYMBOL or not value.is_finite() or value <= 0 or not first <= stamp < end:
            raise ValueError('invalid BVOL observation')
        if found.setdefault(stamp, value) != value:
            raise ValueError('conflicting BVOL observation')
    return (str(median(found.values())) if found else None), len(found)


def collect(day):
    receipt = OUT / f'{day}.json'
    if receipt.is_file():
        return json.loads(receipt.read_bytes())
    name = f'{SYMBOL}-BVOLIndex-{day}'
    url = f'{URL}/{name}.zip'
    try:
        raw = read_url(url)
    except HTTPError as exc:
        if exc.code != 404:
            return dict(date=day, error=f'HTTP {exc.code}')
        result = dict(date=day, archive_missing=True)
    except (RuntimeError, OSError) as exc:
        return dict(date=day, error=str(exc))
    else:
        try:
            expected, member = read_url(url + '.CHECKSUM').decode('ascii').split()
            checksum = digest(raw)
            if expected != checksum or member.lstrip('*') != name + '.zip':
                raise ValueError('checksum mismatch')
            value, count = read_bvol(raw, name, day)
            result = dict(date=day, checksum=checksum, index_percent=value, samples=count)
        except (RuntimeError, OSError, ValueError, ArithmeticError, zipfile.BadZipFile) as exc:
            return dict(date=day, error=str(exc))
    atomic(receipt, json.dumps(result, sort_keys=True).encode('utf-8'))
    return result


def main():
    start, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    first = datetime.fromtimestamp(start / 1000, timezone.utc).date()
    until = datetime.fromtimestamp(end / 1000, timezone.utc).date()
    dates = [(first + timedelta(days=i)).isoformat() for i in range((until-first).days)]
    OUT.mkdir(exist_ok=True)
    days = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(collect, dates):
            days.append(result)
            if len(days) % 100 == 0:
                print(f'{len(days)}/{len(dates)} UTC dates checked', flush=True)
    errors = sum('error' in row for row in days)
    good = sum(row.get('index_percent') is not None for row in days)
    manifest = dict(status='INCOMPLETE' if errors else 'COMPLETE_WITH_EXPLICIT_MASKS',
                    source=URL, symbol=SYMBOL, field='index_percent',
                    sample='median timestamped BVOL from 23:55 to 24:00 UTC', days=days)
    raw = (json.dumps(manifest, indent=1, sort_keys=True) + '\n').encode('utf-8')
    atomic(OUT / 'manifest.json', raw)
    print(f'{good}/{len(dates)} verified, {errors} errors. Manifest {digest(raw)}')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
