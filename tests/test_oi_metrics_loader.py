"""Synthetic, no-network tests for the default-OFF causal OI row loader."""
import csv
import datetime as dt
import hashlib
import io
import os
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))

import oi_metrics_loader as L  # noqa: E402


def _rows(day: dt.date, symbol='BTCUSDT', *, new=None, mutate=None):
    new = day >= L.CONVENTION_START_DAY if new is None else new
    start = dt.datetime.combine(day, dt.time()) if new else dt.datetime.combine(day, dt.time()) + L.PERIOD
    rows = []
    for i in range(288):
        row = {
            'create_time': (start + i * L.PERIOD).strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': symbol,
            'sum_open_interest': '100.0',
            'sum_open_interest_value': '200.0',
            'count_toptrader_long_short_ratio': '1.1',
            'sum_toptrader_long_short_ratio': '1.2',
            'count_long_short_ratio': '1.3',
            'sum_taker_long_short_vol_ratio': '1.4',
        }
        rows.append(row)
    if mutate:
        mutate(rows)
    return rows


def _zip(path, day, symbol='BTCUSDT', *, rows=None, member=None, columns=L.EXPECTED_COLUMNS):
    rows = rows or _rows(day, symbol)
    member = member or f'{symbol}-metrics-{day.isoformat()}.csv'
    buf = io.StringIO(newline='')
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, buf.getvalue())


def test_old_convention_normalizes_end_labels(tmp_path):
    day = dt.date(2026, 6, 24)
    path = tmp_path / 'old.zip'
    _zip(path, day)
    rows = list(L.iter_archive(path, 'BTCUSDT', day))
    assert len(rows) == 288
    assert rows[0]['raw_create_time'] == '2026-06-24 00:05:00'
    assert rows[0]['period_end'] == '2026-06-24T00:05:00Z'
    assert rows[-1]['period_end'] == '2026-06-25T00:00:00Z'
    assert rows[0]['available_at'] == '2026-06-24T00:10:00Z'
    assert {row['label_convention'] for row in rows} == {'end_labeled_v1'}


def test_new_convention_shifts_start_labels_by_five_minutes(tmp_path):
    day = dt.date(2026, 6, 25)
    path = tmp_path / 'new.zip'
    _zip(path, day)
    rows = list(L.iter_archive(path, 'BTCUSDT', day))
    assert rows[0]['raw_create_time'] == '2026-06-25 00:00:00'
    assert rows[0]['period_end'] == '2026-06-25T00:05:00Z'
    assert rows[-1]['period_end'] == '2026-06-26T00:00:00Z'
    assert {row['label_convention'] for row in rows} == {'start_labeled_v2'}


@pytest.mark.parametrize('mutate,code', [
    (lambda rows: rows.pop(), 'ROW:count'),
    (lambda rows: rows[20].update(create_time='2026-06-25 01:40:02'), 'ROW:timestamp_grid'),
    (lambda rows: rows[20].update(create_time=rows[19]['create_time']), 'ROW:sequence'),
    (lambda rows: rows[20].update(symbol='ETHUSDT'), 'ROW:symbol'),
    (lambda rows: rows[20].update(sum_open_interest='NaN'), 'ROW:number:sum_open_interest'),
    (lambda rows: rows[20].update(sum_open_interest='-1'), 'ROW:number:sum_open_interest'),
])
def test_row_anomalies_fail_closed(tmp_path, mutate, code):
    day = dt.date(2026, 6, 25)
    path = tmp_path / 'bad.zip'
    _zip(path, day, rows=_rows(day, mutate=mutate))
    with pytest.raises(L.LoaderError, match=code):
        list(L.iter_archive(path, 'BTCUSDT', day))


def test_wrong_member_and_header_fail_closed(tmp_path):
    day = dt.date(2026, 6, 25)
    wrong_member = tmp_path / 'member.zip'
    _zip(wrong_member, day, member='other.csv')
    with pytest.raises(L.LoaderError, match='ZIP:member'):
        list(L.iter_archive(wrong_member, 'BTCUSDT', day))
    wrong_header = tmp_path / 'header.zip'
    columns = tuple(reversed(L.EXPECTED_COLUMNS))
    _zip(wrong_header, day, columns=columns)
    with pytest.raises(L.LoaderError, match='CSV:header'):
        list(L.iter_archive(wrong_header, 'BTCUSDT', day))


def test_archive_bytes_are_hashable_for_manifest_fixture(tmp_path):
    day = dt.date(2026, 6, 25)
    path = tmp_path / 'fixture.zip'
    _zip(path, day)
    raw = path.read_bytes()
    assert len(raw) > 0 and len(hashlib.sha256(raw).hexdigest()) == 64


def test_verified_archive_parses_the_same_handle_that_was_hashed(tmp_path, monkeypatch):
    day = dt.date(2026, 6, 25)
    rel = f'daily/metrics/BTCUSDT/BTCUSDT-metrics-{day}.zip'
    path = tmp_path.joinpath(*rel.split('/'))
    path.parent.mkdir(parents=True)
    _zip(path, day)
    raw = path.read_bytes()
    entry = {
        'path': rel, 'symbol': 'BTCUSDT', 'date': day.isoformat(),
        'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
    }
    seen = []

    def fake_iter(source, symbol, archive_day):
        assert hasattr(source, 'read') and not isinstance(source, (str, os.PathLike))
        seen.append((source.read(), symbol, archive_day))
        return iter(())

    monkeypatch.setattr(L, 'iter_archive', fake_iter)
    assert list(L.iter_verified_archive(tmp_path, entry)) == []
    assert seen == [(raw, 'BTCUSDT', day)]


@pytest.mark.parametrize('change,code', [
    (lambda raw: raw + b'x', 'DISK:size'),
    (lambda raw: raw[:-1] + bytes([raw[-1] ^ 1]), 'DISK:sha256'),
])
def test_verified_archive_refuses_bytes_not_bound_by_manifest(tmp_path, change, code):
    day = dt.date(2026, 6, 25)
    rel = f'daily/metrics/BTCUSDT/BTCUSDT-metrics-{day}.zip'
    path = tmp_path.joinpath(*rel.split('/'))
    path.parent.mkdir(parents=True)
    _zip(path, day)
    raw = path.read_bytes()
    entry = {
        'path': rel, 'symbol': 'BTCUSDT', 'date': day.isoformat(),
        'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
    }
    path.write_bytes(change(raw))
    with pytest.raises(L.LoaderError, match=code):
        list(L.iter_verified_archive(tmp_path, entry))
