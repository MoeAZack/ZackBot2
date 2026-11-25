"""Lock market-wide BTC USD-M taker-flow fields from the public kline archive."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json

from longscout_btc_premium_archive import collect, STEP
from longscout_funding_archive import atomic, CACHE, digest, months, PRICE, price_bounds

URL = 'https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/4h'
OUT = CACHE / 'longscout_codex_btc_signed_flow_20261125'


def main():
    first, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    source, flows = {}, {}
    operation = partial(collect, base_url=URL, symbol='BTCUSDT', value_column=(7, 10))
    with ThreadPoolExecutor(max_workers=6) as pool:
        for month, sha, result in pool.map(operation, months(first,end)):
            if result.keys() & flows.keys():
                raise ValueError('overlapping BTC futures kline archives')
            source[month] = sha
            flows.update(result)
    rows = [[stamp, flows.get(stamp)] for stamp in range(first, end - STEP + 1, STEP)]
    manifest = dict(status='COMPLETE_WITH_EXPLICIT_MASKS', source=URL, timeframe='4h',
                    fields=['quote_turnover','taker_buy_quote_turnover'], archive_sha256=source, rows=rows)
    data = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    OUT.mkdir(exist_ok=True)
    atomic(OUT / 'manifest.json', data)
    print(f'{sum(row[1] is not None for row in rows)}/{len(rows)} observed. manifest {digest(data)}')


if __name__ == '__main__':
    main()
