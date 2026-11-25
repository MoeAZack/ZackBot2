"""Collect frozen daily BTC near/outer depth shifts from public snapshots."""
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import itertools
import json
from statistics import median
from urllib.error import HTTPError
import zipfile

from longscout_funding_archive import atomic, CACHE, digest, PRICE, price_bounds, read_url

URL = 'https://data.binance.vision/data/futures/um/daily/bookDepth/BTCUSDT'
OUT = CACHE / 'longscout_codex_btc_depth_20261125'
BANDS = {-5, -1, 1, 5}


def closing_depth(raw, name, day):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != [f'{name}.csv']:
            raise ValueError('unexpected archive members')
        lines = archive.read(f'{name}.csv').decode('utf-8-sig').splitlines()
    before_close = f'{day} 23:55:00'
    same_day = f'{day} 23:'
    rows = csv.DictReader(itertools.chain(lines[:1], (s for s in lines[1:] if s.startswith(same_day))))
    if rows.fieldnames != ['timestamp', 'percentage', 'depth', 'notional']:
        raise ValueError('unknown book depth columns')
    snapshots = {}
    for row in rows:
        t = row['timestamp']
        side = Decimal(row['percentage'])
        if t < before_close or side not in BANDS:
            continue
        value = Decimal(row['notional'])
        if not value.is_finite() or value <= 0:
            continue
        sides = snapshots.setdefault(t, {})
        old = sides.setdefault(side, value)
        if old != value:
            raise ValueError('conflicting side snapshot')
    scores = []
    for sides in snapshots.values():
        if set(sides) != BANDS:
            continue
        bid, ask = sides[-1], sides[1]
        outer_bid, outer_ask = sides[-5] - bid, sides[5] - ask
        if outer_bid > 0 and outer_ask > 0:
            scores.append(bid / (bid + ask) - outer_bid / (outer_bid + outer_ask))
    return (str(median(scores)) if scores else None), len(scores)


def collect(day):
    receipt = OUT / f'{day}.json'
    if receipt.is_file():
        return json.loads(receipt.read_bytes())
    name = f'BTCUSDT-bookDepth-{day}'
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
            expected, entry = read_url(url + '.CHECKSUM').decode('ascii').split()
            checksum = digest(raw)
            if expected != checksum or entry.lstrip('*') != name + '.zip':
                raise ValueError('checksum mismatch')
            depth, n = closing_depth(raw, name, day)
            result = dict(date=day, checksum=checksum, near_minus_outer=depth, snapshots=n)
        except (RuntimeError, OSError, ValueError, KeyError, ArithmeticError, zipfile.BadZipFile) as exc:
            return dict(date=day, error=str(exc))
    atomic(receipt, json.dumps(result, sort_keys=True).encode('utf-8'))
    return result


def main():
    start, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    first = datetime.fromtimestamp(start / 1000, timezone.utc).date()
    last = datetime.fromtimestamp(end / 1000, timezone.utc).date()
    dates = [(first + timedelta(days=i)).isoformat() for i in range((last - first).days)]
    OUT.mkdir(exist_ok=True)
    days = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(collect, dates):
            days.append(result)
            if len(days) % 100 == 0:
                print(f'{len(days)}/{len(dates)} UTC days checked', flush=True)
    errors = sum('error' in row for row in days)
    available = sum(row.get('near_minus_outer') is not None for row in days)
    manifest = dict(status='INCOMPLETE' if errors else 'COMPLETE_WITH_EXPLICIT_MASKS',
                    source=URL, field='near_minus_outer_bid_share', sample='23:55 to 24:00 UTC',
                    days=days)
    data = (json.dumps(manifest, indent=1, sort_keys=True) + '\n').encode('utf-8')
    atomic(OUT / 'manifest.json', data)
    print(f'{available}/{len(days)} available, {errors} errors; manifest {digest(data)}')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
