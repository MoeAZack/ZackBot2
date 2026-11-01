"""Default-OFF causal row loader for Binance UM daily ``metrics`` archives.

The loader is research-only.  It performs the signed OI-LOADER-01 manifest and
disk gate before opening any zip, then normalizes Binance's two observed label
conventions to one causal ``period_end``.  It never downloads data, fills a
missing row, rounds an off-grid timestamp, or imports into PAPER/runtime code.

The convention boundary is frozen from binance/binance-public-data issue #491:

* archive days through 2026-06-24: labels are period ends (00:05..next 00:00)
* archive days from 2026-06-25: labels are period starts (00:00..23:55)

Both normalize to period ends 00:05..next 00:00.  A further five-minute
availability lag is deliberately conservative and is exposed on every row.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

import oi_manifest_admission as ADMIT


FORMAT = 'zb-oi-metrics-causal/1'
PERIOD = dt.timedelta(minutes=5)
AVAILABILITY_LAG = dt.timedelta(minutes=5)
CONVENTION_START_DAY = dt.date(2026, 6, 25)
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250
TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
NUMBER = re.compile(r'(?:0|[1-9]\d*)(?:\.\d+)?')
EXPECTED_COLUMNS = (
    'create_time',
    'symbol',
    'sum_open_interest',
    'sum_open_interest_value',
    'count_toptrader_long_short_ratio',
    'sum_toptrader_long_short_ratio',
    'count_long_short_ratio',
    'sum_taker_long_short_vol_ratio',
)
NUMERIC_COLUMNS = EXPECTED_COLUMNS[2:]


class LoaderError(ValueError):
    """A fail-closed archive or row refusal."""


def _utc_text(value: dt.datetime) -> str:
    return value.replace(tzinfo=dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def _parse_timestamp(value: str) -> dt.datetime:
    if type(value) is not str or not TIMESTAMP.fullmatch(value):
        raise LoaderError('ROW:timestamp_format')
    try:
        parsed = dt.datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except ValueError as exc:
        raise LoaderError('ROW:timestamp_value') from exc
    if parsed.second or parsed.microsecond or parsed.minute % 5:
        raise LoaderError('ROW:timestamp_grid')
    return parsed


def _decimal(value: str, column: str) -> str:
    if type(value) is not str or not NUMBER.fullmatch(value):
        raise LoaderError(f'ROW:number:{column}')
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise LoaderError(f'ROW:number:{column}') from exc
    if not number.is_finite() or number < 0:
        raise LoaderError(f'ROW:number:{column}')
    return value


def _expected_times(archive_day: dt.date):
    start = dt.datetime.combine(archive_day, dt.time()) + PERIOD
    return [start + i * PERIOD for i in range(288)]


def _raw_to_period_end(raw: dt.datetime, archive_day: dt.date) -> tuple[str, dt.datetime]:
    if archive_day < CONVENTION_START_DAY:
        return 'end_labeled_v1', raw
    return 'start_labeled_v2', raw + PERIOD


def _member(zf: zipfile.ZipFile, symbol: str, archive_day: dt.date):
    infos = [info for info in zf.infolist() if not info.is_dir()]
    expected = f'{symbol}-metrics-{archive_day.isoformat()}.csv'
    if len(infos) != 1 or infos[0].filename != expected:
        raise LoaderError('ZIP:member')
    info = infos[0]
    if info.file_size <= 0 or info.file_size > MAX_MEMBER_BYTES:
        raise LoaderError('ZIP:size')
    if info.compress_size <= 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
        raise LoaderError('ZIP:ratio')
    if info.flag_bits & 0x1:
        raise LoaderError('ZIP:encrypted')
    return info


def iter_archive(path, symbol: str, archive_day: dt.date):
    """Validate and yield normalized rows from one already checksum-verified zip."""
    expected_period_ends = _expected_times(archive_day)
    try:
        with zipfile.ZipFile(path) as zf:
            info = _member(zf, symbol, archive_day)
            with zf.open(info, 'r') as raw, io.TextIOWrapper(raw, encoding='utf-8', newline='') as text:
                reader = csv.DictReader(text)
                if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
                    raise LoaderError('CSV:header')
                seen = set()
                count = 0
                for count, row in enumerate(reader, 1):
                    if None in row or set(row) != set(EXPECTED_COLUMNS):
                        raise LoaderError('CSV:shape')
                    if row['symbol'] != symbol:
                        raise LoaderError('ROW:symbol')
                    raw_time = _parse_timestamp(row['create_time'])
                    convention, period_end = _raw_to_period_end(raw_time, archive_day)
                    if count > 288 or period_end != expected_period_ends[count - 1]:
                        raise LoaderError('ROW:sequence')
                    if period_end in seen:
                        raise LoaderError('ROW:duplicate')
                    seen.add(period_end)
                    values = {column: _decimal(row[column], column) for column in NUMERIC_COLUMNS}
                    yield {
                        'format': FORMAT,
                        'symbol': symbol,
                        'archive_day': archive_day.isoformat(),
                        'raw_create_time': row['create_time'],
                        'label_convention': convention,
                        'period_end': _utc_text(period_end),
                        'available_at': _utc_text(period_end + AVAILABILITY_LAG),
                        **values,
                    }
                if count != 288:
                    raise LoaderError('ROW:count')
    except (zipfile.BadZipFile, RuntimeError, UnicodeError, csv.Error, OSError) as exc:
        if isinstance(exc, LoaderError):
            raise
        raise LoaderError(f'ZIP:unreadable:{type(exc).__name__}') from exc


def _selected_entries(manifest, symbols, start: dt.date, end_exclusive: dt.date):
    wanted = set(symbols)
    return sorted(
        (entry for entry in manifest['files']
         if entry['symbol'] in wanted and start <= dt.date.fromisoformat(entry['date']) < end_exclusive),
        key=lambda entry: (entry['symbol'], entry['date']),
    )


def load_rows(manifest, symbols, request_start: str, request_end_exclusive: str, store):
    """Return all normalized rows after a two-pass, all-or-nothing validation.

    Pass one parses every archive and discards rows.  Only after every archive
    succeeds does pass two materialize rows for the caller, preventing a partial
    research result from escaping a malformed later file.
    """
    ok, reasons = ADMIT.admit(manifest, symbols, request_start, request_end_exclusive, store)
    if not ok:
        raise LoaderError('ADMISSION:' + json.dumps(reasons, sort_keys=True, separators=(',', ':')))
    start = dt.date.fromisoformat(request_start)
    end_exclusive = dt.date.fromisoformat(request_end_exclusive)
    entries = _selected_entries(manifest, symbols, start, end_exclusive)
    for entry in entries:
        path = ADMIT.confined_path(store, entry['path'])
        for _ in iter_archive(path, entry['symbol'], dt.date.fromisoformat(entry['date'])):
            pass
    output = []
    for entry in entries:
        path = ADMIT.confined_path(store, entry['path'])
        output.extend(iter_archive(path, entry['symbol'], dt.date.fromisoformat(entry['date'])))
    return output


def _atomic_jsonl(path: Path, rows) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.part')
    try:
        with tmp.open('w', encoding='ascii', newline='\n') as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-research-loader', action='store_true')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--store', required=True)
    parser.add_argument('--symbols', required=True, help='comma-separated exact manifest symbols')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end-exclusive', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    if not args.enable_research_loader:
        parser.error('default OFF: --enable-research-loader is required')
    with open(args.manifest, encoding='utf-8') as fh:
        manifest = json.load(fh)
    symbols = args.symbols.split(',')
    rows = load_rows(manifest, symbols, args.start, args.end_exclusive, args.store)
    _atomic_jsonl(Path(args.out), rows)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
