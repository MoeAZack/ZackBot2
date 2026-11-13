"""Acquire and prepare audited, bounded EDGE-03 Stage-A inputs.

This tool never runs the strategy.  ``acquire`` performs public, unsigned
Binance USD-M GETs for the four missing funding histories and the nine terminal
4h bars.  ``prepare`` verifies every source and emits write-once runner inputs;
only the terminal bar's open is propagated.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


BASE_URL = 'https://fapi.binance.com'
START = '2024-10-14T00:00:00Z'
END = '2026-09-28T00:00:00Z'
START_MS = 1728864000000
END_MS = 1790553600000
TF_MS = 4 * 60 * 60 * 1000
SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT',
           'NEARUSDT', 'DOGEUSDT', 'UNIUSDT', 'SUIUSDT')
MISSING_FUNDING = ('ZECUSDT', 'NEARUSDT', 'UNIUSDT', 'SUIUSDT')
EXISTING_FUNDING = tuple(s for s in SYMBOLS if s not in MISSING_FUNDING)
ACQUISITION_SCHEMA = 'zackbot.edge03.source-acquisition/1'
CANDLE_SCHEMA = 'zackbot.edge03.candles/1'
FUNDING_SCHEMA = 'zackbot.edge03.funding-pit/1'
VENUE = 'BINANCE_USDM'


class InputError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as f:
            f.write(data)
    except FileExistsError:
        raise InputError(f'write-once target exists: {path}') from None


def write_json_new(path: Path, value, *, sort_keys: bool = True) -> None:
    write_new(path, (json.dumps(value, indent=2, sort_keys=sort_keys) + '\n').encode())


def get_json(path: str, params: dict[str, object], retries: int = 4) -> tuple[bytes, str]:
    query = urllib.parse.urlencode(params)
    url = f'{BASE_URL}{path}?{query}'
    request = urllib.request.Request(url, headers={'User-Agent': 'ZackBot-EDGE03-audit/1'})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                if response.status != 200:
                    raise InputError(f'HTTP {response.status} for {path}')
                json.loads(body)
                return body, utc_now()
        except Exception as e:
            if attempt + 1 == retries:
                raise InputError(f'GET failed for {path}: {e}') from None
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError('unreachable')


def acquire(out) -> Path:
    root = Path(out).resolve()
    if root.exists():
        raise InputError(f'no-overwrite acquisition root exists: {root}')
    root.mkdir(parents=True)
    manifest = {
        'schema': ACQUISITION_SCHEMA, 'venue': VENUE, 'source': BASE_URL,
        'started_at': utc_now(), 'window': {'start': START, 'end_inclusive': END},
        'public_unsigned': True, 'funding': {}, 'terminal_4h': {},
    }
    try:
        for symbol in MISSING_FUNDING:
            pages, cursor, seen = [], START_MS, set()
            while cursor <= END_MS:
                params = {'symbol': symbol, 'startTime': cursor, 'endTime': END_MS, 'limit': 1000}
                body, retrieved = get_json('/fapi/v1/fundingRate', params)
                rows = json.loads(body)
                if not isinstance(rows, list):
                    raise InputError(f'{symbol}: funding response is not a list')
                bounded = [r for r in rows if START_MS <= int(r.get('fundingTime', -1)) <= END_MS]
                if not bounded:
                    break
                for row in bounded:
                    if row.get('symbol') != symbol or set(('fundingTime', 'fundingRate', 'markPrice')) - set(row):
                        raise InputError(f'{symbol}: malformed funding row')
                    t = int(row['fundingTime'])
                    if t in seen:
                        raise InputError(f'{symbol}: duplicate funding timestamp {t}')
                    seen.add(t)
                page_path = root / 'raw' / 'funding' / symbol / f'page-{len(pages) + 1:03d}.json'
                write_new(page_path, body)
                pages.append({'path': page_path.relative_to(root).as_posix(), 'sha256': sha256(page_path),
                              'bytes': page_path.stat().st_size, 'retrieved_at': retrieved,
                              'request': {'path': '/fapi/v1/fundingRate', 'params': params},
                              'rows': len(bounded), 'first_ms': int(bounded[0]['fundingTime']),
                              'last_ms': int(bounded[-1]['fundingTime'])})
                last = int(bounded[-1]['fundingTime'])
                if last >= END_MS:
                    break
                if last < cursor:
                    raise InputError(f'{symbol}: funding cursor did not advance')
                cursor = last + 1
                time.sleep(0.15)
            manifest['funding'][symbol] = {'pages': pages, 'rows': len(seen),
                                           'first_ms': min(seen) if seen else None,
                                           'last_ms': max(seen) if seen else None}

        for symbol in SYMBOLS:
            params = {'symbol': symbol, 'interval': '4h', 'startTime': END_MS,
                      'endTime': END_MS + TF_MS - 1, 'limit': 1}
            body, retrieved = get_json('/fapi/v1/klines', params)
            rows = json.loads(body)
            if not isinstance(rows, list) or len(rows) != 1 or int(rows[0][0]) != END_MS:
                raise InputError(f'{symbol}: exact terminal 4h row unavailable')
            page_path = root / 'raw' / 'terminal_4h' / f'{symbol}.json'
            write_new(page_path, body)
            manifest['terminal_4h'][symbol] = {
                'path': page_path.relative_to(root).as_posix(), 'sha256': sha256(page_path),
                'bytes': page_path.stat().st_size, 'retrieved_at': retrieved,
                'request': {'path': '/fapi/v1/klines', 'params': params},
                'rows': 1, 'open_time_ms': int(rows[0][0]),
            }
            time.sleep(0.15)
        manifest['finished_at'] = utc_now()
        manifest_path = root / 'acquisition_manifest.json'
        write_json_new(manifest_path, manifest)
        return manifest_path
    except Exception:
        # Preserve partial immutable evidence for diagnosis; never reuse it.
        raise


def checked_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise InputError(f'cannot read JSON {path}: {e}') from None


def checked_artifact(root: Path, spec: dict) -> Path:
    path = (root / spec['path']).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise InputError('artifact path escapes acquisition root') from None
    if not path.is_file() or sha256(path) != spec.get('sha256'):
        raise InputError(f'acquisition artifact missing or hash mismatch: {path.name}')
    return path


def archive_rows(archive_root: Path, symbol: str) -> tuple[dict[int, str], list[dict]]:
    result, provenance = {}, []
    symbol_root = archive_root / symbol
    for path in sorted(symbol_root.glob(f'{symbol}-fundingRate-*.zip')):
        month = path.stem.rsplit('-', 2)[-2:]
        if len(month) != 2 or not ('2024-10' <= '-'.join(month) <= '2026-09'):
            continue
        provenance.append({'path': str(path), 'sha256': sha256(path), 'bytes': path.stat().st_size})
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if not n.endswith('/')]
            if len(names) != 1:
                raise InputError(f'{symbol}: archive must contain one CSV: {path.name}')
            with zf.open(names[0]) as raw:
                rows = csv.DictReader((line.decode('utf-8-sig') for line in raw))
                if rows.fieldnames != ['calc_time', 'funding_interval_hours', 'last_funding_rate']:
                    raise InputError(f'{symbol}: unexpected archive schema')
                for row in rows:
                    t = int(row['calc_time'])
                    if START_MS <= t <= END_MS:
                        if t in result:
                            raise InputError(f'{symbol}: duplicate archive timestamp {t}')
                        result[t] = row['last_funding_rate']
    return result, provenance


def api_csv_rows(path: Path, symbol: str) -> list[dict]:
    with path.open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    return [{'symbol': symbol, 'fundingTime': int(r['fundingTime']),
             'fundingRate': r['fundingRate'], 'markPrice': r['markPrice']}
            for r in rows if START_MS <= int(r['fundingTime']) <= END_MS]


def acquired_rows(acquisition_root: Path, spec: dict, symbol: str) -> list[dict]:
    rows = []
    for page in spec.get('pages', []):
        data = checked_json(checked_artifact(acquisition_root, page))
        rows.extend(r for r in data if START_MS <= int(r['fundingTime']) <= END_MS)
    return rows


def verify_funding(rows: list[dict], archive: dict[int, str], symbol: str) -> list[dict]:
    actual = {}
    for row in rows:
        if row.get('symbol', symbol) != symbol:
            raise InputError(f'{symbol}: wrong API symbol')
        t = int(row['fundingTime'])
        if t in actual:
            raise InputError(f'{symbol}: duplicate API timestamp {t}')
        try:
            rate, mark = Decimal(str(row['fundingRate'])), Decimal(str(row['markPrice']))
        except (InvalidOperation, TypeError):
            raise InputError(f'{symbol}: invalid funding numeric at {t}') from None
        if not rate.is_finite() or not mark.is_finite() or mark <= 0:
            raise InputError(f'{symbol}: invalid funding value at {t}')
        actual[t] = {'symbol': symbol, 'settlement_ms': t, 'available_ms': t,
                     'rate': str(row['fundingRate']), 'mark_price': str(row['markPrice'])}
    if set(actual) != set(archive):
        missing = sorted(set(archive) - set(actual))
        extra = sorted(set(actual) - set(archive))
        raise InputError(f'{symbol}: API/archive key mismatch missing={missing[:1]} extra={extra[:1]}')
    for t, row in actual.items():
        if Decimal(row['rate']) != Decimal(archive[t]):
            raise InputError(f'{symbol}: API/archive rate mismatch at {t}')
    return [actual[t] for t in sorted(actual)]


def prepare(acquisition_manifest, candle_source_manifest, candle_source_dir,
            existing_funding_dir, archive_root, out) -> tuple[Path, Path, Path]:
    out = Path(out).resolve()
    if out.exists():
        raise InputError(f'no-overwrite preparation root exists: {out}')
    acq_path = Path(acquisition_manifest).resolve()
    acq_root, acq = acq_path.parent, checked_json(acq_path)
    if acq.get('schema') != ACQUISITION_SCHEMA or acq.get('source') != BASE_URL:
        raise InputError('wrong acquisition manifest')
    if set(acq.get('funding', {})) != set(MISSING_FUNDING) or set(acq.get('terminal_4h', {})) != set(SYMBOLS):
        raise InputError('acquisition manifest has wrong frozen universe/order')

    source_manifest_path = Path(candle_source_manifest).resolve()
    source_manifest = checked_json(source_manifest_path)
    source_dir = Path(candle_source_dir).resolve()
    existing_dir = Path(existing_funding_dir).resolve()
    archives = Path(archive_root).resolve()
    verified_funding, funding_sources, calendar = {}, {}, None
    for symbol in SYMBOLS:
        archive, archive_provenance = archive_rows(archives, symbol)
        if symbol in EXISTING_FUNDING:
            api_path = existing_dir / f'{symbol}.csv'
            rows = api_csv_rows(api_path, symbol)
            api_source = {'kind': 'audited_api_csv', 'path': str(api_path),
                          'sha256': sha256(api_path), 'bytes': api_path.stat().st_size}
        else:
            rows = acquired_rows(acq_root, acq['funding'][symbol], symbol)
            api_source = {'kind': 'acquisition_pages', 'pages': acq['funding'][symbol]['pages']}
        # Verify the inclusive source window, then emit only settlements which
        # can be crossed by an episode inside the half-open Stage-A window.
        verified = [row for row in verify_funding(rows, archive, symbol)
                    if row['settlement_ms'] > START_MS]
        times = tuple(row['settlement_ms'] for row in verified)
        if calendar is None:
            calendar = times
        elif times != calendar:
            raise InputError(f'{symbol}: settlement calendar differs')
        verified_funding[symbol] = verified
        funding_sources[symbol] = {'api': api_source, 'archives': archive_provenance,
                                   'parity': 'exact timestamp and decimal rate'}
    if not calendar or calendar[0] <= START_MS or calendar[-1] != END_MS:
        raise InputError('funding calendar does not cover (start, end] through terminal settlement')

    # Validate everything before creating output.
    bounded_candles, candle_sources, terminal_sources = {}, {}, {}
    expected_times = tuple(range(START_MS, END_MS, TF_MS))
    for symbol in SYMBOLS:
        spec = source_manifest.get('files', {}).get(symbol, {})
        source = source_dir / str(spec.get('file', ''))
        if not source.is_file() or sha256(source) != spec.get('sha256'):
            raise InputError(f'{symbol}: candle source missing or hash mismatch')
        with source.open(newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or not set(('t', 'o', 'h', 'l', 'c')).issubset(reader.fieldnames):
                raise InputError(f'{symbol}: candle source schema')
            rows = [r for r in reader if START_MS <= int(float(r['t'])) < END_MS]
        times = tuple(int(float(r['t'])) for r in rows)
        if times != expected_times:
            raise InputError(f'{symbol}: candle source is not exact contiguous frozen window')
        terminal_spec = acq['terminal_4h'][symbol]
        terminal_path = checked_artifact(acq_root, terminal_spec)
        terminal = checked_json(terminal_path)
        if len(terminal) != 1 or int(terminal[0][0]) != END_MS:
            raise InputError(f'{symbol}: wrong terminal candle')
        try:
            terminal_open = Decimal(str(terminal[0][1]))
        except InvalidOperation:
            raise InputError(f'{symbol}: invalid terminal open') from None
        if not terminal_open.is_finite() or terminal_open <= 0:
            raise InputError(f'{symbol}: invalid terminal open')
        bounded_candles[symbol] = [
            {field: row[field] for field in ('t', 'o', 'h', 'l', 'c')} for row in rows
        ] + [{'t': str(END_MS), 'o': str(terminal_open), 'h': str(terminal_open),
              'l': str(terminal_open), 'c': str(terminal_open)}]
        candle_sources[symbol] = {'path': str(source), 'sha256': sha256(source)}
        terminal_sources[symbol] = {'path': terminal_spec['path'], 'sha256': terminal_spec['sha256'],
                                    'propagated_field': 'open only'}

    out.mkdir(parents=True)
    candle_files = {}
    for symbol in SYMBOLS:
        path = out / 'candles' / f'{symbol}_4h.csv'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['t', 'o', 'h', 'l', 'c'], lineterminator='\n')
            writer.writeheader(); writer.writerows(bounded_candles[symbol])
        candle_files[symbol] = {'path': path.relative_to(out).as_posix(), 'sha256': sha256(path)}

    calendar_path = out / 'funding_calendar.csv'
    with calendar_path.open('x', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, lineterminator='\n'); writer.writerow(['settlement_ms'])
        writer.writerows((t,) for t in calendar)
    records_path = out / 'funding_records.csv'
    with records_path.open('x', newline='', encoding='utf-8') as f:
        fields = ['symbol', 'settlement_ms', 'available_ms', 'rate', 'mark_price']
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n'); writer.writeheader()
        for symbol in SYMBOLS:
            writer.writerows(verified_funding[symbol])

    common = {'venue': VENUE, 'audited': True,
              'window': {'start': START, 'end_exclusive': END}, 'symbols': list(SYMBOLS)}
    candle_manifest = out / 'candles_manifest.json'
    write_json_new(candle_manifest, dict(common, schema=CANDLE_SCHEMA, timeframe='4h',
                                         terminal_open=True, files=candle_files), sort_keys=False)
    funding_manifest = out / 'funding_manifest.json'
    write_json_new(funding_manifest, dict(common, schema=FUNDING_SCHEMA, point_in_time=True,
                                          calendar={'path': calendar_path.name, 'sha256': sha256(calendar_path)},
                                          records={'path': records_path.name, 'sha256': sha256(records_path)}),
                   sort_keys=False)
    audit = {
        'schema': 'zackbot.edge03.prepared-inputs-audit/1', 'created_at': utc_now(),
        'acquisition_manifest': {'path': str(acq_path), 'sha256': sha256(acq_path)},
        'candle_source_manifest': {'path': str(source_manifest_path), 'sha256': sha256(source_manifest_path)},
        'candle_sources': candle_sources, 'terminal_sources': terminal_sources,
        'funding_sources': funding_sources,
        'coverage': {'candles': '9/9', 'funding': '9/9', 'funding_rows_per_symbol': len(calendar),
                     'terminal_opens': '9/9'},
        'outputs': {'candles_manifest': {'path': candle_manifest.name, 'sha256': sha256(candle_manifest)},
                    'funding_manifest': {'path': funding_manifest.name, 'sha256': sha256(funding_manifest)},
                    'funding_calendar': {'path': calendar_path.name, 'sha256': sha256(calendar_path)},
                    'funding_records': {'path': records_path.name, 'sha256': sha256(records_path)}},
        'holdout_read': False, 'settlement_boundary': '(entry_ms, exit_ms]',
    }
    audit_path = out / 'audit_manifest.json'
    write_json_new(audit_path, audit)
    return candle_manifest, funding_manifest, audit_path


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    a = sub.add_parser('acquire'); a.add_argument('--out', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--acquisition-manifest', required=True)
    p.add_argument('--candle-source-manifest', required=True)
    p.add_argument('--candle-source-dir', required=True)
    p.add_argument('--existing-funding-dir', required=True)
    p.add_argument('--archive-root', required=True)
    p.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    if args.command == 'acquire':
        print(acquire(args.out))
    else:
        print(prepare(args.acquisition_manifest, args.candle_source_manifest, args.candle_source_dir,
                      args.existing_funding_dir, args.archive_root, args.out)[2])


if __name__ == '__main__':
    main()
