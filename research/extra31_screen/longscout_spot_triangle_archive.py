"""Lock same-minute BTC spot triangular quote residuals on the 4h grid."""
from concurrent.futures import ThreadPoolExecutor
import csv
from decimal import Decimal
import io
import json
import math
from statistics import median
from urllib.error import HTTPError
import zipfile

from longscout_funding_archive import atomic, CACHE, digest, HOUR_MS, months, PRICE, price_bounds, read_url

BASE = 'https://data.binance.vision/data/spot/monthly/klines'
OUT = CACHE / 'longscout_codex_spot_triangle_20261125'
SYMBOLS = ('BTCUSDT', 'BTCUSDC', 'USDCUSDT')
MINUTE = 60_000
STEP = 4 * HOUR_MS


def archive_closes(job):
    symbol, month = job
    stem = f'{symbol}-1m-{month}'
    url = f'{BASE}/{symbol}/1m/{stem}.zip'
    try:
        raw = read_url(url)
    except HTTPError as exc:
        if exc.code == 404:
            return job, None, {}
        raise
    expected, member = read_url(url + '.CHECKSUM').decode('ascii').split()
    checksum = digest(raw)
    if expected != checksum or member.lstrip('*') != stem + '.zip':
        raise ValueError('archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != [stem + '.csv']:
            raise ValueError('unexpected archive member')
        lines = archive.read(stem + '.csv').decode('utf-8-sig').splitlines()
    result = {}
    for line in lines:
        stamp = line.split(',', 1)[0]
        if stamp == 'open_time':
            continue
        multiplier = 1000 if len(stamp) >= 16 else 1
        start = int(stamp) // multiplier
        if start % STEP not in (STEP - 3*MINUTE, STEP - 2*MINUTE, STEP - MINUTE):
            continue
        row = next(csv.reader([line]))
        end = int(row[6])
        if int(stamp) % multiplier or end != int(stamp) + MINUTE*multiplier - 1:
            raise ValueError('noncanonical spot candle close')
        price = Decimal(row[4])
        if not price.is_finite() or price <= 0 or start in result:
            raise ValueError('invalid 1m spot close')
        if int(row[8]) > 0:
            result[start] = float(price)
    return job, checksum, result


def main():
    first, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    jobs = [(symbol, month) for symbol in SYMBOLS for month in months(first, end)]
    data = {symbol: {} for symbol in SYMBOLS}
    checksums = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for (symbol, month), sha, prices in pool.map(archive_closes, jobs):
            if data[symbol].keys() & prices.keys():
                raise ValueError('overlapping minute archive dates')
            data[symbol].update(prices)
            checksums[symbol + '/' + month] = sha
    rows = []
    for bar in range(first, end - STEP + 1, STEP):
        ts = [bar + STEP - i*MINUTE for i in (3, 2, 1)]
        if not all(t in data[s] for s in SYMBOLS for t in ts):
            rows.append([bar, None])
            continue
        residual = [math.log(data['BTCUSDT'][t]) - math.log(data['BTCUSDC'][t]) - math.log(data['USDCUSDT'][t]) for t in ts]
        rows.append([bar, median(residual)])
    manifest = dict(status='COMPLETE_WITH_EXPLICIT_MASKS', source=BASE, symbols=SYMBOLS, timeframe='4h',
                    field='median_log_triangle_residual_last_three_traded_minutes',
                    archive_sha256=checksums, rows=rows)
    raw = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    OUT.mkdir(exist_ok=True)
    atomic(OUT / 'manifest.json', raw)
    print(f'{sum(x is not None for _, x in rows)}/{len(rows)} triangular close windows, manifest {digest(raw)}')


if __name__ == '__main__':
    main()
