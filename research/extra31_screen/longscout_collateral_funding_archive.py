"""Lock matched BTC coin-margined and USDT-margined funding events."""
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
from urllib.error import HTTPError

from longscout_funding_archive import archive_rows, atomic, CACHE, digest, months, PRICE, price_bounds

USD_M = CACHE / 'longscout_codex_funding_20261125' / 'masked'
COIN_M_SOURCE = 'https://data.binance.vision/data/futures/cm/monthly/fundingRate'
OUT = CACHE / 'longscout_codex_btc_collateral_20261125'
USD_M_HASH = '324c4ebf7f05798f74e74ed9772310ea44c0216efe1db8b4d6786950a619e691'


def get_month(month):
    try:
        sha, rows = archive_rows('BTCUSD_PERP', month, COIN_M_SOURCE)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        sha, rows = None, []
    return month, sha, rows


def main():
    raw = (USD_M / 'manifest.json').read_bytes()
    assert digest(raw) == USD_M_HASH
    btc = json.loads(raw)['symbols']['BTCUSDT']
    payload = (USD_M / 'BTCUSDT.csv').read_bytes()
    assert digest(payload) == btc['csv_sha256']
    linear = [[int(row['calc_time']), int(row['funding_interval_hours']), row['last_funding_rate']]
              for row in csv.DictReader(io.StringIO(payload.decode('ascii')))]
    start, end = price_bounds(PRICE / 'BTCUSDT_1h.csv')
    inverse, checksums = {}, {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for month, sha, rows in pool.map(get_month, months(start, end)):
            checksums[month] = sha
            for stamp, hours, rate in rows:
                if start <= stamp <= end:
                    value = (hours, rate)
                    if inverse.setdefault(stamp, value) != value:
                        raise ValueError('conflicting coin-margined event')
    result = dict(status='COMPLETE_WITH_EXPLICIT_MASKS', field='settled_funding_by_collateral',
                  usd_m_manifest_sha256=USD_M_HASH, coin_m_source=COIN_M_SOURCE,
                  coin_m_month_sha256=checksums, linear=linear,
                  inverse=[[t, hours, str(rate)] for t, (hours, rate) in sorted(inverse.items())])
    data = (json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    OUT.mkdir(exist_ok=True)
    atomic(OUT / 'manifest.json', data)
    print(f'{len(linear)} USDT-margined and {len(inverse)} coin-margined records; manifest {digest(data)}')


if __name__ == '__main__':
    main()
