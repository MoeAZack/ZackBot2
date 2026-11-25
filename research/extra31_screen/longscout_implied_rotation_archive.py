"""Combine exact BTC/ETH implied-volatility releases with ETH futures closes."""
from concurrent.futures import ThreadPoolExecutor
import json

from longscout_delivery_archive import fetch, DAY_MS
from longscout_funding_archive import atomic, CACHE, digest, months, PRICE, price_bounds

OUT = CACHE / 'longscout_codex_implied_rotation_20261125'
BTC = CACHE / 'longscout_codex_bvol_20261125' / 'manifest.json'
ETH = CACHE / 'longscout_codex_eth_bvol_20261125' / 'manifest.json'
BTC_SHA256 = 'e80a7ef7ff5dede66d48a5ae6ef865ea87fc3a5e6756363f6979be08a3581c50'


def main():
    btc_raw, eth_raw = BTC.read_bytes(), ETH.read_bytes()
    assert digest(btc_raw) == BTC_SHA256
    btc, eth = json.loads(btc_raw), json.loads(eth_raw)
    assert btc['symbol'] == 'BTCBVOLUSDT' and eth['symbol'] == 'ETHBVOLUSDT'
    assert btc['status'] == eth['status'] == 'COMPLETE_WITH_EXPLICIT_MASKS'
    btc_rows, eth_rows = btc['days'], eth['days']
    assert [row['date'] for row in btc_rows] == [row['date'] for row in eth_rows]
    first, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    eth_price, sources = {}, {}
    jobs = [('futures/um', 'ETHUSDT', m) for m in months(first,end)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (_, _, month), sha, candles in pool.map(fetch, jobs):
            if eth_price.keys() & candles.keys():
                raise ValueError('overlapping ETH price archives')
            eth_price.update(candles)
            sources[month] = sha
    expected = range(first, end - DAY_MS + 1, DAY_MS)
    assert len(expected) == len(eth_rows)
    rows = [[start, a.get('index_percent'), b.get('index_percent'), eth_price.get(start)]
            for start, a, b in zip(expected, btc_rows, eth_rows, strict=True)]
    data = dict(status='COMPLETE_WITH_EXPLICIT_MASKS', timeframe='1d',
                fields=['btc_bvol_percent','eth_bvol_percent','eth_perp_close'],
                btc_bvol_manifest_sha256=BTC_SHA256, eth_bvol_manifest_sha256=digest(eth_raw),
                eth_price_archive_sha256=sources, rows=rows)
    payload = (json.dumps(data, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    OUT.mkdir(exist_ok=True)
    atomic(OUT / 'manifest.json', payload)
    print(f'{sum(all(v is not None for v in row[1:]) for row in rows)}/{len(rows)} paired dates; manifest {digest(payload)}')


if __name__ == '__main__':
    main()
