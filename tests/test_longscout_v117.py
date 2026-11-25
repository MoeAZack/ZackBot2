"""Synthetic settlement ordering and funding gap controls."""
from pathlib import Path
import hashlib
import json
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v117 as M

PLANTED_MUTANTS = [
    ('settlement_visible_before_publication', "side='left'", "side='right'",
     'test_settlement_is_strictly_prior'),
    ('accept_missing_prior_payment', 'gap <= np.maximum(interval[known], interval[pre]) * HOUR_MS + 60_000',
     'gap >= 0', 'test_gap_blocks_until_two_adjacent_events'),
    ('accept_stale_payment', 'age <= interval[known] * HOUR_MS + 60_000', 'age >= 0',
     'test_gap_blocks_until_two_adjacent_events'),
]


def rates(hour, value, interval=4):
    return pd.DataFrame(dict(calc_time=np.array(hour) * M.HOUR_MS,
                             funding_interval_hours=interval, last_funding_rate=value))


def mask(close, funding):
    return M.negative_funding_mask(np.array(close) * M.HOUR_MS, funding).tolist()


def test_settlement_is_strictly_prior():
    data = rates([0, 4, 8, 12], [-.0003, -.0002, 0, -.0004])
    assert mask([4, 5, 8, 9, 13], data) == [False, True, True, False, False]
    assert mask([4, 5, 8], data.iloc[:2]) == mask([4, 5, 8], data)


def test_gap_blocks_until_two_adjacent_events():
    data = rates([0, 4, 12, 16], [-.0003] * 4)
    assert mask([5, 10, 13, 17, 25], data) == [True, False, False, True, False]


def test_invalid_funding_events_rejected():
    with pytest.raises(ValueError, match='invalid frozen funding rows'):
        mask([10], rates([0, 4, 4], [-.0003] * 3))
    with pytest.raises(ValueError, match='invalid frozen funding rows'):
        mask([10], rates([0, 4, 8], [-.0003, float('nan'), -.0003]))
    same_scheduled_event = rates([0, 4, 4], [-.0003] * 3)
    same_scheduled_event.loc[2, 'calc_time'] += 10
    assert mask([5], same_scheduled_event) == [False]


def test_manifest_and_payment_bytes_are_frozen(tmp_path, monkeypatch):
    csv_file = tmp_path / 'COIN0.csv'
    csv_file.write_text('calc_time,funding_interval_hours,last_funding_rate\n0,4,-0.0003\n', encoding='utf-8')
    identifier = hashlib.sha256(csv_file.read_bytes()).hexdigest()
    symbols = {f'COIN{i}': dict(rows=1, csv_sha256=identifier) for i in range(166)}
    manifest = dict(status='COMPLETE', expected_symbols=166, successes=166,
                    gap_policy='MASK_OUTSIDE_CADENCE', symbols=symbols)
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    monkeypatch.setenv('LONGSCOUT_FUNDING_DATA', str(tmp_path))
    M.funding_manifest.cache_clear()
    monkeypatch.setattr(M, 'DATA_MANIFEST_SHA256', None)
    with pytest.raises(ValueError, match='pinned funding manifest'):
        M.funding_manifest()
    monkeypatch.setattr(M, 'DATA_MANIFEST_SHA256', hashlib.sha256(path.read_bytes()).hexdigest())
    M.set_panel(dict.fromkeys(symbols))
    assert len(M.load_funding('COIN0')) == 1
    with pytest.raises(ValueError, match='universes differ'):
        M.set_panel({'COIN0': None})
    csv_file.write_text('altered', encoding='utf-8')
    with pytest.raises(ValueError, match='CSV hash differs'):
        M.load_funding('COIN0')
    M.funding_manifest.cache_clear()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
