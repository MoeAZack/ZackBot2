"""Verify cached public-source coverage and publish compact reproducible evidence."""
import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from lane2_mark_sources import needed_events

CAIRO = ZoneInfo('Africa/Cairo')


def local_time(stamp):
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).astimezone(CAIRO).isoformat()


def gz_csv(path, rows):
    rows = iter(rows)
    first = next(rows, None)
    if first is None:
        raise ValueError('empty evidence table: ' + str(path))
    with gzip.open(path, 'wt', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, list(first))
        writer.writeheader(); writer.writerow(first); writer.writerows(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', type=Path, default=Path('research_cache/lane2'))
    p.add_argument('--out', type=Path, default=Path('docs/research/lane2_funding_audit_20261116/completed'))
    p.add_argument('--details', nargs='*', default=[], help='completed trade_deltas.jsonl.gz evidence files')
    a = p.parse_args(); a.out.mkdir(parents=True, exist_ok=True)
    windows = json.loads((a.cache / 'funding_windows.json').read_text())
    sources = json.loads((a.cache / 'mark_archives/mark_sources.json').read_text())
    if sources['incomplete'] or sources['stopped_rate_limit']:
        raise ValueError('mark-source acquisition has not completed cleanly')
    proxy = {(r['symbol'], r['funding_time_ms']): r for r in sources['records']}
    if len(proxy) != len(sources['records']):
        raise ValueError('duplicate proxy source keys')
    required = {(symbol, stamp) for (symbol, _), times in
                needed_events(windows, a.cache / 'native_funding/symbols').items() for stamp in times}
    missing = sorted(required - proxy.keys())
    if missing:
        raise ValueError(f'{len(missing)} required marks lack source candles: {missing[:5]}')
    # Compact bitsets keep per-mark job provenance bounded across thousands of books.
    usage = defaultdict(lambda: dict(job_bits=0, strategy_bits=0, trade_instances=0, primary_instances=0))
    job_numbers, strategy_numbers = {}, {}
    for path in a.details:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                trade = json.loads(line)
                if trade['status'] != 'COMPLETE_MODELLED_CLOCK':
                    raise ValueError('incomplete trade in completed evidence')
                job = trade['job_id']
                strategy = job.split('|')[2] if job.split('|')[0] in ('path4h', 'worst4h', 'h1') else job.split(':')[0]
                job_number = job_numbers.setdefault(job, len(job_numbers))
                strategy_number = strategy_numbers.setdefault(strategy, len(strategy_numbers))
                for event in trade['proxy_events']:
                    key = event['symbol'], event['funding_time_ms']
                    if key not in proxy:
                        raise ValueError('applied proxy has no source provenance')
                    state = usage[key]
                    state['job_bits'] |= 1 << job_number
                    state['strategy_bits'] |= 1 << strategy_number
                    state['trade_instances'] += 1
                    state['primary_instances'] += event['primary_qty'] > 0
    def proxy_rows():
        for key in sorted(required):
            r = proxy[key]
            u = usage.get(key, dict(job_bits=0, strategy_bits=0, trade_instances=0, primary_instances=0))
            yield dict(symbol=r['symbol'], funding_time_ms=r['funding_time_ms'],
                funding_time_cairo=local_time(r['funding_time_ms']), mark_kind='APPROVED_LABELLED_1M_PROXY',
                proxy_open=r['value'], candle_high=r['high'], candle_low=r['low'], candle_close=r['close'],
                candle_open_ms=r['candle_open_ms'], candle_open_cairo=local_time(r['candle_open_ms']),
                candle_close_ms=r['candle_close_ms'], timestamp_gap_ms=r['timestamp_gap_ms'],
                containing_candle=r['containing_candle'], source_type=r['source_type'],
                source_url=r['source_url'], source_sha256=r['source_sha256'],
                checksum_url=r.get('checksum_url', ''), source_verified_or_fetched_cairo=r.get('verified_at_cairo', r.get('fetched_at_cairo')),
                applied_to_closed_trade_book=bool(u['job_bits']), job_count=u['job_bits'].bit_count(),
                trade_instances=u['trade_instances'], primary_trade_instances=u['primary_instances'],
                strategies='|'.join(sorted(s for s, i in strategy_numbers.items() if u['strategy_bits'] & (1 << i))))
    gz_csv(a.out / 'mark_proxy_ledger.csv.gz', proxy_rows())

    coverage, manifest, event_paths = [], [], []
    for symbol in sorted(windows):
        path = a.cache / 'native_funding/symbols' / (symbol + '.json')
        data = json.loads(path.read_text())
        if data['status'] != 'PAGINATED_REST_RANGE_COMPLETE':
            raise ValueError('incomplete public-rate pagination')
        offset, times = 0, []
        for page in data['pages']:
            meta = page['response']
            key = hashlib.sha256(meta['url'].encode()).hexdigest()
            raw = (a.cache / 'native_funding/raw' / (key + '.json')).read_bytes()
            if hashlib.sha256(raw).hexdigest() != meta['sha256']:
                raise ValueError('public funding response checksum mismatch')
            rows = json.loads(raw)
            if len(rows) != page['count'] or rows != data['events'][offset:offset + len(rows)]:
                raise ValueError('combined funding range differs from raw responses')
            offset += len(rows); manifest.append(meta)
        if offset != len(data['events']):
            raise ValueError('funding range has events outside raw response pages')
        times = [r['fundingTime'] for r in data['events']]
        if times != sorted(set(times)):
            raise ValueError('duplicate or unordered native event')
        gaps = [(x, y) for x, y in zip(times, times[1:]) if y-x > 8*3600000+120000]
        coverage.append(dict(symbol=symbol, query_start_ms=data['start_ms'], query_end_ms=data['end_ms'],
            query_start_cairo=local_time(data['start_ms']), query_end_cairo=local_time(data['end_ms']),
            events=len(times), pages=len(data['pages']), missing_native_marks=sum(not r.get('markPrice') for r in data['events']),
            required_proxy_marks=sum(s == symbol for s, _ in required), cadence_discontinuities=gaps,
            pagination_status=data['status'], combined_range_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        event_paths.append(path)
    def event_rows():
        for path in event_paths:
            data = json.loads(path.read_text())
            for event in data['events']:
                key = data['symbol'], event['fundingTime']; mark = event.get('markPrice')
                pr = proxy.get(key)
                label = 'NATIVE_SETTLEMENT_MARK' if mark not in ('', None) else ('APPROVED_LABELLED_1M_PROXY' if key in required else 'MISSING_MARK_OUTSIDE_REQUIRED_EXPOSURE_WINDOWS')
                yield dict(symbol=data['symbol'], funding_time_ms=event['fundingTime'],
                    funding_time_cairo=local_time(event['fundingTime']), funding_rate=event['fundingRate'],
                    native_settlement_mark=mark, mark_kind=label,
                    proxy_value=pr['value'] if key in required else '', rate_type=event.get('rateType', ''))
    gz_csv(a.out / 'public_funding_events.csv.gz', event_rows())
    (a.out / 'public_coverage.json').write_bytes((json.dumps(coverage, indent=2) + '\n').encode())
    with gzip.open(a.out / 'public_source_manifest.json.gz', 'wt', encoding='utf-8') as stream:
        json.dump(dict(funding_responses=manifest, mark_source_acquisition=sources), stream, separators=(',', ':'))
    shutil.copyfile(a.cache / 'funding_windows.json', a.out / 'funding_windows.json')
    overview = dict(symbols=len(coverage), events=sum(r['events'] for r in coverage),
        response_pages=sum(r['pages'] for r in coverage), required_proxy_marks=len(required),
        applied_proxy_marks=len(usage) if a.details else None,
        LUNC_required_proxy_marks=sum(s == '1000LUNCUSDT' for s, _ in required),
        LUNC_applied_proxy_marks=sum(s == '1000LUNCUSDT' for s, _ in usage) if a.details else None,
        proxy_max_abs_timestamp_gap_ms=max(abs(proxy[k]['timestamp_gap_ms']) for k in required),
        non_containing_proxy_candles=sum(not proxy[k]['containing_candle'] for k in required),
        source_gaps=sources['gaps'], cadence_discontinuities=sum(len(r['cadence_discontinuities']) for r in coverage),
        unresolved_required_marks=len(missing),
        status='FULL_SOURCE_COVERAGE_UNDER_DECLARED_MARK_PROXY_AND_CLOCK_CONVENTIONS')
    (a.out / 'coverage_summary.json').write_bytes((json.dumps(overview, indent=2) + '\n').encode())
    print(json.dumps(overview), flush=True)


if __name__ == '__main__': main()
