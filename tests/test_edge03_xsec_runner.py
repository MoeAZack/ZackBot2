"""Synthetic-only packet tests; no market cache, network, PAPER, or runtime input."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools' / 'research'))
import edge03_xsec as X                                                                    # noqa: E402
import run_edge03_xsec as R                                                                # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def repo(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=path, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'], cwd=path, check=True)
    subprocess.run(['git', 'config', 'user.name', 'test'], cwd=path, check=True)
    (path / 'seed').write_text('synthetic\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'seed'], cwd=path, check=True)
    subprocess.run(['git', 'commit', '-qm', 'seed'], cwd=path, check=True)
    return path


def manifests(root):
    root.mkdir()
    t = np.arange(R.START_MS, R.END_MS + X.TF_MS, X.TF_MS, dtype=np.int64)
    files = {}
    for j, symbol in enumerate(X.CORE_10_ELIGIBLE_9):
        r = (j - 4) * 0.00003 + 0.0002 * np.sin(np.arange(len(t)) * 0.31 + j)
        c = (100 + j) * np.exp(np.cumsum(r))
        o = np.r_[c[0], c[:-1]]
        w = c * 0.003
        path = root / f'{symbol}.csv'
        pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c)).to_csv(path, index=False)
        files[symbol] = {'path': path.name, 'sha256': sha(path)}
    common = {'venue': R.VENUE, 'audited': True, 'window': {'start': R.START, 'end_exclusive': R.END},
              'symbols': list(X.CORE_10_ELIGIBLE_9)}
    candle = root / 'candles.json'
    candle.write_text(json.dumps(dict(common, schema=R.CANDLE_SCHEMA, timeframe='4h', terminal_open=True, files=files)),
                      encoding='utf-8')

    interval = 8 * 60 * 60 * 1000
    settlements = np.arange(R.START_MS + interval, R.END_MS + 1, interval, dtype=np.int64)
    calendar = root / 'funding_calendar.csv'
    pd.DataFrame({'settlement_ms': settlements}).to_csv(calendar, index=False)
    rows = []
    for symbol in X.CORE_10_ELIGIBLE_9:
        for settlement in settlements:
            rows.append((symbol, settlement, settlement, 0.00001, 100.0))
    funding_rows = root / 'funding.csv'
    pd.DataFrame(rows, columns=['symbol', 'settlement_ms', 'available_ms', 'rate', 'mark_price']).to_csv(
        funding_rows, index=False)
    funding = root / 'funding.json'
    funding.write_text(json.dumps(dict(common, schema=R.FUNDING_SCHEMA, point_in_time=True,
                                       calendar={'path': calendar.name, 'sha256': sha(calendar)},
                                       records={'path': funding_rows.name, 'sha256': sha(funding_rows)})), encoding='utf-8')
    return candle, funding, funding_rows


@pytest.fixture(scope='module')
def packet_env(tmp_path_factory):
    root = tmp_path_factory.mktemp('edge03')
    candle, funding, funding_rows = manifests(root / 'inputs')
    git = repo(root / 'repo')
    out = root / 'packet.json'
    packet = R.run(candle, funding, git, out)
    return root, candle, funding, funding_rows, git, out, packet


def test_packet_is_exact_head_hashed_bounded_side_separated_and_write_once(packet_env):
    root, candle, funding, _, git, out, packet = packet_env
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=git, check=True, capture_output=True, text=True).stdout.strip()
    assert packet['git'] == {'head': head, 'clean': True}
    assert packet['inputs']['candles']['manifest']['sha256'] == sha(candle)
    assert packet['inputs']['funding']['manifest']['sha256'] == sha(funding)
    assert packet['window'] == {'start': R.START, 'end_exclusive': R.END, 'holdout_read': False}
    assert set(packet['runs']) == {'long.base', 'long.stress_1_5x', 'short.base', 'short.stress_1_5x'}
    assert all([f['name'] for f in row['folds']] == [f'WF{i}' for i in range(7)] for row in packet['runs'].values())
    assert packet['train']['purpose'].startswith('warm-up') and packet['funding_boundary'] == '(entry_ms, exit_ms]'
    assert all(row['complete'] for row in packet['train']['integrity'].values())
    assert json.loads(out.read_text(encoding='utf-8')) == packet
    with pytest.raises(R.RunnerError, match='write-once'):
        R.run(candle, funding, git, out)


def test_manifest_hash_tamper_missing_and_noncausal_funding_are_refused(packet_env, tmp_path):
    _, candle, funding, funding_rows, git, _, _ = packet_env
    bad_hash = candle.with_name('bad_hash.json')
    doc = json.loads(candle.read_text(encoding='utf-8'))
    doc['files']['BTCUSDT']['sha256'] = '0' * 64
    bad_hash.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(R.RunnerError, match='sha256 mismatch'):
        R.build_packet(bad_hash, funding, git)

    def bad_funding(name, mutate):
        d = tmp_path / name
        d.mkdir()
        rows = pd.read_csv(funding_rows)
        mutate(rows)
        rows_path = d / 'rows.csv'
        rows.to_csv(rows_path, index=False)
        source = json.loads(funding.read_text(encoding='utf-8'))
        original_calendar = Path(funding).parent / source['calendar']['path']
        copied_calendar = d / 'calendar.csv'
        copied_calendar.write_bytes(original_calendar.read_bytes())
        source['calendar'] = {'path': copied_calendar.name, 'sha256': sha(copied_calendar)}
        source['records'] = {'path': rows_path.name, 'sha256': sha(rows_path)}
        manifest = d / 'funding.json'
        manifest.write_text(json.dumps(source), encoding='utf-8')
        return manifest

    missing = bad_funding('missing', lambda rows: rows.drop(index=0, inplace=True))
    with pytest.raises(R.RunnerError, match='missing'):
        R.build_packet(candle, missing, git)
    late = bad_funding('late', lambda rows: rows.__setitem__('available_ms', rows['settlement_ms'] + 1))
    with pytest.raises(R.RunnerError, match='non-causal'):
        R.build_packet(candle, late, git)


def test_dirty_head_and_holdout_or_wrong_window_are_refused_before_packet(packet_env, tmp_path):
    _, candle, funding, _, git, _, _ = packet_env
    (git / 'dirty').write_text('x', encoding='utf-8')
    with pytest.raises(R.RunnerError, match='dirty git tree'):
        R.build_packet(candle, funding, git)
    (git / 'dirty').unlink()

    bad = tmp_path / 'holdout.json'
    doc = json.loads(candle.read_text(encoding='utf-8'))
    doc['window']['end_exclusive'] = '2026-10-01T00:00:00Z'
    bad.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(R.RunnerError, match='frozen development interval'):
        R.build_packet(bad, funding, git)
