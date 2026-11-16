"""Public-only LUNC settlement-mark probe. Never substitutes candle OHLC for a settlement mark.

This is a source-gap diagnostic, not a completed funding re-costing engine.
Network calls are serial, at least four seconds apart; HTTP failures stop the run.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

BASE = 'https://fapi.binance.com/fapi/v1/'
QUERY = dict(symbol='1000LUNCUSDT', startTime=1673136000000,
             endTime=1673236800000, limit=1000)


def funding_rows(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('missing funding history')
    prev = -1
    for row in rows:
        if row['symbol'] != QUERY['symbol']:
            raise ValueError('wrong symbol')
        stamp = row['fundingTime']
        if not isinstance(stamp, int) or not QUERY['startTime'] <= stamp <= QUERY['endTime'] or stamp <= prev:
            raise ValueError('invalid funding timestamp/order')
        from decimal import Decimal
        if not Decimal(row['fundingRate']).is_finite():
            raise ValueError('non-finite funding rate')
        mark = row.get('markPrice')
        if mark not in ('', None) and (not Decimal(mark).is_finite() or Decimal(mark) <= 0):
            raise ValueError('invalid settlement mark')
        prev = stamp
    return rows


def classify(row, candles):
    """Availability of OHLC cannot upgrade an unknown event mark to verified."""
    minute = row['fundingTime'] // 60000 * 60000
    if len(candles) != 1 or candles[0][0] != minute or candles[0][6] != minute + 59999:
        raise ValueError('missing/wrong containing mark-price minute')
    from decimal import Decimal
    o, h, l, c = [Decimal(v) for v in candles[0][1:5]]
    if not all(x.is_finite() and x > 0 for x in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
        raise ValueError('invalid mark candle')
    has_mark = row.get('markPrice') not in ('', None)
    return dict(funding_time_ms=row['fundingTime'], rate=row['fundingRate'],
                settlement_mark=row.get('markPrice') or None,
                status='NATIVE_MARK_PRESENT' if has_mark else 'INCOMPLETE_SETTLEMENT_MARK',
                containing_minute_ohlc=dict(zip(('open', 'high', 'low', 'close'), candles[0][1:5])),
                handling='OHLC retained as source evidence only; no substitution and no funding cash computed')


class PublicCache:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.last = None

    def get(self, endpoint, params):
        if endpoint not in ('fundingRate', 'markPriceKlines'):
            raise ValueError('non-public endpoint')
        url = BASE + endpoint + '?' + urlencode(params)
        key = hashlib.sha256(url.encode()).hexdigest()
        raw_path = self.root / (key + '.json')
        meta_path = self.root / (key + '.meta.json')
        if meta_path.exists():
            raw = raw_path.read_bytes()
            meta = json.loads(meta_path.read_text())
            if meta['url'] != url or meta['sha256'] != hashlib.sha256(raw).hexdigest():
                raise ValueError('cache provenance/hash mismatch')
            return json.loads(raw), meta
        if self.last is not None:
            time.sleep(max(0, 4 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        with urlopen(url, timeout=40) as response:
            raw = response.read()
            now = datetime.now(timezone.utc)
            meta = dict(url=url, http_status=response.status,
                        fetched_cairo=now.astimezone(ZoneInfo('Africa/Cairo')).isoformat(),
                        fetched_utc=now.isoformat(), sha256=hashlib.sha256(raw).hexdigest(),
                        bytes=len(raw), weight_used=response.headers.get('X-MBX-USED-WEIGHT-1M'))
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError('unexpected public response schema')
        raw_path.write_bytes(raw)
        meta_path.write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
        return payload, meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='docs/research/lane2_funding_audit_20261116')
    args = parser.parse_args()
    root = Path(args.out)
    cache = PublicCache(root / 'public_cache')
    rows, meta = cache.get('fundingRate', QUERY)
    rows = funding_rows(rows)
    manifest = [meta]
    gaps = []
    for row in rows:
        minute = row['fundingTime'] // 60000 * 60000
        candles, cm = cache.get('markPriceKlines', dict(symbol=QUERY['symbol'], interval='1m',
                               startTime=minute, endTime=minute + 59999, limit=2))
        manifest.append(cm)
        gaps.append(classify(row, candles))
    result = dict(status='INCOMPLETE', scope='early LUNC window only; not full-strategy coverage',
                  requests=manifest, events=gaps, funding_cash=None, strategy_deltas=None)
    (root / 'lunc_probe.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status=result['status'], events=len(gaps),
                         missing_marks=sum(g['settlement_mark'] is None for g in gaps))))


if __name__ == '__main__':
    main()
