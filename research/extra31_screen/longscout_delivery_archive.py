"""Collect daily BTC spot and native quarterly delivery candle closes."""
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
from urllib.error import HTTPError
import zipfile

from longscout_funding_archive import atomic, CACHE, digest, HOUR_MS, months, PRICE, price_bounds, read_url

ARCHIVE = 'https://data.binance.vision/data'
OUT = CACHE / 'longscout_codex_btc_delivery_20261125'
DAY_MS = 24 * HOUR_MS


def expiries(first, last):
    for year in range(first.year, last.year + 2):
        for month in (3, 6, 9, 12):
            day = datetime(year, month, monthrange(year, month)[1], 8, tzinfo=timezone.utc)
            day -= timedelta(days=(day.weekday() - 4) % 7)
            if first < day <= last + timedelta(days=120):
                yield day


def fetch(job):
    market, symbol, month = job
    stem = f'{symbol}-1d-{month}'
    url = f'{ARCHIVE}/{market}/monthly/klines/{symbol}/1d/{stem}.zip'
    try:
        raw = read_url(url)
    except HTTPError as exc:
        if exc.code == 404:
            return job, None, {}
        raise
    expected, name = read_url(url + '.CHECKSUM').decode('ascii').split()
    got = digest(raw)
    if expected != got or name.lstrip('*') != stem + '.zip':
        raise ValueError(f'{symbol} {month}: checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != [stem + '.csv']:
            raise ValueError(f'{symbol} {month}: unexpected archive members')
        rows = list(csv.reader(io.StringIO(archive.read(stem + '.csv').decode('utf-8-sig'))))
    if rows[0][0] == 'open_time':
        rows = rows[1:]
    found = {}
    for row in rows:
        start, close_time = int(row[0]), int(row[6])
        if start >= 10**15:
            if start % 1000 or close_time % 1000 != 999:
                raise ValueError('invalid microsecond timestamps')
            start, close_time = start // 1000, close_time // 1000
        if not start % DAY_MS == 0 or not start <= close_time <= start + DAY_MS - 1:
            raise ValueError('invalid daily market candle times')
        if close_time != start + DAY_MS - 1:
            continue
        close = Decimal(row[4])
        if not close.is_finite() or close <= 0 or start in found:
            raise ValueError('invalid daily close')
        found[start] = str(close)
    return job, got, found


def main():
    start, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    first = datetime.fromtimestamp(start / 1000, timezone.utc)
    last = datetime.fromtimestamp(end / 1000, timezone.utc)
    jobs = [('spot', 'BTCUSDT', m) for m in months(start, end)]
    delivery = {}
    for expiry in expiries(first, last):
        symbol = expiry.strftime('BTCUSDT_%y%m%d')
        delivery[symbol] = int(expiry.timestamp()) * 1000
        observed_from = int(max(first, expiry - timedelta(days=152)).timestamp()) * 1000
        observed_until = int(min(last, expiry - timedelta(days=28)).timestamp()) * 1000
        jobs.extend(('futures/um', symbol, m) for m in months(observed_from, observed_until))
    checksums, markets = {}, {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (_, symbol, month), checksum, rows in pool.map(fetch, jobs):
            checksums[f'{symbol}/{month}'] = checksum
            observations = markets.setdefault(symbol, {})
            if observations.keys() & rows.keys():
                raise ValueError(f'{symbol}: overlapping archive rows')
            observations.update(rows)
    trimmed = {symbol: [[t, c] for t, c in sorted(prices.items()) if start <= t and t + DAY_MS <= end]
               for symbol, prices in markets.items()}
    manifest = dict(status='COMPLETE_WITH_EXPLICIT_MASKS', source=ARCHIVE, timeframe='1d',
                    first_ms=start, last_end_ms=end, expiry_ms=delivery,
                    archive_sha256=checksums, market_closes=trimmed)
    raw = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    OUT.mkdir(exist_ok=True)
    atomic(OUT / 'manifest.json', raw)
    print(f'{sum(v is not None for v in checksums.values())}/{len(checksums)} archives verified; manifest {digest(raw)}')


if __name__ == '__main__':
    main()
