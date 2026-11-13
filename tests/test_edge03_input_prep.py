"""Synthetic-only tests for EDGE-03 audited input acquisition/preparation."""
import csv
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools' / 'research'))
import prepare_edge03_inputs as P  # noqa: E402
import run_edge03_xsec as R        # noqa: E402


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def synthetic_sources(root):
    candle_dir = root / 'candle_source'; candle_dir.mkdir(parents=True)
    candle_files = {}
    times = range(P.START_MS, P.END_MS, P.TF_MS)
    for j, symbol in enumerate(P.SYMBOLS):
        path = candle_dir / f'{symbol}_4h.csv'
        with path.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f, lineterminator='\n'); w.writerow(['t', 'o', 'h', 'l', 'c', 'v'])
            for t in times:
                value = 100 + j
                w.writerow([float(t), value, value + 2, value - 2, value + 1, 7])
        candle_files[symbol] = {'file': path.name, 'sha256': digest(path)}
    candle_manifest = root / 'candle_source.json'
    candle_manifest.write_text(json.dumps({'files': candle_files}), encoding='utf-8')

    settlements = list(range(P.START_MS, P.END_MS + 1, 8 * 60 * 60 * 1000))
    archive_root = root / 'archives'
    existing = root / 'existing'; existing.mkdir()
    for symbol in P.SYMBOLS:
        zpath = archive_root / symbol / f'{symbol}-fundingRate-2024-10.zip'
        zpath.parent.mkdir(parents=True)
        text = 'calc_time,funding_interval_hours,last_funding_rate\n' + ''.join(
            f'{t},8,0.00010000\n' for t in settlements)
        with zipfile.ZipFile(zpath, 'w') as zf:
            zf.writestr(f'{symbol}-fundingRate-2024-10.csv', text)
        if symbol in P.EXISTING_FUNDING:
            with (existing / f'{symbol}.csv').open('w', newline='', encoding='utf-8') as f:
                w = csv.writer(f, lineterminator='\n')
                w.writerow(['fundingTime', 'fundingRate', 'markPrice', 'time_utc'])
                w.writerows((t, '0.00010000', str(100 + i), 'x') for i, t in enumerate(settlements))

    acq = root / 'acquisition'; acq.mkdir()
    funding, terminal = {}, {}
    for symbol in P.MISSING_FUNDING:
        path = acq / 'raw' / 'funding' / symbol / 'page-001.json'
        path.parent.mkdir(parents=True)
        rows = [{'symbol': symbol, 'fundingTime': t, 'fundingRate': '0.00010000',
                 'markPrice': str(200 + i)} for i, t in enumerate(settlements)]
        path.write_text(json.dumps(rows), encoding='utf-8')
        funding[symbol] = {'pages': [{'path': path.relative_to(acq).as_posix(), 'sha256': digest(path)}]}
    for symbol in P.SYMBOLS:
        path = acq / 'raw' / 'terminal_4h' / f'{symbol}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([[P.END_MS, '123.45', '999', '1', '777']]), encoding='utf-8')
        terminal[symbol] = {'path': path.relative_to(acq).as_posix(), 'sha256': digest(path)}
    acq_manifest = acq / 'acquisition_manifest.json'
    acq_manifest.write_text(json.dumps({'schema': P.ACQUISITION_SCHEMA, 'source': P.BASE_URL,
                                        'funding': funding, 'terminal_4h': terminal}), encoding='utf-8')
    return candle_manifest, candle_dir, existing, archive_root, acq_manifest


def test_prepare_verifies_sources_and_exposes_only_terminal_open(tmp_path):
    candle_source, candle_dir, existing, archives, acquisition = synthetic_sources(tmp_path)
    out = tmp_path / 'prepared'
    candles, funding, audit = P.prepare(acquisition, candle_source, candle_dir, existing, archives, out)
    frames, _ = R.load_candles(candles)
    _, funding_prov = R.load_funding(funding)
    assert len(frames) == 9
    assert funding_prov['calendar']['rows'] == 2142
    assert funding_prov['records']['rows'] == 9 * 2142
    assert all(frame.iloc[-1][['o', 'h', 'l', 'c']].tolist() == [123.45] * 4 for frame in frames.values())
    assert json.loads(audit.read_text())['holdout_read'] is False
    with pytest.raises(P.InputError, match='no-overwrite'):
        P.prepare(acquisition, candle_source, candle_dir, existing, archives, out)


def test_prepare_refuses_tampered_acquisition_before_output(tmp_path):
    candle_source, candle_dir, existing, archives, acquisition = synthetic_sources(tmp_path)
    doc = json.loads(acquisition.read_text())
    target = acquisition.parent / doc['funding'][P.MISSING_FUNDING[0]]['pages'][0]['path']
    target.write_bytes(target.read_bytes() + b' ')
    out = tmp_path / 'must-not-exist'
    with pytest.raises(P.InputError, match='hash mismatch'):
        P.prepare(acquisition, candle_source, candle_dir, existing, archives, out)
    assert not out.exists()


def test_acquire_requests_only_missing_funding_and_all_terminal_opens(tmp_path, monkeypatch):
    calls = []

    def fake_get(path, params, retries=4):
        calls.append((path, dict(params)))
        if path.endswith('fundingRate'):
            body = [{'symbol': params['symbol'], 'fundingTime': P.END_MS,
                     'fundingRate': '0.1', 'markPrice': '10'}]
        else:
            body = [[P.END_MS, '10', '11', '9', '10']]
        return json.dumps(body).encode(), '2026-10-26T00:00:00.000Z'

    monkeypatch.setattr(P, 'get_json', fake_get)
    manifest = P.acquire(tmp_path / 'acquired')
    doc = json.loads(manifest.read_text())
    funding_symbols = [p['symbol'] for path, p in calls if path.endswith('fundingRate')]
    terminal_symbols = [p['symbol'] for path, p in calls if path.endswith('klines')]
    assert funding_symbols == list(P.MISSING_FUNDING)
    assert terminal_symbols == list(P.SYMBOLS)
    assert all(spec['open_time_ms'] == P.END_MS for spec in doc['terminal_4h'].values())
