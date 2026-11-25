"""Lock historical 4h premium or spot reference closes on the BTC price grid."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from decimal import Decimal
from functools import partial
import io
import json
from urllib.error import HTTPError
import zipfile

from longscout_funding_archive import atomic, CACHE, digest, HOUR_MS, months, PRICE, price_bounds, read_url

URL = 'https://data.binance.vision/data/futures/um/monthly/premiumIndexKlines/BTCUSDT/4h'
OUT = CACHE / 'longscout_codex_btc_premium_20261125'
STABLE_URL = 'https://data.binance.vision/data/spot/monthly/klines/USDCUSDT/4h'
STABLE_OUT = CACHE / 'longscout_codex_stablecoin_20261125'
SPOT_TURNOVER_URL = 'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/4h'
SPOT_TURNOVER_OUT = CACHE / 'longscout_codex_btc_spot_turnover_20261125'
STEP = 4 * HOUR_MS


def collect(month, base_url, symbol, value_column):
    name = f'{symbol}-4h-{month}'
    url = f'{base_url}/{name}.zip'
    try:
        raw = read_url(url)
    except HTTPError as exc:
        if symbol == 'USDCUSDT' and exc.code == 404:
            return month, None, {}
        raise
    expected, entry = read_url(url + '.CHECKSUM').decode('ascii').split()
    sha = digest(raw)
    if expected != sha or entry.lstrip('*') != f'{name}.zip':
        raise ValueError(f'{month}: checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != [f'{name}.csv']:
            raise ValueError(f'{month}: unexpected archive members')
        csv_rows = list(csv.reader(io.StringIO(archive.read(f'{name}.csv').decode('utf-8-sig'))))
    if csv_rows[0][0] == 'open_time':
        csv_rows = csv_rows[1:]
    rows = {}
    for row in csv_rows:
        start, end, value = int(row[0]), int(row[6]), Decimal(row[value_column])
        if start >= 10**15:
            if start % 1000 != 0 or end % 1000 != 999:
                raise ValueError(f'{month}: invalid microsecond timestamps')
            start, end = start // 1000, end // 1000
        if symbol == 'USDCUSDT' and start % STEP == 0 and start <= end < start + STEP - 1:
            continue
        if start % STEP != 0 or end != start + STEP - 1 or not value.is_finite():
            raise ValueError(f'{month}: invalid 4h reference row')
        if start in rows:
            raise ValueError(f'{month}: duplicate source row')
        rows[start] = str(value)
    return month, sha, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--stablecoin', action='store_true')
    inputs.add_argument('--spot-turnover', action='store_true')
    args = parser.parse_args()
    if args.stablecoin:
        source_url, symbol, field, target, column = STABLE_URL, 'USDCUSDT', 'spot_close', STABLE_OUT, 4
    elif args.spot_turnover:
        source_url, symbol, field, target, column = SPOT_TURNOVER_URL, 'BTCUSDT', 'spot_quote_volume', SPOT_TURNOVER_OUT, 7
    else:
        source_url, symbol, field, target, column = URL, 'BTCUSDT', 'premium_index_close', OUT, 4
    first, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    needed = list(range(first, end - STEP + 1, STEP))
    all_rows, source = {}, {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        request = partial(collect, base_url=source_url, symbol=symbol, value_column=column)
        for month, sha, rows in pool.map(request, months(first, end)):
            if rows.keys() & all_rows.keys():
                raise ValueError('months contain overlapping candles')
            all_rows.update(rows)
            source[month] = sha
    result = dict(status='COMPLETE_WITH_EXPLICIT_MASKS', source=source_url, symbol=symbol, timeframe='4h',
                  field=field, archive_sha256=source,
                  rows=[[stamp, all_rows.get(stamp)] for stamp in needed])
    missing = sum(row[1] is None for row in result['rows'])
    data = (json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    target.mkdir(exist_ok=True)
    atomic(target / 'manifest.json', data)
    print(f'{len(needed)-missing}/{len(needed)} observed, {missing} explicitly masked; manifest {digest(data)}')


if __name__ == '__main__':
    main()
