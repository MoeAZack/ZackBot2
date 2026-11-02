import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import oi_divergence_stagea_readiness as R  # noqa: E402


def test_missing_manifest_is_precise_data_block_not_exception(tmp_path):
    doc = R.check(tmp_path / 'missing.json', tmp_path)
    assert doc['status'] == 'DATA_BLOCKED' and doc['code'] == 'MANIFEST_MISSING'
    assert doc['sealed_holdout_opened'] is doc['paper_authority'] is doc['runtime_authority'] is False


def test_missing_store_is_precise_after_manifest_exists(tmp_path):
    manifest = tmp_path / 'manifest.json'; manifest.write_text('{}', encoding='utf-8')
    doc = R.check(manifest, tmp_path / 'missing-store')
    assert doc['status'] == 'DATA_BLOCKED' and doc['code'] == 'STORE_MISSING'


def test_summary_enforces_real_timestamp_availability_and_exact_count():
    day = dt.date(2025, 1, 1)
    rows = []
    for i in range(R.PER_DAY):
        end = dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc) + dt.timedelta(minutes=5 * (i + 1))
        rows.append({'symbol': 'BTCUSDT', 'period_end': end.isoformat().replace('+00:00', 'Z'),
                     'available_at': (end + dt.timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')})
    got = R.summarize_rows(rows, ('BTCUSDT',), '2025-01-01', '2025-01-02')
    assert got['BTCUSDT']['rows'] == 288
    rows[-1]['available_at'] = rows[-1]['period_end']
    with pytest.raises(R.ReadinessError, match='availability_rule'):
        R.summarize_rows(rows, ('BTCUSDT',), '2025-01-01', '2025-01-02')


def test_record_is_write_once(tmp_path):
    p = tmp_path / 'result.json'; R._write_new(p, {'status': 'DATA_BLOCKED'})
    with pytest.raises(R.ReadinessError, match='never overwritten'):
        R._write_new(p, {'status': 'READY'})
