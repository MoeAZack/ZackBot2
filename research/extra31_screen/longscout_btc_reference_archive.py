"""Lock contemporaneous BTC mark-price and index-price high/low/close."""
from concurrent.futures import ThreadPoolExecutor
import json

from longscout_btc_premium_archive import collect, STEP
from longscout_funding_archive import atomic, CACHE, digest, months, PRICE, price_bounds

BASE = 'https://data.binance.vision/data/futures/um/monthly'
OUT = CACHE / 'longscout_codex_btc_reference_20261125'
SERIES = {'mark': 'markPriceKlines', 'index': 'indexPriceKlines'}


def request(job):
    label, month = job
    url = f'{BASE}/{SERIES[label]}/BTCUSDT/4h'
    _, checksum, values = collect(month, url, 'BTCUSDT', (2, 3, 4))
    return label, month, checksum, values


def main():
    first, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    dates = range(first, end - STEP + 1, STEP)
    jobs = [(label, month) for label in SERIES for month in months(first, end)]
    observations = {label: {} for label in SERIES}
    checksums = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for label, month, checksum, values in pool.map(request, jobs):
            if observations[label].keys() & values.keys():
                raise ValueError('overlapping reference candles')
            observations[label].update(values)
            checksums[f'{label}/{month}'] = checksum
    rows = [[stamp, observations['mark'].get(stamp), observations['index'].get(stamp)] for stamp in dates]
    output = dict(status='COMPLETE_WITH_EXPLICIT_MASKS', source=BASE, symbol='BTCUSDT', timeframe='4h',
                  fields=['high','low','close'], data=['mark','index'], archive_sha256=checksums, rows=rows)
    raw = (json.dumps(output, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    OUT.mkdir(exist_ok=True)
    atomic(OUT / 'manifest.json', raw)
    good = sum(mark is not None and index is not None for _, mark, index in rows)
    print(f'{good}/{len(rows)} paired reference bars, manifest {digest(raw)}')


if __name__ == '__main__':
    main()
