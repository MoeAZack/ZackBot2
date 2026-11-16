"""Unauthenticated public funding history cache for lane2 fixed-book research.

Serial <=150 requests/5min. Stops on HTTP/rate/schema errors; resumable hash-checked
responses. No clients, settings, secrets, account data or trading endpoints.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

BASE = 'https://fapi.binance.com/fapi/v1/'


class Cache:
    def __init__(self, root, spacing=2.0):
        if spacing < 2:
            raise ValueError('spacing must be >=2 seconds')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.spacing = spacing
        self.last = None

    def get(self, endpoint, params):
        if endpoint not in ('fundingRate', 'markPriceKlines'):
            raise ValueError('only allowlisted public endpoints')
        url = BASE + endpoint + '?' + urlencode(params)
        key = hashlib.sha256(url.encode()).hexdigest()
        raw_path = self.root / (key + '.json')
        meta_path = self.root / (key + '.meta.json')
        if meta_path.exists():
            raw = raw_path.read_bytes()
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            if meta['url'] != url or meta['sha256'] != hashlib.sha256(raw).hexdigest():
                raise ValueError('cache hash/provenance mismatch')
            return json.loads(raw), meta
        if self.last is not None:
            time.sleep(max(0, self.spacing - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        with urlopen(url, timeout=45) as response:
            raw = response.read()
            now = datetime.now(timezone.utc)
            meta = dict(url=url, http_status=response.status,
                        fetched_cairo=now.astimezone(ZoneInfo('Africa/Cairo')).isoformat(),
                        fetched_utc=now.isoformat(), sha256=hashlib.sha256(raw).hexdigest(),
                        bytes=len(raw), weight_used=response.headers.get('X-MBX-USED-WEIGHT-1M'))
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError('public endpoint returned non-list')
        raw_path.write_bytes(raw)
        meta_path.write_bytes((json.dumps(meta, indent=2) + '\n').encode())
        return data, meta


def validate_page(rows, symbol, start, end):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('invalid page size/type')
    prev = start - 1
    for row in rows:
        stamp = row['fundingTime']
        if row['symbol'] != symbol or not isinstance(stamp, int) or not start <= stamp <= end or stamp <= prev:
            raise ValueError('invalid symbol/time/order in funding response')
        rate = Decimal(row['fundingRate'])
        if not rate.is_finite():
            raise ValueError('non-finite funding rate')
        mark = row.get('markPrice')
        if mark not in ('', None) and (not Decimal(mark).is_finite() or Decimal(mark) <= 0):
            raise ValueError('invalid native mark')
        prev = stamp
    return rows


def fetch_symbol(cache, symbol, start, end, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows, pages = [], []
    cursor = start
    while cursor <= end:
        data, meta = cache.get('fundingRate', dict(symbol=symbol, startTime=cursor, endTime=end, limit=1000))
        validate_page(data, symbol, cursor, end)
        pages.append(dict(start_ms=cursor, end_ms=end, count=len(data), response=meta))
        rows.extend(data)
        if len(data) < 1000:
            break
        cursor = data[-1]['fundingTime'] + 1
    result = dict(symbol=symbol, start_ms=start, end_ms=end, status='PAGINATED_REST_RANGE_COMPLETE',
                  note='REST pagination exhausted; event schedule consistency audited separately, not assumed fixed 8h',
                  events=rows, pages=pages)
    path = output / (symbol + '.json')
    path.write_bytes((json.dumps(result, separators=(',', ':')) + '\n').encode())
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--windows', default='research_cache/lane2/funding_windows.json')
    p.add_argument('--root', default='research_cache/lane2/native_funding')
    p.add_argument('--symbols', help='optional comma-separated subset')
    a = p.parse_args()
    root = Path(a.root)
    cache = Cache(root / 'raw')
    windows = json.loads(Path(a.windows).read_text(encoding='utf-8'))
    wanted = a.symbols.split(',') if a.symbols else sorted(windows)
    # Price and timing sensitivities need one candle on each edge; preserve actual
    # funding timestamps rather than rounding them into assumed 8h slots.
    margin = 8 * 3600000
    progress = []
    for symbol in wanted:
        spans = windows[symbol]
        start, end = min(x[0] for x in spans) - margin, max(x[1] for x in spans) + margin
        record = fetch_symbol(cache, symbol, start, end, root / 'symbols')
        missing = sum(r.get('markPrice') in ('', None) for r in record['events'])
        item = dict(symbol=symbol, pages=len(record['pages']), events=len(record['events']), missing_marks=missing)
        progress.append(item)
        print(json.dumps(item), flush=True)
        (root / 'progress.json').write_bytes((json.dumps(progress, indent=2) + '\n').encode())


if __name__ == '__main__':
    main()
